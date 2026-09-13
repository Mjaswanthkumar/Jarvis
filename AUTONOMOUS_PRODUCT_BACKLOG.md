# Autonomous Product Backlog

Living roadmap maintained by the autonomous product-engineering loop.
Read this before choosing work; update it after every meaningful change.

*Iteration 2 · 2026-09-13 · 28 tools · 273 tests · 35 evals*

---

## 1. Product vision

**What it is.** A personal PC agent. You ask in natural language; an LLM chooses
from typed Python tools; a deterministic permission layer decides whether the
action may run; Python does the work on Windows.

**Who it is for.** Developers and power users who want to understand and control
their machine without remembering commands — and who want to do it from their
phone while the PC is in another room.

**The core problem.** Knowing what your PC is doing requires a dozen different
tools (Task Manager, Explorer, terminal, Docker Desktop, `kubectl`) and knowing
which one to open. Controlling it remotely requires none of them to be open.

**What makes it valuable.** Not the chat — the *gate*. Anything can wrap an LLM
around `subprocess`. The value is that a model can propose an action and still
not be able to perform it: authorisation is deterministic Python that never asks
the model's opinion.

**What it could become.** A PC that tells *you* things. Today it answers when
asked. The largest untapped value is the inversion: proactive monitoring, a
machine that says "your disk will be full in three days" before you wonder.

---

## 2. Current product assessment

### Strengths
- Permission model is real and enforced at one chokepoint, not scattered
- Confirmation binds to the frozen call, so approval cannot be bait-and-switched
- Provider abstraction proven (audio transcription touched two files)
- 247 tests + 34 evals, cassette-replayed in CI with no API key
- Injection defence measured before and after, not asserted
- Voice works in every browser (native Web Speech, else server transcription)

### Weaknesses / UX problems
- Metrics are instantaneous. "Is my CPU spiking?" needs a trend, not a number.
- No way to stop a running turn once it starts.
- `/confirmations` is still unused by the UI: a pending approval is lost if the
  page reloads before it is answered.
- The reply renderer handles bold, code and bullets, but the model often emits
  markdown tables, which render as raw pipes.
- Errors from a failed turn are plain red text with no retry affordance.

### Technical limitations
- Windows-only by construction (`winreg`, `EnumWindows`, App Paths)
- History truncated at 40 messages with no summarisation
- One shared token; no per-device credentials or revocation
- In-memory throttle resets on restart
- Plain HTTP by default; TLS optional
- Gemini free tier 15 req/min — a voice turn costs 3+

### Product risks
- Single provider: a Gemini outage is a total outage, and the free tier is
  15 req/min while a voice turn costs 3+
- No per-turn latency or token accounting, so cost and slowness are invisible
- Schema migrations are additive-only; a structural change would need a
  rebuild-and-copy path that does not exist yet

### Opportunities
- Proactive monitoring would change the product category
- Tool previews are now persisted — a "what did Jarvis do today?" digest is
  nearly free from data already stored
- Metric history would make the vitals row genuinely diagnostic

---

## 3. Improvement backlog

| # | Improvement | Problem / opportunity | Value | Impact | Effort | Conf. | Priority | Status |
|---|---|---|---|---|---|---|---|---|
| 1 | **Taint tracking** | Injection can reach LOW_RISK actions with no confirmation | Closes the one security gap named in the docs | High | Med | High | 🔥 Critical | ✅ Done |
| 2 | **Conversation management** | Cannot start a new chat; one thread forever, silently truncated | Removes a functional hole users hit daily | High | Low | High | 🔥 Critical | ✅ Done |
| 3 | **Capability discovery** | Nothing tells a new user what to ask; `/tools` unused | First-run experience; converts 28 hidden tools into visible value | High | Low | High | 🚀 Next | ✅ Done |
| 4 | **Tool call transparency** | Activity panel hid args and results | Makes the agent explainable; debugging and trust | Med | Low | High | 🚀 Next | ✅ Done |
| 5 | **Per-turn traces** | Cannot explain a slow turn | LLM vs tool latency, tokens, cost | Med | Med | High | 🚀 Next | Open |
| 15 | **Markdown tables in replies** | The model emits tables; the renderer shows raw pipes | Answers with several values are unreadable | Med | Low | High | 🎨 UX | Open |
| 16 | **Restore pending confirmations on load** | A reload loses an unanswered approval card | The action stays pending server-side but is invisible | Med | Low | High | 🛡 Reliability | Open |
| 17 | **Retry a failed turn** | A 429 or network blip leaves red text and a dead end | One click instead of retyping | Med | Low | High | 🎨 UX | Open |
| 6 | **Proactive triggers** | Agent is purely reactive | "Tell me when disk drops below 10%" — changes the category | High | High | Med | 💡 Opportunity | Open |
| 7 | **Metric history / sparklines** | Instantaneous numbers hide trends | "Is my CPU spiking?" answerable at a glance | Med | Med | Med | 🎨 UX | Open |
| 8 | **Cross-session memory** | Nothing persists between conversations | "My main project is X" remembered | Med | Med | Med | 💡 Opportunity | Open |
| 9 | **Second provider (Ollama)** | Gemini outage = total outage; proves the abstraction | Offline demo, no quota | Med | Low | High | 🚀 Next | Open |
| 10 | **Streaming (SSE)** | Replies land all at once after seconds | Perceived latency; forces a stop-mid-turn answer | Med | Med | Med | ⚡ Perf | Open |
| 11 | **History summarisation** | 40-message window drops context silently | Long conversations stay coherent | Med | Med | Med | 🧹 Debt | Open |
| 12 | **UWP / Store app launching** | `open_application` misses Store apps | "any app" becomes literally true | Low | Low | High | 🎨 UX | Open |
| 13 | **Stop button** | A 6-iteration turn cannot be cancelled | Control during a runaway turn | Low | Med | Med | 🎨 UX | Open |
| 14 | **Automated red-teaming** | 7 hand-written injection cases | Generated variants find what I did not imagine | Med | Med | Low | 🔬 Experiment | Open |

