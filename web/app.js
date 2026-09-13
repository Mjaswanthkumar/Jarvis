/* Jarvis web client: token gate, live metrics, chat, tool activity. */
(() => {
  "use strict";

  const TOKEN_KEY = "jarvis.token";
  const CONV_KEY = "jarvis.conversation";
  const POLL_MS = 5000;

  const $ = (id) => document.getElementById(id);
  const els = {
    gate: $("gate"),
    gateForm: $("gate-form"),
    gateToken: $("gate-token"),
    gateError: $("gate-error"),
    app: $("app"),
    host: $("host"),
    statusDot: $("status-dot"),
    statusText: $("status-text"),
    logout: $("logout"),
    messages: $("messages"),
    composer: $("composer"),
    input: $("input"),
    send: $("send"),
    activity: $("activity"),
    side: $("side"),
    metrics: $("metrics"),
    metricsToggle: $("metrics-toggle"),
    gateHint: $("gate-hint"),
    mic: $("mic"),
    speakToggle: $("speak-toggle"),
    voiceStatus: $("voice-status"),
    voiceText: $("voice-text"),
    menu: $("menu"),
    newChat: $("new-chat"),
    drawer: $("drawer"),
    drawerScrim: $("drawer-scrim"),
    drawerClose: $("drawer-close"),
    drawerNew: $("drawer-new"),
    conversations: $("conversations"),
    sheet: $("sheet"),
    sheetScrim: $("sheet-scrim"),
    sheetClose: $("sheet-close"),
    sheetBody: $("sheet-body"),
  };

  //: Shown in an empty conversation so a new user knows what to ask for.
  const STARTERS = [
    "How is my PC doing?",
    "What is using the most memory?",
    "What apps do I have open?",
    "What is eating my disk space?",
    "Is Docker running?",
  ];

  const SPEAK_KEY = "jarvis.speak";

  const REQUEST_TIMEOUT_MS = 45000;
  //: Sparklines change slowly; refreshing them every poll would be wasted work.
  const HISTORY_REFRESH_MS = 60000;

  /** Read a token handed over by the pairing QR code, then scrub the URL. */
  function tokenFromFragment() {
    const match = /[#&]t=([^&]+)/.exec(window.location.hash || "");
    if (!match) return "";
    history.replaceState(null, "", window.location.pathname);
    return decodeURIComponent(match[1]);
  }

  const pairedToken = tokenFromFragment();
  let token = pairedToken || storageGet(TOKEN_KEY) || "";
  let conversationId = storageGet(CONV_KEY) || null;
  let pollTimer = null;
  let historyFetchedAt = 0;
  const voice = window.JarvisVoice;
  let speakReplies = storageGet(SPEAK_KEY) === "1";
  //: True while a voice turn is in flight, so we know to re-open the mic.
  let handsFree = false;

  function storageGet(key) {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  }

  function storageSet(key, value) {
    try {
      value === null ? localStorage.removeItem(key) : localStorage.setItem(key, value);
    } catch {
      /* private mode -- session-only is fine */
    }
  }

  async function api(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    let response;
    try {
      response = await fetch(`/api${path}`, {
        ...options,
        signal: controller.signal,
        headers: {
          "Content-Type": "application/json",
          "X-Jarvis-Token": token,
          ...(options.headers || {}),
        },
      });
    } catch (error) {
      throw new Error(
        error.name === "AbortError" ? "request timed out" : "cannot reach this PC"
      );
    } finally {
      clearTimeout(timer);
    }
    if (response.status === 429) {
      throw new Error("too many attempts -- wait a few minutes and retry");
    }
    if (response.status === 401) {
      const error = new Error("unauthorized");
      error.unauthorized = true;
      throw error;
    }
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || `request failed (${response.status})`);
    }
    return response.status === 204 ? null : response.json();
  }

  /* ---------- metrics ---------- */
  function setBar(barId, valueId, percent, label) {
    const bar = $(barId);
    const value = $(valueId);
    if (percent === null || percent === undefined) {
      bar.style.width = "0%";
      value.textContent = label ?? "—";
      return;
    }
    bar.style.width = `${Math.max(0, Math.min(100, percent))}%`;
    bar.className = percent >= 90 ? "bad" : percent >= 75 ? "warn" : "";
    value.textContent = label ?? `${Math.round(percent)}%`;
  }

  function renderSystem(data) {
    els.host.textContent = `${data.host} · ${data.os}`;
    setBar("cpu-bar", "cpu-val", data.cpu.percent);
    setBar(
      "ram-bar",
      "ram-val",
      data.memory.percent,
      `${data.memory.used_gb}/${data.memory.total_gb} GB`
    );
    const primary = data.disks[0];
    if (primary) {
      setBar(
        "disk-bar",
        "disk-val",
        primary.percent,
        `${primary.free_gb} GB free`
      );
    }
    if (data.battery.present) {
      setBar(
        "bat-bar",
        "bat-val",
        data.battery.percent,
        `${Math.round(data.battery.percent)}%${data.battery.plugged_in ? " ⚡" : ""}`
      );
    } else {
      setBar("bat-bar", "bat-val", null, "AC");
    }
  }

  /** Draw one metric's recent history as a sparkline. */
  function drawSpark(id, values) {
    const svg = $(id);
    if (!svg) return;
    const points = values.filter((value) => typeof value === "number");
    if (points.length < 2) {
      svg.innerHTML = "";
      return;
    }

    // Scale to the observed range with a little headroom, so a flat-but-busy
    // line is still readable rather than pinned to the floor.
    const high = Math.max(...points, 1);
    const low = Math.min(...points);
    const span = Math.max(high - low, 5);
    const step = 100 / (points.length - 1);
    const path = points
      .map((value, index) => {
        const x = (index * step).toFixed(2);
        const y = (22 - ((value - low) / span) * 20).toFixed(2);
        return `${index ? "L" : "M"}${x},${y}`;
      })
      .join(" ");

    const area = `${path} L100,24 L0,24 Z`;
    svg.innerHTML =
      `<path class="spark-area" d="${area}"></path>` +
      `<path class="spark-line" d="${path}"></path>`;
    svg.setAttribute("title", `${Math.round(low)}%–${Math.round(high)}% recently`);
  }

  async function refreshHistory(force) {
    const now = Date.now();
    if (!force && now - historyFetchedAt < HISTORY_REFRESH_MS) return;
    historyFetchedAt = now;
    try {
      const data = await api("/metrics/history?minutes=60");
      const rows = data.samples || [];
      drawSpark("cpu-spark", rows.map((r) => r.cpu_percent));
      drawSpark("ram-spark", rows.map((r) => r.memory_percent));
      drawSpark("disk-spark", rows.map((r) => r.disk_percent));
      drawSpark("bat-spark", rows.map((r) => r.battery_percent));
    } catch (error) {
      if (error.unauthorized) logout("Session expired.");
    }
  }

  function setStatus(online, text) {
    els.statusDot.className = `dot ${online ? "online" : "offline"}`;
    els.statusText.textContent = text;
  }

  async function poll() {
    try {
      const [system, activity] = await Promise.all([
        api("/system"),
        api("/activity?limit=12"),
      ]);
      renderSystem(system);
      renderActivity(activity);
      setStatus(true, "online");
      refreshHistory(false);
    } catch (error) {
      if (error.unauthorized) return logout("Session expired.");
      setStatus(false, "offline");
    }
  }

  function renderActivity(rows) {
    els.activity.innerHTML = "";
    if (!rows.length) {
      els.activity.innerHTML = '<li class="empty">No tool calls yet.</li>';
      return;
    }
    for (const row of rows) {
      const li = document.createElement("li");
      const time = new Date(row.created_at).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      });
      li.innerHTML = `<div class="row"><span class="name">${escapeHtml(
        row.tool
      )}</span><span class="meta">${time}</span></div>
      <div class="row"><span class="meta${row.ok ? "" : " bad"}">${
        row.ok ? "ok" : escapeHtml(row.error || "failed")
      }</span><span class="meta">${row.duration_ms} ms</span></div>`;

      if (row.result_preview || Object.keys(row.args || {}).length) {
        li.classList.add("expandable");
        li.title = "Click to inspect";
        const detail = document.createElement("div");
        detail.className = "tool-details";
        detail.hidden = true;
        li.appendChild(detail);
        li.addEventListener("click", () => {
          detail.hidden = !detail.hidden;
          if (!detail.hidden) {
            detail.innerHTML = renderToolDetail({
              args: row.args,
              result_preview: row.result_preview,
              error: row.error,
              permission: row.permission,
              decision: row.decision,
            });
          }
        });
      }
      els.activity.appendChild(li);
    }
  }

  /* ---------- chat ---------- */
  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text ?? "";
    return div.innerHTML;
  }

  function renderMarkdown(text) {
    return window.JarvisMarkdown.render(text);
  }

  /** Show exactly what a tool was asked and what it gave back. */
  function renderToolDetail(event) {
    const rows = [];
    const args = Object.keys(event.args || {}).length
      ? JSON.stringify(event.args, null, 2)
      : "(no arguments)";
    rows.push(`<div class="detail-label">Arguments</div><pre>${escapeHtml(args)}</pre>`);

    if (event.result_preview) {
      rows.push(
        `<div class="detail-label">Returned</div><pre>${escapeHtml(
          event.result_preview
        )}</pre>`
      );
    }
    if (event.error) {
      rows.push(
        `<div class="detail-label">Error</div><pre class="bad">${escapeHtml(
          event.error
        )}</pre>`
      );
    }

    const facts = [
      `<span class="tag">${escapeHtml(event.permission)}</span>`,
      `<span class="tag">${escapeHtml(event.decision)}</span>`,
    ];
    if (event.tainted) {
      facts.push('<span class="tag warn">escalated: read external content</span>');
    }
    if (event.injection_findings && event.injection_findings.length) {
      facts.push(
        `<span class="tag bad">injection signals: ${escapeHtml(
          event.injection_findings.join(", ")
        )}</span>`
      );
    }
    rows.push(`<div class="detail-facts">${facts.join("")}</div>`);
    return rows.join("");
  }

  function addMessage(role, html, toolEvents) {
    const wrapper = document.createElement("div");
    wrapper.className = `msg ${role}`;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.innerHTML = html;
    if (toolEvents && toolEvents.length) {
      const chips = document.createElement("div");
      chips.className = "tools-used";
      const details = document.createElement("div");
      details.className = "tool-details";
      details.hidden = true;

      for (const event of toolEvents) {
        const chip = document.createElement("button");
        chip.className = `chip${event.ok ? "" : " fail"}`;
        chip.title = "Show what this tool was asked and what it returned";
        chip.textContent = event.duration_ms
          ? `${event.name} · ${event.duration_ms}ms`
          : event.name;
        chip.addEventListener("click", () => {
          const showing = details.dataset.open === event.name && !details.hidden;
          if (showing) {
            details.hidden = true;
            details.dataset.open = "";
            return;
          }
          details.dataset.open = event.name;
          details.hidden = false;
          details.innerHTML = renderToolDetail(event);
        });
        chips.appendChild(chip);
      }
      bubble.append(chips, details);
    }
    wrapper.appendChild(bubble);
    els.messages.appendChild(wrapper);
    els.messages.scrollTop = els.messages.scrollHeight;
    return wrapper;
  }

  function addConfirmation(confirmation) {
    const wrapper = document.createElement("div");
    wrapper.className = "msg assistant";
    const card = document.createElement("div");
    card.className = `bubble confirm-card${confirmation.suspicious ? " suspicious" : ""}`;
    const heading = confirmation.suspicious
      ? "⛔ Confirmation required — possible prompt injection"
      : confirmation.escalated
        ? "⚠ Confirmation required — proposed after reading a file"
        : "⚠ Confirmation required";

    let note = "";
    if (confirmation.suspicious) {
      note = `Content Jarvis just read contained text that tried to issue
        instructions. This action may have been suggested by that content rather
        than by you.`;
    } else if (confirmation.escalated) {
      note = `This action would normally run without asking, but Jarvis read
        external content earlier in this turn — so it may have been suggested by
        that content rather than by you.`;
    }

    card.innerHTML = `<div class="confirm-head">${heading}</div>
      <div class="confirm-summary">${escapeHtml(confirmation.summary)}</div>` +
      (note ? `<div class="confirm-warning">${note}</div>` : "");

    const actions = document.createElement("div");
    actions.className = "confirm-actions";
    const approve = document.createElement("button");
    approve.textContent = "Approve";
    approve.className = "approve";
    const deny = document.createElement("button");
    deny.textContent = "Cancel";
    deny.className = "deny";
    actions.append(approve, deny);
    card.appendChild(actions);
    wrapper.appendChild(card);
    els.messages.appendChild(wrapper);
    els.messages.scrollTop = els.messages.scrollHeight;

    const resolve = async (approved) => {
      approve.disabled = deny.disabled = true;
      actions.remove();
      card.insertAdjacentHTML(
        "beforeend",
        `<div class="confirm-state">${approved ? "Approved" : "Cancelled"}</div>`
      );
      const typing = approved ? addTyping() : null;
      try {
        const data = await api("/confirm", {
          method: "POST",
          body: JSON.stringify({
            confirmation_id: confirmation.id,
            approve: approved,
          }),
        });
        typing?.remove();
        addMessage("assistant", renderMarkdown(data.reply), data.tool_events);
        (data.confirmations || []).forEach(addConfirmation);
        poll();
      } catch (error) {
        typing?.remove();
        if (error.unauthorized) return logout("Session expired.");
        addMessage("assistant", `<span class="error">${escapeHtml(error.message)}</span>`);
      }
    };

    approve.addEventListener("click", () => resolve(true));
    deny.addEventListener("click", () => resolve(false));
  }

  /** A failed turn keeps the question, so retrying costs one click. */
  function addFailure(message, reason) {
    const wrapper = addMessage(
      "assistant",
      '<span class="error">' + escapeHtml(reason) + "</span>" +
        '<div class="failure-actions"><button class="starter retry">Retry</button></div>'
    );
    wrapper.querySelector(".retry").addEventListener("click", () => {
      wrapper.remove();
      send(message);
    });
  }

  function addTyping() {
    return addMessage(
      "assistant",
      '<span class="typing"><span></span><span></span><span></span></span>'
    );
  }

  async function send(message) {
    const welcome = els.messages.querySelector(".welcome");
    if (welcome) welcome.closest(".msg").remove();
    addMessage("user", escapeHtml(message));
    const typing = addTyping();
    els.send.disabled = true;
    try {
      const data = await api("/chat", {
        method: "POST",
        body: JSON.stringify({ message, conversation_id: conversationId }),
      });
      conversationId = data.conversation_id;
      storageSet(CONV_KEY, conversationId);
      typing.remove();
      addMessage("assistant", renderMarkdown(data.reply), data.tool_events);
      (data.confirmations || []).forEach(addConfirmation);
      // A pending confirmation needs a tap, so don't re-open the mic over it.
      announce(data.reply, !(data.confirmations || []).length);
      poll();
    } catch (error) {
      typing.remove();
      handsFree = false;
      if (error.unauthorized) return logout("Session expired.");
      addFailure(message, error.message);
    } finally {
      els.send.disabled = false;
      if (!isTouchDevice()) els.input.focus();
    }
  }

  /* ---------- conversations ---------- */
  function openPanel(panel, scrim) {
    panel.hidden = false;
    scrim.hidden = false;
  }

  function closePanels() {
    els.drawer.hidden = true;
    els.drawerScrim.hidden = true;
    els.sheet.hidden = true;
    els.sheetScrim.hidden = true;
  }

  async function showConversations() {
    openPanel(els.drawer, els.drawerScrim);
    els.conversations.innerHTML = '<li class="empty">Loading...</li>';
    try {
      renderConversations(await api("/conversations?limit=30"));
    } catch (error) {
      if (error.unauthorized) return logout("Session expired.");
      els.conversations.innerHTML =
        '<li class="empty">' + escapeHtml(error.message) + "</li>";
    }
  }

  function renderConversations(rows) {
    const usable = rows.filter(
      (row) => row.message_count > 0 || row.id === conversationId
    );
    if (!usable.length) {
      els.conversations.innerHTML = '<li class="empty">No conversations yet.</li>';
      return;
    }
    els.conversations.innerHTML = "";
    for (const row of usable) {
      const li = document.createElement("li");
      if (row.id === conversationId) li.className = "current";

      const open = document.createElement("button");
      open.className = "conversation";
      const plural = row.message_count === 1 ? "" : "s";
      open.innerHTML =
        '<span class="title">' + escapeHtml(row.title || "Untitled") + "</span>" +
        '<span class="meta">' + row.message_count + " message" + plural +
        " \u00b7 " + relativeTime(row.updated_at) + "</span>";
      open.addEventListener("click", () => switchConversation(row.id));

      const remove = document.createElement("button");
      remove.className = "icon-btn delete";
      remove.textContent = "\u2715";
      remove.title = "Delete this conversation";
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        deleteConversation(row.id);
      });

      li.append(open, remove);
      els.conversations.appendChild(li);
    }
  }

  function relativeTime(iso) {
    const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
    if (seconds < 90) return "just now";
    if (seconds < 3600) return Math.round(seconds / 60) + "m ago";
    if (seconds < 86400) return Math.round(seconds / 3600) + "h ago";
    return Math.round(seconds / 86400) + "d ago";
  }

  async function switchConversation(id) {
    conversationId = id;
    storageSet(CONV_KEY, id);
    closePanels();
    els.messages.innerHTML = "";
    await loadHistory();
    await restorePendingConfirmations();
  }

  async function startNewConversation() {
    voice.stopSpeaking();
    closePanels();
    try {
      const created = await api("/conversations", { method: "POST" });
      conversationId = created.id;
      storageSet(CONV_KEY, created.id);
    } catch (error) {
      if (error.unauthorized) return logout("Session expired.");
      // A failed create must not strand the user in the old thread.
      conversationId = null;
      storageSet(CONV_KEY, null);
    }
    els.messages.innerHTML = "";
    showWelcome();
    if (!isTouchDevice()) els.input.focus();
  }

  async function deleteConversation(id) {
    try {
      await api("/conversations/" + id, { method: "DELETE" });
    } catch (error) {
      if (error.unauthorized) return logout("Session expired.");
    }
    if (id === conversationId) await startNewConversation();
    showConversations();
  }

  /* ---------- capabilities ---------- */
  function showWelcome() {
    const chips = STARTERS.map(
      (text) => '<button class="starter">' + escapeHtml(text) + "</button>"
    ).join("");
    const wrapper = addMessage(
      "assistant",
      '<span class="welcome"></span>Jarvis online. Ask about this PC in plain ' +
        "language, or start with one of these:" +
        '<div class="starters">' + chips + "</div>" +
        '<button class="link-btn" id="see-all">See everything Jarvis can do</button>'
    );
    wrapper.querySelectorAll(".starter").forEach((button, index) => {
      button.addEventListener("click", () => {
        els.input.value = "";
        send(STARTERS[index]);
      });
    });
    wrapper.querySelector("#see-all").addEventListener("click", showCapabilities);
  }

  const PERMISSION_LABEL = {
    READ_ONLY: ["Reads only", "ok"],
    LOW_RISK: ["Small change", "warn"],
    CONFIRM_REQUIRED: ["Asks first", "bad"],
  };

  async function showCapabilities() {
    openPanel(els.sheet, els.sheetScrim);
    els.sheetBody.innerHTML = '<p class="empty">Loading...</p>';
    try {
      renderCapabilities(await api("/tools"));
    } catch (error) {
      if (error.unauthorized) return logout("Session expired.");
      els.sheetBody.innerHTML =
        '<p class="empty">' + escapeHtml(error.message) + "</p>";
    }
  }

  function renderCapabilities(tools) {
    const groups = {};
    for (const tool of tools) {
      const key = tool.tags[0] || "other";
      (groups[key] = groups[key] || []).push(tool);
    }
    els.sheetBody.innerHTML = Object.entries(groups)
      .map(([group, items]) => {
        const rows = items
          .map((tool) => {
            const pair = PERMISSION_LABEL[tool.permission] || [tool.permission, ""];
            return (
              "<li><div class=\"tool-row\"><code>" + escapeHtml(tool.name) +
              '</code><span class="tag ' + pair[1] + '">' + pair[0] +
              "</span></div><p>" + escapeHtml(tool.description) + "</p></li>"
            );
          })
          .join("");
        return "<h3>" + escapeHtml(group) + '</h3><ul class="tool-list">' + rows + "</ul>";
      })
      .join("");
  }

  /* ---------- voice ---------- */

  /** Speak a reply, and hand the turn back to the mic if the user spoke first. */
  function announce(text, mayContinue) {
    const resume = handsFree && mayContinue;
    handsFree = false;
    if (!speakReplies) {
      if (resume) setTimeout(() => voice.start(), 350);
      return;
    }
    voice.speak(text, () => {
      if (resume) setTimeout(() => voice.start(), 250);
    });
  }

  function showVoiceStatus(text, listening) {
    els.voiceStatus.hidden = false;
    els.voiceText.textContent = text;
    els.voiceStatus.classList.toggle("listening", Boolean(listening));
  }

  function hideVoiceStatus(delay = 0) {
    setTimeout(() => {
      els.voiceStatus.hidden = true;
      els.voiceStatus.classList.remove("listening");
    }, delay);
  }

  /** Upload a recording for server-side transcription. */
  async function transcribe(blob) {
    const form = new FormData();
    const extension = blob.type.includes("mp4") ? "mp4" : blob.type.split("/")[1];
    form.append("audio", blob, `speech.${extension}`);

    const response = await fetch("/api/transcribe", {
      method: "POST",
      headers: { "X-Jarvis-Token": token },
      body: form,
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || "could not transcribe that");
    }
    return (await response.json()).text;
  }

  function setupVoice() {
    voice.transcriber = transcribe;
    if (voice.canSpeak) {
      els.speakToggle.hidden = false;
      updateSpeakButton();
      els.speakToggle.addEventListener("click", () => {
        speakReplies = !speakReplies;
        storageSet(SPEAK_KEY, speakReplies ? "1" : null);
        if (!speakReplies) voice.stopSpeaking();
        updateSpeakButton();
      });
    } else {
      els.speakToggle.hidden = true;
    }

    if (!voice.canListen) {
      els.mic.hidden = true;
      return;
    }
    els.mic.hidden = false;

    voice
      .on("start", () => {
        els.mic.classList.add("active");
        els.input.value = "";
        showVoiceStatus(
          voice.mode === "recorder" ? "Recording… tap 🎙 when done" : "Listening…",
          true
        );
      })
      .on("partial", (text) => {
        els.input.value = text;
        if (text) showVoiceStatus(text, true);
      })
      .on("fallback", () => {
        showVoiceStatus("Using Jarvis to transcribe in this browser…", false);
      })
      .on("transcribing", () => {
        els.mic.classList.remove("active");
        showVoiceStatus("Transcribing…", false);
      })
      .on("error", (message) => {
        handsFree = false;
        els.mic.classList.remove("active");
        showVoiceStatus(message, false);
        hideVoiceStatus(3000);
      })
      .on("end", (finalText) => {
        els.mic.classList.remove("active");
        if (!finalText) return hideVoiceStatus(1200);
        hideVoiceStatus(0);
        els.input.value = "";
        handsFree = true;
        send(finalText);
      });

    els.mic.addEventListener("click", () => {
      voice.stopSpeaking();
      voice.toggle();
    });
  }

  function updateSpeakButton() {
    els.speakToggle.textContent = speakReplies ? "🔊" : "🔈";
    els.speakToggle.setAttribute("aria-pressed", String(speakReplies));
    els.speakToggle.title = speakReplies
      ? "Replies are spoken aloud"
      : "Speak replies aloud";
    els.speakToggle.classList.toggle("on", speakReplies);
  }

  /** Pending approvals live on the server; a reload must not orphan them. */
  async function restorePendingConfirmations() {
    if (!conversationId) return;
    try {
      const rows = await api(
        "/confirmations?conversation_id=" + encodeURIComponent(conversationId)
      );
      rows.forEach(addConfirmation);
    } catch (error) {
      if (error.unauthorized) return logout("Session expired.");
    }
  }

  async function loadHistory() {
    if (!conversationId) return showWelcome();
    try {
      const data = await api(`/conversations/${conversationId}`);
      els.messages.innerHTML = "";
      if (!data.messages.length) return showWelcome();
      for (const message of data.messages) {
        addMessage(
          message.role === "user" ? "user" : "assistant",
          renderMarkdown(message.content)
        );
      }
    } catch {
      storageSet(CONV_KEY, null);
      conversationId = null;
      els.messages.innerHTML = "";
      showWelcome();
    }
  }

  /* ---------- session ---------- */
  async function connect(candidate) {
    token = candidate;
    const health = await api("/health");
    storageSet(TOKEN_KEY, token);
    els.gate.hidden = true;
    els.app.hidden = false;
    const flags = [];
    if (!health.llm_configured) flags.push("no API key");
    if (health.read_only_mode) flags.push("read-only");
    setStatus(true, flags.length ? `online · ${flags.join(" · ")}` : "online");
    await loadHistory();
    await restorePendingConfirmations();
    await poll();
    await refreshHistory(true);
    startPolling();
    if (!isTouchDevice()) els.input.focus();
  }

  function isTouchDevice() {
    return window.matchMedia("(pointer: coarse)").matches;
  }

  /** Polling costs battery on a phone, so only poll a visible page. */
  function startPolling() {
    clearInterval(pollTimer);
    pollTimer = setInterval(() => {
      if (document.visibilityState === "visible") poll();
    }, POLL_MS);
  }

  function logout(reason) {
    clearInterval(pollTimer);
    handsFree = false;
    voice.stop();
    voice.stopSpeaking();
    storageSet(TOKEN_KEY, null);
    token = "";
    els.app.hidden = true;
    els.gate.hidden = false;
    els.gateError.hidden = !reason;
    els.gateError.textContent = reason || "";
  }

  els.gateForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    els.gateError.hidden = true;
    try {
      await connect(els.gateToken.value.trim());
    } catch (error) {
      els.gateError.hidden = false;
      els.gateError.textContent = error.unauthorized
        ? "Invalid token."
        : error.message;
    }
  });

  els.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    const message = els.input.value.trim();
    if (!message) return;
    if (voice.listening) voice.stop();
    voice.stopSpeaking();
    els.input.value = "";
    send(message);
  });

  els.logout.addEventListener("click", () => logout(""));
  els.menu.addEventListener("click", showConversations);
  els.newChat.addEventListener("click", startNewConversation);
  els.drawerNew.addEventListener("click", startNewConversation);
  els.drawerClose.addEventListener("click", closePanels);
  els.drawerScrim.addEventListener("click", closePanels);
  els.sheetClose.addEventListener("click", closePanels);
  els.sheetScrim.addEventListener("click", closePanels);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closePanels();
    // Ctrl/Cmd+K is the near-universal "start something new" shortcut.
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      startNewConversation();
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      if (token && !els.app.hidden) poll();
    } else {
      voice.stop();
      voice.stopSpeaking();
    }
  });

  els.metricsToggle.addEventListener("click", () => {
    const collapsed = els.metrics.classList.toggle("collapsed");
    els.metricsToggle.setAttribute("aria-expanded", String(!collapsed));
  });

  setupVoice();

  if (pairedToken) els.gateHint.hidden = false;
  if (token) {
    connect(token).catch(() => logout(pairedToken ? "Pairing link expired." : ""));
  }
})();
