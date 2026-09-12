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

### From your phone

Set `JARVIS_HOST=0.0.0.0` and restart. Jarvis prints its LAN address and a QR
code; scan it and the phone pairs itself. The token travels in the URL
*fragment*, which browsers never send to a server, and the client strips it from
the address bar once stored. On the phone, "Add to Home Screen" installs it as a
standalone app.

Jarvis refuses to bind to a non-loopback address unless `JARVIS_AUTH_TOKEN` is at
least 24 characters.

## Configuration

| Variable | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Gemini API key ([AI Studio](https://aistudio.google.com/apikey)) |
| `GEMINI_MODEL` | Model id, default `gemini-3.5-flash-lite` |
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

**Policy layer.** `jarvis/security.py` decides whether a selected tool may run
*now*. Configuration can only tighten the defaults, with one deliberate
exception (`JARVIS_AUTO_APPROVE_TOOLS`, for unattended setups):

* `JARVIS_BLOCKED_TOOLS` — disable named tools outright
* `JARVIS_AUTO_APPROVE_TOOLS` — pre-approve named CONFIRM_REQUIRED tools
* `JARVIS_READ_ONLY_MODE=true` — refuse everything that is not READ_ONLY

**Confirmation flow.** When the model selects a CONFIRM_REQUIRED tool, Jarvis
does *not* run it. It stores the exact call, returns a single-use confirmation
id, and the UI shows an Approve/Cancel card. `POST /api/confirm` then executes
**that stored call** — not a fresh one from the model — so what runs is exactly
what the user saw. Ids are single-use: a replay returns 409.

**Remote exposure.** Binding beyond loopback requires a 24+ character token, or
the server refuses to start. Failed authentications are rate limited per client
(8 failures in 5 minutes locks that address out for 5 minutes, valid token
included). Every response carries `nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: no-referrer` and a same-origin CSP; `/api` responses are
`no-store`. Jarvis speaks plain HTTP, so treat it as a *trusted-LAN* service --
put it behind a tunnel before exposing it to the internet.

**Audit.** Every tool call is written to SQLite with its permission level, policy
decision, arguments, duration and error, and to a rotating log file in
`JARVIS_DATA_DIR`.

**Terminal.** `run_safe_command` is an allowlist of read-only programs with a
per-program argument pattern, not a shell. `shutdown`, `del`, `taskkill`,
`powershell`, `git push`, `docker rm`, `kubectl delete`, command chaining,
redirection and substitution are all rejected.

## Tools

| Tool | Level | What it does |
| --- | --- | --- |
| `system_health` | READ_ONLY | CPU, RAM, disks, battery, uptime in one call |
| `cpu_info` / `memory_info` / `disk_usage` / `battery_status` / `network_info` | READ_ONLY | Individual metrics |
| `list_processes` | READ_ONLY | Top processes by CPU or memory |
| `list_open_windows` | READ_ONLY | Apps with visible windows — "what do I have open?" |
| `search_files` | READ_ONLY | Substring or glob search inside the sandbox |
| `list_directory` | READ_ONLY | Folder contents with sizes |
| `read_text_file` | READ_ONLY | Text/source files; refuses binaries and credential files |
| `largest_files` | READ_ONLY | What is eating disk space |
| `open_path` | LOW_RISK | Open a file or folder in Explorer |
| `open_application` | LOW_RISK | Launch an app by name (alias table, App Paths registry, Start Menu) |
| `close_application` | CONFIRM_REQUIRED | Terminate matching processes; critical Windows processes refused |
| `git_status` / `git_log` / `git_branches` / `git_diff_stat` | READ_ONLY | Branch, ahead/behind, dirty files, history, diff stats |
| `docker_status` / `docker_containers` / `docker_logs` / `docker_images` | READ_ONLY | Daemon state, containers, logs, images |
| `k8s_status` / `k8s_pods` / `k8s_logs` | READ_ONLY | Context, node readiness, pod health, pod logs |
| `run_tests` | CONFIRM_REQUIRED | pytest or npm test, auto-detected; runs project code |
| `run_safe_command` | LOW_RISK | One read-only diagnostic command from a fixed allowlist |

### Filesystem sandbox

Every path argument is resolved through `jarvis/tools/paths.py`. Anything outside
`JARVIS_ALLOWED_ROOTS` (default: your home directory) is refused before the tool
runs, `..` escapes included. Credential files (`.env`, `*.pem`, SSH keys) are
never read back.

### External commands

`jarvis/tools/shell.py` is the only place a process is spawned. It takes argv
lists (never `shell=True`), refuses anything off a small executable allowlist
(`git`, `docker`, `kubectl`, `python`, `npm`, `npx`), time-boxes every run and
truncates output. Container, pod and namespace names are regex-validated so a
model-supplied string cannot become an extra flag. Missing CLIs are reported as
"not installed", not as a crash.

### Application launching

`open_application` takes a *name*, never a command line: it resolves through an
alias table, `shutil.which`, the Windows `App Paths` registry and Start Menu
shortcuts, then execs an argv list with no shell. Nothing machine-specific is
hard-coded.

## API

| Endpoint | Description |
| --- | --- |
| `GET /ping` | Unauthenticated liveness check |
| `GET /manifest.webmanifest` | PWA manifest for home-screen install |
| `GET /api/health` | Version, provider, tool count |
| `GET /api/system` | Live CPU/RAM/disk/battery snapshot |
| `GET /api/tools` | Tool catalogue with permission levels |
| `POST /api/chat` | `{message, conversation_id?, approved_tools?}` |
| `GET /api/conversations` | Recent conversations |
| `GET /api/conversations/{id}` | Transcript |
| `DELETE /api/conversations/{id}` | Delete a conversation |
| `POST /api/confirm` | `{confirmation_id, approve}` — run or cancel a pending action |
| `GET /api/confirmations` | Actions awaiting approval |
| `GET /api/activity` | Recent tool-call audit entries |

## Tests

```powershell
.venv\Scripts\python -m pytest
```

## Roadmap

1. ✅ Setup, Gemini, agent loop, system monitoring
2. ✅ Application / file control
3. ✅ Git, Docker, Kubernetes tools
4. Confirmation flow, richer policy, logging
5. ✅ Mobile interface, QR pairing, remote hardening
6. Voice and long-term memory
