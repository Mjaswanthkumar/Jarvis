# Jarvis

An AI-powered personal PC agent. Ask questions in natural language; Jarvis answers
using deterministic Python tools that read your machine's real state. The LLM
(Gemini) only ever *selects* a tool — it never executes anything itself.

```text
Mobile/Desktop UI -> FastAPI -> Jarvis Agent -> Gemini -> tool selection
                                     |
                          permission/safety layer -> Python PC tools -> Windows
```

## Quick start

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
copy .env.example .env     # then set GEMINI_API_KEY and JARVIS_AUTH_TOKEN
.venv\Scripts\python -m jarvis
```

Open <http://127.0.0.1:8010> and paste your `JARVIS_AUTH_TOKEN` to connect.

## Configuration

| Variable | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Gemini API key ([AI Studio](https://aistudio.google.com/apikey)) |
| `GEMINI_MODEL` | Model id, default `gemini-2.0-flash` |
| `JARVIS_AUTH_TOKEN` | Shared secret required by every API call — the server refuses to start without it |
| `JARVIS_HOST` / `JARVIS_PORT` | Bind address, default `127.0.0.1:8010` |
| `JARVIS_ALLOWED_ROOTS` | Comma-separated filesystem roots Jarvis may inspect (default: your home dir) |
| `JARVIS_DATA_DIR` | Where `jarvis.db` lives (default `~/.jarvis`) |

## Security model

Every tool declares a permission level:

| Level | Meaning |
| --- | --- |
| `READ_ONLY` | Observes the machine, changes nothing |
| `LOW_RISK` | Small, reversible changes |
| `CONFIRM_REQUIRED` | Runs only after explicit user approval for that turn |
| `BLOCKED` | Never advertised to the model, never executed |

The API has no anonymous mode: a missing `JARVIS_AUTH_TOKEN` fails closed.
Every tool call is written to a SQLite audit log.

## Tools (phase 1)

`system_health`, `cpu_info`, `memory_info`, `disk_usage`, `battery_status`,
`network_info`, `list_processes` — all `READ_ONLY`.

## API

| Endpoint | Description |
| --- | --- |
| `GET /ping` | Unauthenticated liveness check |
| `GET /api/health` | Version, provider, tool count |
| `GET /api/system` | Live CPU/RAM/disk/battery snapshot |
| `GET /api/tools` | Tool catalogue with permission levels |
| `POST /api/chat` | `{message, conversation_id?, approved_tools?}` |
| `GET /api/conversations` | Recent conversations |
| `GET /api/conversations/{id}` | Transcript |
| `DELETE /api/conversations/{id}` | Delete a conversation |
| `GET /api/activity` | Recent tool-call audit entries |

## Tests

```powershell
.venv\Scripts\python -m pytest
```

## Roadmap

1. ✅ Setup, Gemini, agent loop, system monitoring
2. Application / file control
3. Git, Docker, Kubernetes tools
4. Confirmation flow, richer policy, logging
5. Remote access hardening for mobile
6. Voice and long-term memory
