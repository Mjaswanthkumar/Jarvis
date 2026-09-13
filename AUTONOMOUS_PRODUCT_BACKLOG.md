# Autonomous Product Backlog

Living roadmap maintained by the autonomous product-engineering loop.
Read this before choosing work; update it after every meaningful change.

*Iteration 6 · 2026-09-13 · 32 tools · 330 python + 22 js tests · 38 evals*

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

**What it could become.** A PC that tells *you* things. The inversion has
started: watches now report a threshold crossing without being asked. The next
step is a machine that *predicts* — "at this rate your disk is full in three
days" — rather than only reacting to a line being crossed.

---

## 2. Current product assessment

### Strengths
- Permission model is real and enforced at one chokepoint, not scattered
- Confirmation binds to the frozen call, so approval cannot be bait-and-switched
- Provider abstraction proven (audio transcription touched two files)
- 324 python + 22 js tests, 38 evals, cassette-replayed in CI with no API key
- Proactive: watches fire from the sampler without a conversation open
- Every tool call is inspectable: arguments, returned data, permission, decision
- Vitals are recorded over time, so trends are answerable by both UI and agent
- Injection defence measured before and after, not asserted
- Voice works in every browser (native Web Speech, else server transcription)

### Weaknesses / UX problems
- No way to stop a running turn once it starts; a 6-iteration turn runs to
  completion whatever the user does.
- Replies land all at once after several seconds. No streaming, so a slow turn
  looks identical to a hung one.
- Nothing persists across conversations; a new thread knows nothing about the
  user's machine or projects.
- Alerts reach the PC via a native toast, but a phone across the house still
  gets nothing until the page is opened.
- Watches are threshold-only. "Tell me when my disk is nearly full" is better
  served by a projection than a fixed line.

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
| 5 | **Per-turn traces** | Tool durations are shown; LLM latency and tokens are not | Explains where a slow turn went | Med | Med | High | 🚀 Next | Open |
| 15 | **Markdown tables in replies** | The model emits tables; the renderer showed raw pipes | Answers with several values are unreadable | Med | Low | High | 🎨 UX | ✅ Done |
| 16 | **Restore pending confirmations on load** | A reload lost an unanswered approval card | The action stayed pending server-side but was invisible | Med | Low | High | 🛡 Reliability | ✅ Done |
| 17 | **Retry a failed turn** | A 429 or network blip left red text and a dead end | One click instead of retyping | Med | Low | High | 🎨 UX | ✅ Done |
| 18 | **Streaming (merge of #10)** | A slow turn looks identical to a hung one | Perceived latency, and forces a real stop-mid-turn answer | Med | Med | Med | ⚡ Perf | Open |
| 6 | **Proactive triggers** | Agent was purely reactive | "Tell me when disk drops below 10%" — changes the category | High | Med | Med | 🔥 Now | ✅ Done |
| 19 | **Alerts reach a closed page** | An alert only landed if the page was open | A watch that fires overnight should reach the user | High | Low | High | 🚀 Next | ✅ Done (native toast, not web push) |
| 21 | **Alerts to the phone** | Native toast covers the PC; a phone across the house gets nothing | Completes the remote story | Med | High | Low | 💡 Opportunity | Open — needs HTTPS + push service; revisit only if remote use proves common |
| 20 | **Trend projection** | Watches are fixed thresholds | "At this rate the disk is full in 3 days" is more useful than a line | Med | Med | Med | 💡 Opportunity | Open |
| 7 | **Metric history / sparklines** | Instantaneous numbers hid trends | "Is my CPU spiking?" answerable at a glance, and by the agent | Med | Med | Med | 🎨 UX | ✅ Done |
| 8 | **Cross-session memory** | Nothing persists between conversations | "My main project is X" remembered | Med | Med | Med | 💡 Opportunity | Open |
| 9 | **Second provider (Ollama)** | Gemini outage = total outage; proves the abstraction | Offline demo, no quota | Med | Low | High | ❌ Deferred | Owner chose "Gemini only" when asked; revisit only if they raise it |
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
| Proactive alerts are the single largest value unlock | The product answers but never initiates; monitoring is why people open Task Manager | **Shipped and working**, now reaching the desktop with nothing open |
| Running *on* the machine makes the conventional answer wrong | Web push exists to reach a device you do not control; Jarvis controls this one | **Validated** — a native toast replaced HTTPS + service worker + VAPID + 3 dependencies with zero |
| Threshold watches are enough; users do not need complex rules | "Below 10%" covers most of what people actually want to know | Holding — no evidence yet that anyone wants compound conditions |
| Recorded history makes the agent qualitatively more useful, not just the UI | A trend question was previously unanswerable at any price | **Validated** — "has my CPU been busy?" now selects `metric_history` and answers from data |
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
| 2026-09-13 | **Reply rendering** — real markdown (tables, lists, code), with XSS tests in CI | The system prompt asks for tables, so most multi-value answers were displayed as raw pipe characters | `b6336f4` |
| 2026-09-13 | **Approval + failure resilience** — restore pending confirmations on load, retry a failed turn | A reload orphaned a pending action; a 429 dead-ended the conversation | `b6336f4` |
| 2026-09-13 | **Metric history** — 30s sampling, sparklines, and a `metric_history` tool | "Was my CPU busy an hour ago?" was unanswerable at any price; now both the UI and the agent can answer it | `14d0a67` |
| 2026-09-13 | **Proactive watches** — conditions evaluated on every sample, with streak and cooldown | Changes what the product *is*: it now initiates. Verified live — a watch created from plain language fired 70 seconds later without a prompt. | `551c55b` |
| 2026-09-13 | **Desktop notifications** — native Windows toast when a watch fires | Removed the cap on the previous change: an alert now reaches the user with no browser open, with zero new dependencies | `4f0d05a` |
| 2026-09-13 | **Proactive watches** — conditions evaluated on every sample, with streak and cooldown | Changes what the product *is*: it now initiates. Verified live — a watch created from plain language fired 70 seconds later without a prompt. | `551c55b` |
| 2026-09-13 | **Desktop notifications** — native Windows toast when a watch fires | Removed the cap on the previous change: an alert now reaches the user with no browser open, with zero new dependencies | `4f0d05a` |

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

**Iteration 3** — fixed the reply surface: real markdown rendering with XSS
tests, restored pending approvals across reloads, and a retry on failure. The
renderer got its own test suite in CI because everything it renders is model
output or file content.

**Iteration 4** — recorded vitals over time. This made an existing feature
(the dashboard) meaningfully better *and* gave the agent a capability it did not
have: answering questions about the past.

**Iteration 5** — proactive watches. The product now initiates rather than only
responding, which is a change in category rather than degree. Deliberately not
an LLM loop: the model translates the request into a comparison once, and plain
Python evaluates it thereafter — cheap, predictable, and impossible to get wrong
in an interesting way.

**Iteration 6** — closed the cap the previous iteration created. Notable as a
decision rather than an implementation: the conventional answer (web push) would
have added HTTPS, a service worker, VAPID keys and three dependencies to deliver
a message to a machine Jarvis was already running on. A native toast was better
*because* of what this product is.

*Next evaluation:* the remaining honest gap is memory. Every conversation starts
from nothing — Jarvis re-learns the user's main project, their drive layout and
their preferences every time. That is #8, and it is now the largest difference
between this and something that feels like it knows you.
