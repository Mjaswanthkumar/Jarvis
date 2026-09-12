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
  };

  const SPEAK_KEY = "jarvis.speak";

  const REQUEST_TIMEOUT_MS = 45000;

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
    return escapeHtml(text)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/^\s*[-*]\s+(.*)$/gm, "• $1");
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
      for (const event of toolEvents) {
        const chip = document.createElement("span");
        chip.className = `chip${event.ok ? "" : " fail"}`;
        chip.textContent = event.name;
        chips.appendChild(chip);
      }
      bubble.appendChild(chips);
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
      : "⚠ Confirmation required";
    card.innerHTML = `<div class="confirm-head">${heading}</div>
      <div class="confirm-summary">${escapeHtml(confirmation.summary)}</div>` +
      (confirmation.suspicious
        ? `<div class="confirm-warning">Content Jarvis just read contained text
           that tried to issue instructions. This action may have been suggested
           by that content rather than by you.</div>`
        : "");

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

  function addTyping() {
    return addMessage(
      "assistant",
      '<span class="typing"><span></span><span></span><span></span></span>'
    );
  }

  async function send(message) {
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
      addMessage("assistant", `<span class="error">${escapeHtml(error.message)}</span>`);
    } finally {
      els.send.disabled = false;
      if (!isTouchDevice()) els.input.focus();
    }
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

  async function loadHistory() {
    if (!conversationId) return;
    try {
      const data = await api(`/conversations/${conversationId}`);
      if (!data.messages.length) return;
      els.messages.innerHTML = "";
      for (const message of data.messages) {
        addMessage(
          message.role === "user" ? "user" : "assistant",
          renderMarkdown(message.content)
        );
      }
    } catch {
      storageSet(CONV_KEY, null);
      conversationId = null;
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
    await poll();
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
