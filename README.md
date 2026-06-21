# Local Chat with Ollama (Flask)

A ChatGPT-style web interface for chatting with local models via [Ollama](https://ollama.com). Multi-user support, isolated sessions, and model selection directly from the interface.

## Requirements

* Python 3.10+
* [Ollama](https://ollama.com) installed and running (`ollama serve`)
* At least one model downloaded, for example: `ollama pull llama3.2:3b`

## Installation

```bash
git clone https://github.com/arnaudchalandat-sketch/LocalAI---web-interface.git
cd LocalAI---web-interface

python3 -m venv .venv
source .venv/bin/activate        # Linux/Mac
# .venv\Scripts\activate         # Windows

pip install -r requirements.txt
```

## Starting the Server

```bash
export FLASK_SECRET_KEY="replace-with-a-long-random-value"
python server.py
```

If `FLASK_SECRET_KEY` is not set, the server will still start using a randomly generated key, but all sessions will be invalidated every time the server restarts. To generate a proper key:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

The server listens on `0.0.0.0:5000`, making it accessible from any device on the same local network at:

`http://<server-ip>:5000`

## Multi-User Behavior

Each visitor receives a unique session cookie. Their conversations and selected model are completely isolated from other users.

## Multiple Conversations and Persistence

Each user can create multiple conversations (using the `+` button), access them from the sidebar (automatically titled from the first message), switch between them, and delete them.

Conversations are stored on disk in the `data/` directory, with one JSON file per user (identified by their session cookie). Server restarts no longer cause chat history to be lost.

⚠️ The `data/` directory contains user conversations. It is excluded from the Git repository (`.gitignore`) and should never be published.

## Model Selection

The dropdown menu at the top of the interface displays the models available on the local Ollama installation (`ollama list`). The selected model is specific to each user and remains active for the duration of their session.

## Known Limitations

* **Single process only**: suitable for development using `python server.py`. Conversation caching is kept in memory (and reloaded from `data/` when needed). Deployments with multiple workers (Gunicorn, etc.) would require shared storage (a database) instead of local JSON files.
* **Hardware-dependent performance**: on systems without a dedicated GPU (Raspberry Pi, entry-level laptops), lightweight models such as `llama3.2:3b`, `llama3.2:1b`, or `phi3:mini` are recommended instead of 7B/8B models, which will remain slow when running on CPU only.

## Environment Variables

| Variable           | Purpose                                      | Default  |
| ------------------ | -------------------------------------------- | -------- |
| `OLLAMA_MODEL`     | Default model to use if available            | `llama3` |
| `FLASK_SECRET_KEY` | Session signing key (should be set)          | random   |
| `FLASK_DEBUG`      | Set to `1` to enable debug mode (local only) | `0`      |

⚠️ Never enable `FLASK_DEBUG=1` when the server is exposed to a network. The Werkzeug debugger allows arbitrary code execution directly from the browser.

## Project Structure

```text
.
├── LICENSE
├── README.md
├── requirements.txt
├── server.py
├── static/
│   └── styles.css
└── templates/
    └── index.html
```

## License

MIT — see [LICENSE](LICENSE).
