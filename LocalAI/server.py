import os
import json
import time
import uuid
import logging
from pathlib import Path

import markdown
import bleach
from dotenv import load_dotenv
from flask import Flask, request, render_template, session
from ollama import chat, list as ollama_list, ResponseError

load_dotenv()

DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "llama3")
MAX_MESSAGE_LENGTH = 4000
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))

ALLOWED_TAGS = [
    "p", "br", "strong", "em", "ul", "ol", "li",
    "code", "pre", "blockquote", "h1", "h2", "h3", "h4",
    "a", "table", "thead", "tbody", "tr", "th", "td", "hr", "span",
]
ALLOWED_ATTRS = {"a": ["href", "title", "rel"], "code": ["class"], "span": ["class"]}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

_secret_key = os.environ.get("FLASK_SECRET_KEY")
if not _secret_key:
    logger.warning(
        "FLASK_SECRET_KEY non définie : une clé aléatoire est générée pour "
        "cette session. Les sessions seront invalidées à chaque redémarrage."
    )
    _secret_key = os.urandom(24).hex()
app.secret_key = _secret_key

DATA_DIR.mkdir(parents=True, exist_ok=True)

_chats_in_progress: set[str] = set()


def render_markdown(text: str) -> str:
    html = markdown.markdown(text, extensions=["fenced_code", "tables"])
    return bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)


_store_cache: dict[str, dict] = {}


def get_session_id() -> str:
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())
    return session["session_id"]


def _store_path(sid: str) -> Path:
    return DATA_DIR / f"{sid}.json"


def load_store(sid: str) -> dict:
    if sid in _store_cache:
        return _store_cache[sid]

    path = _store_path(sid)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                store = json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.exception("Unreadable session file : %s", path)
            store = {"chats": {}, "chat_order": []}
    else:
        store = {"chats": {}, "chat_order": []}

    _store_cache[sid] = store
    return store


