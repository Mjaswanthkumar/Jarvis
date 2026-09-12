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
  };

  let token = storageGet(TOKEN_KEY) || "";
  let conversationId = storageGet(CONV_KEY) || null;
  let pollTimer = null;

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
    const response = await fetch(`/api${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-Jarvis-Token": token,
        ...(options.headers || {}),
      },
    });
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
      poll();
    } catch (error) {
      typing.remove();
      if (error.unauthorized) return logout("Session expired.");
      addMessage("assistant", `<span style="color:#ff6b6b">${escapeHtml(error.message)}</span>`);
    } finally {
      els.send.disabled = false;
      els.input.focus();
    }
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
    setStatus(true, health.llm_configured ? "online" : "online · no API key");
    await loadHistory();
    await poll();
    clearInterval(pollTimer);
    pollTimer = setInterval(poll, POLL_MS);
    els.input.focus();
  }

  function logout(reason) {
    clearInterval(pollTimer);
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
    els.input.value = "";
    send(message);
  });

  els.logout.addEventListener("click", () => logout(""));

  if (token) {
    connect(token).catch(() => logout(""));
  }
})();