---

## 4. Product hypotheses

| Hypothesis | Reasoning | Status |
|---|---|---|
| Users will hit the 40-message truncation without noticing quality degrade | One pinned conversation id + no new-chat means every topic accretes | **Validated** — confirmed in code; drove #2 |
| Most first-time users will not discover more than ~4 of 28 capabilities | Only one hint line exists; `/tools` is unused by the UI | **Validated by inspection** — drove #3 |
| Taint-based escalation will rarely fire in normal use | Reading a file *and* acting on the machine in one turn is uncommon | **Validated** — 34/34 evals still pass with strict taint on |
| Proactive alerts are the single largest value unlock | The product answers but never initiates; monitoring is why people open Task Manager | Untested — needs #6 |
| Showing tool arguments increases trust rather than noise | Users who can see `close_application(name="spotify")` will approve faster | **Shipped** — details are collapsed by default, so the cost to a user who does not care is one extra line |
| Additive-only migrations are sufficient for this product | Every schema change so far has been a new column | Holding — a structural change would need a rebuild path |

---

## 5. Completed improvements

| Date | Improvement | Why it was valuable | Commit |
|---|---|---|---|
| 2026-09-13 | **Taint tracking** — untrusted content read in a turn escalates later LOW_RISK actions to CONFIRM_REQUIRED | Closed the one security gap the docs named. Authorisation now depends on *data flow*, not the model's judgement. | `5f7ff84` |
| 2026-09-13 | **Conversation management** — new chat, switcher, delete, auto-titles | Removed a functional hole: users could not start a fresh conversation, so every topic accreted into one silently-truncated thread. The dogfood database proved it: one thread titled "hi" with 24 messages. | `951e0ef` |
| 2026-09-13 | **Capability discovery** — browsable tool catalogue + starter prompts | 28 tools were invisible; a first-time user saw one hint line. Converts built capability into perceived value. | `951e0ef` |
| 2026-09-13 | **Tool call transparency** — clickable chips revealing arguments, returned data, permission and decision | "Where did that number come from?" was unanswerable from the UI. An answer can now be checked against its source. | `bb41155` |
| 2026-09-13 | **Schema migrations** — ALTER TABLE for columns added after the first release | Every column added since Phase 1 was missing on existing databases; the real install failed with "no such column". Fresh test databases hid it entirely. | `bb41155` |

---

## 6. Rejected ideas

| Idea | Why rejected |
|---|---|
| LangChain / LlamaIndex | The agent loop is ~60 lines and is the interesting part of the project; a framework hides it and adds opaque failure modes |
| Vector DB for memory | Unjustifiable at this scale; SQLite FTS5 is the right first step |
| Multi-agent orchestration | Complexity with no problem to solve here |
| React | One screen, four state variables; a build step costs more than it returns |
| Docker packaging | A PC agent needs host access to processes, windows and the filesystem — containerising means breaking out of the container |
| Regex detector as a security gate | Trivially evaded; kept deliberately as a *signal* feeding taint and UI warnings, never as an authorisation decision |

---

## 7. Iteration log

**Iteration 1** — closed the named security gap (taint tracking), then the
largest functional hole (no new conversation) and the largest discoverability
gap (28 invisible tools). Each was chosen because it was the most obvious
weakness remaining, not because it was the most interesting to build.

**Iteration 2** — made tool calls inspectable, which immediately exposed a
latent reliability bug: schema changes had never been applied to existing
databases. Fixing what you can see tends to reveal what you could not.

*Next evaluation:* the UI is now capable but the reply rendering is the weakest
visible surface — markdown tables, which the model emits constantly for
multi-value answers, display as raw pipe characters.