def save_store(sid: str) -> None:
    store = _store_cache.get(sid)
    if store is None:
        return
    path = _store_path(sid)
    tmp_path = path.with_suffix(".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
    except OSError:
        logger.exception("Unable to write session file : %s", path)


def create_chat(sid: str) -> str:
    store = load_store(sid)
    chat_id = str(uuid.uuid4())
    store["chats"][chat_id] = {
        "title": None,
        "messages": [],
        "metriques": [],
    }
    store["chat_order"].insert(0, chat_id)
    save_store(sid)
    return chat_id


def get_current_chat_id(sid: str) -> str:
    store = load_store(sid)
    chat_id = session.get("chat_id")

    if chat_id and chat_id in store["chats"]:
        return chat_id

    if store["chat_order"]:
        chat_id = store["chat_order"][0]
        session["chat_id"] = chat_id
        return chat_id

    chat_id = create_chat(sid)
    session["chat_id"] = chat_id
    return chat_id


def get_chat(sid: str, chat_id: str) -> dict:
    return load_store(sid)["chats"][chat_id]


def set_chat_title_if_needed(sid: str, chat_id: str, first_message: str) -> None:
    chat_obj = get_chat(sid, chat_id)
    if chat_obj["title"] is None:
        chat_obj["title"] = first_message[:30]


def delete_chat(sid: str, chat_id: str) -> None:
    store = load_store(sid)
    if chat_id in store["chats"]:
        del store["chats"][chat_id]
    if chat_id in store["chat_order"]:
        store["chat_order"].remove(chat_id)
    save_store(sid)

    if session.get("chat_id") == chat_id:
        session.pop("chat_id", None)


def get_available_models() -> list[str]:
    try:
        resp = ollama_list()
        return [m.model for m in resp.models]
    except Exception:
        logger.exception("Unable to list Ollama models")
        return []


def get_current_model() -> str:
    available = get_available_models()

    if "model" in session and session["model"] in available:
        return session["model"]

    if DEFAULT_MODEL in available:
        session["model"] = DEFAULT_MODEL
        return DEFAULT_MODEL

    if available:
        session["model"] = available[0]
        return available[0]

    return DEFAULT_MODEL


def render_chat(sid: str):
    store = load_store(sid)
    chat_id = get_current_chat_id(sid)
    chat_obj = get_chat(sid, chat_id)

    recents = [
        {
            "id": cid,
            "title": store["chats"][cid]["title"] or "New chat",
        }
        for cid in store["chat_order"]
        if cid in store["chats"]
    ]

    historique_affichage = [
        {
            "role": m["role"],
            "content": (
                render_markdown(m["content"])
                if m["role"] == "assistant"
                else m["content"]
            ),
        }
        for m in chat_obj["messages"]
    ]

    return render_template(
        "index.html",
        historique=historique_affichage,
        metriques=chat_obj["metriques"],
        modeles=get_available_models(),
        modele_actuel=get_current_model(),
        recents=recents,
        chat_actuel=chat_id,
    )


@app.route("/", methods=["GET"])
def welcome():
    sid = get_session_id()
    return render_chat(sid)


@app.route("/", methods=["POST"])
def send_message():
    sid = get_session_id()
    chat_id = get_current_chat_id(sid)

    if chat_id in _chats_in_progress:
        return render_chat(sid)

    _chats_in_progress.add(chat_id)
    try:
        chat_obj = get_chat(sid, chat_id)

        usr_prompt = request.form.get("message", "").strip()
        current_model = get_current_model()

        if not usr_prompt:
            return render_chat(sid)

        if len(usr_prompt) > MAX_MESSAGE_LENGTH:
            usr_prompt = usr_prompt[:MAX_MESSAGE_LENGTH]

        user_msg = {"role": "user", "content": usr_prompt}
        history_for_model = chat_obj["messages"] + [user_msg]

        try:
            debut = time.time()
            resp = chat(current_model, messages=history_for_model)
            fin = time.time()

            meta = {
                "total_duration": round(resp.total_duration / 1e9, 2),
                "load_duration": round(resp.load_duration / 1e9, 2),
                "prompt_tokens": resp.prompt_eval_count,
                "response_tokens": resp.eval_count,
                "response_time": round(fin - debut, 2),
            }

            set_chat_title_if_needed(sid, chat_id, usr_prompt)
            chat_obj["messages"].append(user_msg)
            chat_obj["messages"].append(
                {"role": "assistant", "content": resp.message.content}
            )
            chat_obj["metriques"].append(meta)

        except ResponseError as e:
            logger.error("Erreur Ollama : %s", e)
            set_chat_title_if_needed(sid, chat_id, usr_prompt)
            chat_obj["messages"].append(user_msg)
            chat_obj["messages"].append(
                {
                    "role": "assistant",
                    "content": (
                        f"Erreur du modèle « {current_model} » : {e}. "
                        "Vérifiez qu'il est bien installé (ollama pull "
                        f"{current_model})."
                    ),
                }
            )
            chat_obj["metriques"].append({})

        except ConnectionError:
            logger.error("Impossible de joindre Ollama.")
            set_chat_title_if_needed(sid, chat_id, usr_prompt)
            chat_obj["messages"].append(user_msg)
            chat_obj["messages"].append(
                {
                    "role": "assistant",
                    "content": (
                        "Impossible de contacter Ollama. "
                        "Vérifiez qu'il est lancé (ollama serve)."
                    ),
                }
            )
            chat_obj["metriques"].append({})

        except Exception as e:
            logger.exception("Erreur inattendue lors de l'appel au modèle")
            set_chat_title_if_needed(sid, chat_id, usr_prompt)
            chat_obj["messages"].append(user_msg)
            chat_obj["messages"].append(
                {
                    "role": "assistant",
                    "content": f"Une erreur inattendue est survenue : {e}",
                }
            )
            chat_obj["metriques"].append({})

        save_store(sid)
        return render_chat(sid)

    finally:
        _chats_in_progress.discard(chat_id)


@app.route("/model", methods=["POST"])
def switch_model():
    sid = get_session_id()
    chosen = request.form.get("model", "")
    available = get_available_models()
    if chosen in available:
        session["model"] = chosen
    return render_chat(sid)


@app.route("/new-chat", methods=["POST"])
def new_chat():
    sid = get_session_id()
    chat_id = create_chat(sid)
    session["chat_id"] = chat_id
    return render_chat(sid)


@app.route("/switch-chat", methods=["POST"])
def switch_chat():
    sid = get_session_id()
    store = load_store(sid)
    chat_id = request.form.get("chat_id", "")
    if chat_id in store["chats"]:
        session["chat_id"] = chat_id
    return render_chat(sid)


@app.route("/delete-chat", methods=["POST"])
def remove_chat():
    sid = get_session_id()
    chat_id = request.form.get("chat_id", "")
    delete_chat(sid, chat_id)
    return render_chat(sid)


if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug_mode, host="0.0.0.0", port=5000, use_reloader=False)