function el(id) {
  const found = document.getElementById(id);
  if (found) return found;
  const blank = {
    textContent: "",
    innerHTML: "",
    value: "",
    hidden: false,
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    style: {},
    dataset: {},
    addEventListener() {},
    querySelector() { return blank; },
    querySelectorAll() { return []; },
    focus() {},
    reset() {},
    requestSubmit() {},
    setAttribute() {},
    getAttribute() { return null; },
  };
  blank.closest = () => blank;
  return blank;
}


    const statusLabel = el("status-label");
    const dot = el("dot");
    const errorBox = el("error");
    const savedBox = el("saved");
    const list = el("list");
    const search = el("search");
    let messages = [];
    let contacts = [];
    let inbox = [];
    let projects = [];
    let tasks = [];
    let meetings = [];
    let documents = [];
    let followups = [];
    let moduleName = "home";
    let settingsReady = false;
    let emergencyOn = false;
    let selectedId = null;
    let inboxFilter = "all";
    let lastMs = null;
    let lastError = "";

    function formatUptime(seconds) {
      const h = Math.floor(seconds / 3600);
      const m = Math.floor((seconds % 3600) / 60);
      const s = seconds % 60;
      return [h, m, s].map((n) => String(n).padStart(2, "0")).join(":");
    }

    function showError(message) {
      errorBox.style.display = message ? "block" : "none";
      errorBox.textContent = message || "";
      if (message) {
        savedBox.style.display = "none";
        lastError = message;
      }
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }

    function visibleMessages() {
      const query = search.value.trim().toLowerCase();
      if (!query) return messages;
      return messages.filter((item) =>
        item.text.toLowerCase().includes(query) || item.reply.toLowerCase().includes(query)
      );
    }

    function renderLog() {
      const visible = visibleMessages();
      el("in-log").textContent = messages.length;
      if (!messages.length) {
        list.innerHTML = '<div class="empty">Waiting for the first message.</div>';
        return;
      }
      if (!visible.length) {
        list.innerHTML = '<div class="empty">No messages match that search.</div>';
        return;
      }
      list.innerHTML = visible.map((item) => `
        <article class="item">
          <div class="meta">
            <span>#${item.id} · <span class="pill ${item.source === "ai" ? "" : ""}">${item.source === "ai" ? "AI" : "LOCAL"}</span></span>
            <time>${new Date(item.time).toLocaleTimeString()}</time>
          </div>
          <p class="text">${escapeHtml(item.text)}</p>
          <p class="reply">${escapeHtml(item.reply)}</p>
        </article>
      `).join("");
    }

    function fillSettings(settings, mode) {
      const editing = document.activeElement && document.activeElement.closest("#settings-form");
      if (!settingsReady && !editing) {
        el("name").value = settings.name;
        el("model").value = settings.model;
        el("prompt").value = settings.system_prompt;
        settingsReady = true;
      }
      el("mode-hint").textContent = mode === "openai"
        ? "OpenAI is connected."
        : "Local mode. Add OPENAI_API_KEY in backend/.env for open questions.";
    }

    function render(data) {
      showError("");
      const stopped = Boolean(data.settings && data.settings.emergency_stop);
      emergencyOn = stopped;
      const online = data.status === "online";
      statusLabel.textContent = !online ? "OFFLINE" : stopped ? "STOPPED" : "OPTIMAL";
      dot.classList.toggle("off", !online || stopped);
      el("voice-state").textContent = !online || stopped ? "Standby" : "Listening";
      el("title").textContent = data.assistant;
      el("uptime").textContent = formatUptime(data.uptime_seconds);
      el("handled").textContent = data.handled;
      el("mode").textContent = data.mode === "openai" ? "OPENAI" : "LOCAL";
      messages = data.messages || [];
      contacts = data.contacts || [];
      inbox = data.inbox || [];
      projects = data.projects || [];
      tasks = data.tasks || [];
      meetings = data.meetings || [];
      documents = data.documents || [];
      followups = data.followups || [];
      renderWork(data.report || {});
      fillSettings(data.settings, data.mode);
      const mode = data.settings.reply_mode || "approval";
      if (document.activeElement !== el("reply-mode")) {
        el("reply-mode").value = mode;
      }
      el("emergency").textContent = stopped ? "Resume replies" : "Emergency stop";
      el("control-hint").textContent = stopped
        ? "Emergency stop is on. No reply can be sent."
        : "Phone app aur yeh page ek hi API use karte hain.";
      const urgent = inbox.filter((item) => item.priority === "urgent").length;
      const normal = inbox.filter((item) => item.priority === "business").length;
      const pending = inbox.filter((item) => item.status === "pending").length;
      const gmail = inbox.filter((item) => item.channel === "gmail").length;
      const whatsapp = inbox.filter((item) => item.channel === "whatsapp").length;
      const alerts = inbox.filter((item) => item.channel === "notification").length;
      const scale = Math.max(gmail, whatsapp, alerts, 1);
      const bar = (label, count) => `<div class="bar-row"><span>${label}</span><div class="bar"><i style="width:${Math.round((count / scale) * 100)}%"></i></div><b>${count}</b></div>`;
      el("buckets").innerHTML = bar("Gmail", gmail) + bar("WhatsApp", whatsapp) + bar("Alerts", alerts);
      const replied = inbox.filter((item) => item.status === "replied").length;
      const held = inbox.filter((item) => item.status === "held").length;
      const skipped = inbox.filter((item) => item.status === "skipped").length;
      const mixScale = Math.max(replied, pending, held, skipped, 1);
      const mixBar = (label, count) => `<div class="bar-row"><span>${label}</span><div class="bar"><i style="width:${Math.round((count / mixScale) * 100)}%"></i></div><b>${count}</b></div>`;
      el("mix").innerHTML = mixBar("Replied", replied) + mixBar("Pending", pending) + mixBar("Held", held) + mixBar("Skipped", skipped);
      el("dash-contacts").textContent = contacts.filter((person) => person.role === "business").length;
      el("dash-pending").textContent = pending;
      el("dash-gmail").textContent = gmail;
      el("dash-whatsapp").textContent = whatsapp;
      el("stat-replied").textContent = replied;
      el("stat-held").textContent = held;
      el("stat-skipped").textContent = skipped;
      el("health-backend").textContent = online ? "Running" : "Offline";
      const database = data.database || {};
      el("health-db").textContent = database.ok ? "MongoDB" : "Offline";
      el("health-db-hint").textContent = database.host
        ? `${database.name} on ${database.host}`
        : "Contacts, inbox, and settings are stored in MongoDB.";
      el("health-uptime").textContent = formatUptime(data.uptime_seconds);
      el("health-ms").textContent = lastMs == null ? "—" : `${lastMs} ms`;
      el("health-error").textContent = lastError || "None";
      const ruleState = el("rule-state");
      if (ruleState) ruleState.textContent = stopped ? "Automation is blocked by emergency stop." : `Reply mode: ${mode}.`;
      const badge = el("nav-pending");
      badge.hidden = pending === 0;
      badge.textContent = pending ? String(pending) : "";
      el("sys-backend").textContent = online ? "Running" : "Offline";
      el("sys-stop").textContent = stopped ? "On" : "Off";
      el("assist-line").textContent = stopped
        ? "Emergency stop is on. No reply can be sent."
        : "Drafts stay here until you approve them.";
      el("brief").textContent = stopped
        ? `Emergency stop is on. Inbox ${inbox.length}. Pending ${pending}.`
        : `Inbox ${inbox.length}. Pending ${pending}. Business ${normal + urgent}.`;
      el("dash-activity").innerHTML = inbox.length
        ? inbox.slice(0, 5).map((item) => {
            const when = item.time ? new Date(item.time).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
            return `<article class="note"><div class="meta"><strong>${escapeHtml(item.sender || "")}</strong><span>${escapeHtml(when)}</span></div><p class="text">${escapeHtml(item.body || "")}</p><span class="pill ${statusClass(item.status)}">${escapeHtml(channelName(item.channel))} · ${escapeHtml(item.status || "")}</span></article>`;
          }).join("")
        : '<div class="empty">Abhi koi message logged nahi.</div>';
      const sync = el("sync-time");
      if (sync) sync.textContent = `Last sync ${new Date(data.synced_at || Date.now()).toLocaleTimeString()}. Phone aur desktop yahi inbox dekhte hain.`;
      const dashSync = el("dash-sync");
      if (dashSync) dashSync.textContent = `Last refresh ${new Date(data.synced_at || Date.now()).toLocaleTimeString()}. Phone and this page share one inbox.`;
      const audit = data.audit || [];
      el("audit").innerHTML = audit.length
        ? audit.slice(0, 12).map((item) => `<article class="note"><p class="text"><strong>${escapeHtml(item.sender || "")}</strong> · ${escapeHtml(item.channel || "")}</p><p class="reply">${escapeHtml(item.draft || "")}</p></article>`).join("")
        : '<div class="empty">No saved reply memory yet.</div>';
      renderPeople();
      renderInbox();
      renderLeads();
      renderLog();
      el("in-log").textContent = inbox.length;
    }

    function channelName(channel) {
      if (channel === "gmail") return "Gmail";
      if (channel === "whatsapp") return "WhatsApp";
      return "Alert";
    }

    function statusClass(status) {
      if (status === "skipped") return "skip";
      if (status === "held") return "hold";
      if (status === "replied") return "sent";
      return "";
    }

    function renderPeople() {
      const reply = contacts.filter((person) => person.role === "business");
      const quiet = contacts.filter((person) => person.role !== "business");
      el("people").innerHTML = reply.length
        ? reply.map(personCard).join("")
        : '<div class="empty">Nobody on the reply list yet.</div>';
      el("quiet").innerHTML = quiet.length
        ? quiet.map(personCard).join("")
        : '<div class="empty">No operational contacts.</div>';
    }

    function personCard(person) {
      const days = person.role === "business"
        ? [1, 7, 15, 30].map((day) => `<button type="button" data-days="${day}" data-id="${person.id}">${day} din</button>`).join("") + `<button type="button" data-days="0" data-id="${person.id}">Band</button>`
        : "";
      const until = person.reply_until ? new Date(person.reply_until) : null;
      const open = until && !Number.isNaN(until.getTime()) && until.getTime() > Date.now();
      const permit = person.role !== "business"
        ? "Automatic reply is off for this contact."
        : open
          ? `JARVIS can reply to this approved contact until ${until.toLocaleDateString()}. After expiry, automatic replies stop.`
          : person.reply_until
            ? "Automatic replies stopped. The permission window has ended."
            : "Automatic reply is off until you choose 1, 7, 15, or 30 days.";
      return `
        <article class="person">
          <div>
            <strong>${escapeHtml(person.name)}</strong>
            <div class="meta">${escapeHtml(person.handle)} · ${channelName(person.channel)}${person.reply_until ? ` · reply till ${escapeHtml(String(person.reply_until).slice(0, 10))}` : ""}</div>
            <div class="filters">${days}</div>
            <p class="hint">${escapeHtml(permit)}</p>
          </div>
          <button class="clear" type="button" data-remove="${person.id}">Remove</button>
        </article>
      `;
    }

    function filteredInbox() {
      let rows = inbox.slice();
      if (moduleName === "gmail") rows = rows.filter((item) => item.channel === "gmail");
      else if (moduleName === "whatsapp") rows = rows.filter((item) => item.channel === "whatsapp");
      else if (moduleName === "alerts") rows = rows.filter((item) => item.channel === "notification");
      else if (moduleName === "approval") rows = rows.filter((item) => item.status === "pending");
      else if (inboxFilter === "gmail") rows = rows.filter((item) => item.channel === "gmail");
      else if (inboxFilter === "whatsapp") rows = rows.filter((item) => item.channel === "whatsapp");
      else if (inboxFilter === "open") rows = rows.filter((item) => item.status === "pending" || item.status === "held");
      else if (inboxFilter === "history") rows = rows.filter((item) => item.status === "replied" || item.status === "skipped");
      const query = (el("inbox-search").value || "").trim().toLowerCase();
      if (query) {
        rows = rows.filter((item) =>
          `${item.sender || ""} ${item.body || ""} ${item.draft || ""}`.toLowerCase().includes(query)
        );
      }
      return rows;
    }

    function renderWork(report) {
      const lines = el("report-lines");
      if (!lines) return;
      const text = (report.lines || []);
      lines.innerHTML = text.length
        ? text.map((line) => `<p class="hint">${escapeHtml(line)}</p>`).join("")
        : '<p class="hint">Report abhi khali hai.</p>';
      el("project-list").innerHTML = projects.length
        ? projects.map((item) => `<article class="note"><strong>${escapeHtml(item.client)} · ${escapeHtml(item.name)}</strong><p class="hint">Budget ${escapeHtml(item.budget || "not entered")} · Deadline ${escapeHtml(item.deadline || "not entered")}</p></article>`).join("")
        : '<div class="empty">No project yet.</div>';
      el("task-list").innerHTML = tasks.length
        ? tasks.map((item) => `<article class="note"><strong>${escapeHtml(item.title)}</strong><p class="hint">${escapeHtml(item.client || "No client")} · ${item.done ? "Done" : "Open"}${item.due ? ` · ${escapeHtml(item.due)}` : ""}</p>${item.done ? "" : `<button class="clear" type="button" data-done="${item.id}">Done</button>`}</article>`).join("")
        : '<div class="empty">No task yet.</div>';
      el("meeting-list").innerHTML = meetings.length
        ? meetings.map((item) => `<article class="note"><strong>${escapeHtml(item.title)}</strong><p class="hint">${escapeHtml(item.client || "")} · ${escapeHtml(item.when || "")}</p></article>`).join("")
        : '<div class="empty">No meeting yet.</div>';
      el("document-list").innerHTML = documents.length
        ? documents.map((item) => `<article class="note"><strong>${escapeHtml(item.title)} · ${escapeHtml(item.kind || "")}</strong><p class="hint">${escapeHtml(item.summary || "")}</p><p class="hint">Amounts: ${escapeHtml((item.amounts || []).join(", ") || "none written")}</p><p class="hint">Dates: ${escapeHtml((item.dates || []).join(", ") || "none written")}</p><p class="hint">${escapeHtml(item.note || "")}</p></article>`).join("")
        : '<div class="empty">No document text yet.</div>';
      el("followup-list").innerHTML = followups.length
        ? followups.map((item) => `<article class="note"><strong>${escapeHtml(item.client)} · ${item.days} din</strong><p class="hint">${escapeHtml(item.draft || "")}</p></article>`).join("")
        : '<div class="empty">Koi purana follow-up nahi.</div>';
    }

    function renderLeads() {
      const board = el("leads-board");
      const count = el("lead-count");
      if (!board || !count) return;
      const stages = [
        ["new", "New Lead"],
        ["interested", "Interested"],
        ["followup", "Follow-up"],
        ["negotiation", "Negotiation"],
        ["converted", "Converted"],
        ["lost", "Lost"]
      ];
      const rows = inbox.filter((item) => item.lead_status);
      count.textContent = String(rows.length);
      board.innerHTML = stages.map(([key, title]) => {
        const cards = rows.filter((item) => item.lead_status === key);
        const body = cards.length
          ? cards.map((item) => {
              const moves = stages
                .filter(([stage]) => stage !== key)
                .map(([stage, label]) => `<button type="button" data-lead="${item.id}" data-status="${stage}">${label}</button>`)
                .join("");
              return `<article class="lead-card"><strong>${escapeHtml(item.sender || "")}</strong><div class="meta">${escapeHtml(channelName(item.channel))}</div><p class="hint">${escapeHtml(item.summary || item.body || "")}</p>${item.followup ? `<p class="hint">${escapeHtml(item.followup)}</p>` : ""}<div class="filters">${moves}</div></article>`;
            }).join("")
          : '<div class="empty">None</div>';
        return `<div class="lead-col"><div class="label">${title} · ${cards.length}</div>${body}</div>`;
      }).join("");
    }

    function renderInbox() {
      const box = el("inbox");
      const rows = filteredInbox();
      const titles = { approval: "APPROVAL", gmail: "GMAIL", whatsapp: "WHATSAPP", alerts: "ALERTS" };
      el("live-title").textContent = titles[moduleName] || "AI INBOX";
      const notes = {
        gmail: "Gmail OAuth abhi connected nahi. Jo mail yahan log hui, wahi dikhegi.",
        whatsapp: "WhatsApp Business API abhi connected nahi. Logged chats yahan dikhengi.",
        alerts: "Phone alerts. iPhone dusre apps ki notification nahi padhta.",
        approval: "Draft taiyar hai. Reply tabhi queue hota hai jab aap approve karo.",
        inbox: "Gmail, WhatsApp, aur alerts ek inbox mein. Read receipts are not tracked."
      };
      el("module-note").textContent = notes[moduleName] || notes.inbox;
      if (!rows.some((item) => item.id === selectedId)) selectedId = rows[0] ? rows[0].id : null;
      const draftBox = el("draft-edit");
      const keepDraft = draftBox && document.activeElement === draftBox ? draftBox.value : null;
      box.innerHTML = rows.length
        ? rows.map((item) => `<button class="convo-item ${item.id === selectedId ? "active" : ""}" type="button" data-pick="${item.id}"><b>${escapeHtml(item.sender || "")}</b><span>${escapeHtml(channelName(item.channel))} · ${escapeHtml(item.status || "")}</span></button>`).join("")
        : '<div class="empty">Is list mein abhi koi message nahi.</div>';
      const item = rows.find((row) => row.id === selectedId);
      const chat = el("chat-pane");
      const reply = el("reply-pane");
      if (!item) {
        chat.innerHTML = '<div class="empty">Select a message.</div>';
        reply.innerHTML = "";
      } else {
        const when = item.time ? new Date(item.time).toLocaleString() : "";
        const leadNames = { new: "New Lead", interested: "Interested", followup: "Follow-up", negotiation: "Negotiation", converted: "Converted", lost: "Lost" };
        chat.innerHTML = `<div class="meta"><strong>${escapeHtml(item.sender || "")}</strong><span>${escapeHtml(when)}</span></div>${item.summary ? `<p class="hint">Summary: ${escapeHtml(item.summary)}</p>` : ""}<p class="text">${escapeHtml(item.body || "")}</p>${item.lead_status ? `<p class="hint">${escapeHtml(leadNames[item.lead_status] || item.lead_status)}</p>` : ""}${item.followup ? `<p class="hint">Follow-up: ${escapeHtml(item.followup)}</p>` : ""}${item.explain ? `<p class="hint">${escapeHtml(item.explain)}</p>` : ""}<p class="hint">${escapeHtml(item.reason || "")}</p>`;
        const canSend = item.decision === "reply" && item.status !== "replied";
        const draftText = keepDraft != null ? keepDraft : (item.draft || "");
        reply.innerHTML = `<div class="label">SUGGESTED REPLY</div>${item.draft || canSend ? `<textarea id="draft-edit" maxlength="2000">${escapeHtml(draftText)}</textarea>` : `<p class="hint">No draft. This person is not on the reply list.</p>`}<div class="actions">${canSend ? `<button class="save" type="button" data-send="${item.id}">Approve</button>` : ""}${item.status === "held" ? `<button class="save" type="button" data-allow="${item.id}">Add to reply list</button>` : ""}${item.status !== "replied" && item.status !== "skipped" ? `<button class="clear" type="button" data-skip="${item.id}">Reject</button>` : ""}</div>${item.status === "replied" ? `<p class="hint">Approved. The reply is queued on this computer.</p>` : ""}${item.status === "skipped" ? `<p class="hint">Rejected. No reply will be sent.</p>` : ""}`;
      }
      const history = el("history-list");
      if (moduleName === "approval") {
        const done = inbox.filter((row) => row.status === "replied" || row.status === "skipped");
        history.innerHTML = `<div class="label">APPROVED AND REJECTED</div>${done.length ? done.slice(0, 8).map((row) => `<article class="note"><div class="meta"><strong>${escapeHtml(row.sender || "")}</strong><span class="pill ${statusClass(row.status)}">${escapeHtml(row.status || "")}</span></div><p class="text">${escapeHtml(row.body || "")}</p></article>`).join("") : '<div class="empty">No approval history yet.</div>'}`;
      } else {
        history.innerHTML = "";
      }
    }

    async function postJson(url, payload) {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || "Request failed.");
      }
    }

    async function refresh() {
      try {
        const started = performance.now();
        const response = await fetch("/api/admin/overview");
        lastMs = Math.round(performance.now() - started);
        if (!response.ok) throw new Error("Could not load the admin overview.");
        render(await response.json());
      } catch (error) {
        statusLabel.textContent = "OFFLINE";
        dot.classList.add("off");
        el("voice-state").textContent = "Standby";
        showError(error.message);
      }
    }

    async function sendText(text) {
      const response = await fetch("/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text })
      });
      if (!response.ok) throw new Error("JARVIS did not accept that message.");
      await refresh();
    }

    el("form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const input = el("text");
      const button = event.currentTarget.querySelector("button");
      button.disabled = true;
      try {
        await sendText(input.value);
        input.value = "";
      } catch (error) {
        showError(error.message);
      } finally {
        button.disabled = false;
        input.focus();
      }
    });

    document.querySelectorAll(".chip").forEach((chip) => {
      chip.addEventListener("click", async () => {
        if (!chip.dataset.text) return;
        chip.disabled = true;
        try {
          await sendText(chip.dataset.text);
        } catch (error) {
          showError(error.message);
        } finally {
          chip.disabled = false;
        }
      });
    });

    el("settings-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = event.currentTarget.querySelector("button");
      button.disabled = true;
      try {
        const response = await fetch("/api/admin/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: el("name").value,
            model: el("model").value,
            system_prompt: el("prompt").value
          })
        });
        if (!response.ok) throw new Error("Could not save settings.");
        savedBox.style.display = "block";
        settingsReady = true;
        await refresh();
      } catch (error) {
        showError(error.message);
      } finally {
        button.disabled = false;
      }
    });

    el("save-mode").addEventListener("click", async () => {
      try {
        await postJson("/api/control", {
          reply_mode: el("reply-mode").value,
          emergency_stop: emergencyOn
        });
        await refresh();
      } catch (error) {
        showError(error.message);
      }
    });

    el("emergency").addEventListener("click", async () => {
      try {
        await postJson("/api/control", {
          reply_mode: el("reply-mode").value,
          emergency_stop: !emergencyOn
        });
        await refresh();
      } catch (error) {
        showError(error.message);
      }
    });

    el("person-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = event.currentTarget.querySelector("button");
      button.disabled = true;
      try {
        await postJson("/api/contacts", {
          name: el("person-name").value,
          handle: el("person-handle").value,
          channel: el("person-channel").value,
          role: el("person-role").value,
          auto_reply: false
        });
        event.currentTarget.reset();
        await refresh();
      } catch (error) {
        showError(error.message);
      } finally {
        button.disabled = false;
      }
    });

    async function saveWork(event, url, payload) {
      event.preventDefault();
      const button = event.currentTarget.querySelector("button");
      button.disabled = true;
      try {
        await postJson(url, payload());
        event.currentTarget.reset();
        await refresh();
      } catch (error) {
        showError(error.message);
      } finally {
        button.disabled = false;
      }
    }

    el("project-form").addEventListener("submit", (event) => saveWork(event, "/api/projects", () => ({
      client: el("project-client").value,
      name: el("project-name").value,
      budget: el("project-budget").value,
      deadline: el("project-deadline").value
    })));
    el("task-form").addEventListener("submit", (event) => saveWork(event, "/api/tasks", () => ({
      title: el("task-title").value,
      client: el("task-client").value,
      due: el("task-due").value
    })));
    el("meeting-form").addEventListener("submit", (event) => saveWork(event, "/api/meetings", () => ({
      title: el("meeting-title").value,
      client: el("meeting-client").value,
      when: el("meeting-when").value
    })));
    el("document-form").addEventListener("submit", (event) => saveWork(event, "/api/documents", () => ({
      title: el("document-title").value,
      kind: el("document-kind").value,
      text: el("document-text").value
    })));
    el("work").addEventListener("click", async (event) => {
      const button = event.target.closest("[data-done]");
      if (!button) return;
      await fetch(`/api/tasks/${button.dataset.done}/done`, { method: "POST" });
      await refresh();
    });
    el("voice-command").addEventListener("click", () => {
      const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!Speech) {
        showError("Is browser mein voice command nahi hai. Phone ke Desk mic se bolo.");
        return;
      }
      const heard = new Speech();
      heard.lang = "hi-IN";
      heard.onresult = (event) => {
        el("text").value = event.results[0][0].transcript;
        el("form").requestSubmit();
      };
      heard.onerror = () => showError("Voice command sunai nahi diya.");
      heard.start();
    });

    el("inbox-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = event.currentTarget.querySelector("button");
      button.disabled = true;
      try {
        await postJson("/api/inbox", {
          sender: el("in-sender").value,
          handle: el("in-handle").value,
          channel: el("in-channel").value,
          body: el("in-body").value
        });
        event.currentTarget.reset();
        await refresh();
      } catch (error) {
        showError(error.message);
      } finally {
        button.disabled = false;
      }
    });

    el("leads").addEventListener("click", async (event) => {
      const button = event.target.closest("[data-lead]");
      if (!button) return;
      try {
        await postJson(`/api/inbox/${button.dataset.lead}/lead`, { status: button.dataset.status });
        await refresh();
      } catch (error) {
        showError(error.message);
      }
    });

    el("people").addEventListener("click", async (event) => {
      const daysButton = event.target.closest("[data-days]");
      if (daysButton) {
        const person = contacts.find((item) => String(item.id) === daysButton.dataset.id);
        if (!person) return;
        const days = Number(daysButton.dataset.days);
        try {
          await postJson("/api/contacts", {
            name: person.name,
            handle: person.handle,
            channel: person.channel,
            role: person.role,
            auto_reply: days > 0,
            reply_days: days
          });
          await refresh();
        } catch (error) {
          showError(error.message);
        }
        return;
      }
      const button = event.target.closest("[data-remove]");
      if (!button) return;
      await fetch(`/api/contacts/${button.dataset.remove}`, { method: "DELETE" });
      await refresh();
    });
    el("quiet").addEventListener("click", async (event) => {
      const button = event.target.closest("[data-remove]");
      if (!button) return;
      await fetch(`/api/contacts/${button.dataset.remove}`, { method: "DELETE" });
      await refresh();
    });
    el("live").addEventListener("click", async (event) => {
      const pick = event.target.closest("[data-pick]");
      if (pick && !event.target.closest("[data-send], [data-skip], [data-allow]")) {
        selectedId = Number(pick.dataset.pick);
        renderInbox();
        return;
      }
      const button = event.target.closest("button");
      if (!button) return;
      try {
        if (button.dataset.send) {
          const draft = el("draft-edit");
          await postJson(`/api/inbox/${button.dataset.send}/send`, { draft: draft ? draft.value : "" });
        }
        if (button.dataset.skip) await postJson(`/api/inbox/${button.dataset.skip}/skip`, {});
        if (button.dataset.allow) await postJson(`/api/inbox/${button.dataset.allow}/allow`, { role: "business", auto_reply: false });
        await refresh();
      } catch (error) {
        showError(error.message);
      }
    });
    el("inbox-filters").addEventListener("click", (event) => {
      const button = event.target.closest("[data-inbox-filter]");
      if (!button) return;
      inboxFilter = button.dataset.inboxFilter;
      document.querySelectorAll("[data-inbox-filter]").forEach((item) => item.classList.toggle("on", item === button));
      if (moduleName !== "inbox") openScreen("inbox");
      else renderInbox();
    });
    el("inbox-search").addEventListener("input", renderInbox);

    search.addEventListener("input", renderLog);
    el("clear").addEventListener("click", async () => {
      const response = await fetch("/api/admin/log", { method: "DELETE" });
      if (!response.ok) {
        showError("Could not clear the log.");
        return;
      }
      await refresh();
    });
    el("clear-memory").addEventListener("click", async () => {
      const response = await fetch("/api/admin/audit", { method: "DELETE" });
      if (!response.ok) {
        showError("Could not delete saved drafts.");
        return;
      }
      await refresh();
    });
    el("gmail-connect").addEventListener("click", () => {
      showError("Gmail OAuth connected nahi hai.");
    });
    el("whatsapp-connect").addEventListener("click", () => {
      showError("WhatsApp Business API connected nahi hai.");
    });

    const board = document.querySelector(".board");
    const pageTitles = {
      home: "Main Dashboard",
      inbox: "AI Inbox",
      approval: "Approval",
      channels: "Gmail & WhatsApp",
      rules: "Automation",
      brain: "AI Brain",
      security: "Security",
      devices: "Devices",
      health: "System Health",
      people: "Contacts",
      leads: "Leads",
      work: "Work",
      desk: "Desk",
      profile: "Profile",
      reports: "Reports",
      stop: "Emergency Stop",
      gmail: "Gmail",
      whatsapp: "WhatsApp",
      alerts: "Alerts",
    };
    function pathFor(screen) {
      return screen === "home" ? "/admin" : `/admin/${screen}`;
    }
    function screenFromPath() {
      const page = location.pathname.split("/").filter(Boolean)[1] || "home";
      return pageTitles[page] ? page : "home";
    }
    function openScreen(screen, push = true) {
      if (!pageTitles[screen]) screen = "home";
      moduleName = screen;
      document.querySelectorAll(".nav, .who, .head-who").forEach((item) => {
        item.classList.toggle("active", item.dataset.screen === screen);
      });
      if (screen === "home") board.removeAttribute("data-screen");
      else board.dataset.screen = screen;
      document.title = `JARVIS · ${pageTitles[screen]}`;
      const path = pathFor(screen);
      if (push && location.pathname !== path) {
        location.assign(path);
        return;
      }
      if (screen === "api" || screen === "brain") el("settings-form").closest("details").open = true;
      if (screen === "desk" || screen === "stop") el("text").focus();
      renderInbox();
    }
    document.querySelectorAll("[data-screen], [data-jump]").forEach((link) => {
      link.addEventListener("click", (event) => {
        const screen = link.dataset.screen || link.dataset.jump;
        if (!screen || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        openScreen(screen);
      });
    });
    el("focus-talk").addEventListener("click", () => openScreen("desk"));
    window.addEventListener("popstate", () => openScreen(screenFromPath(), false));
    openScreen(screenFromPath(), false);
    async function setMode(mode) {
      await postJson("/api/control", { reply_mode: mode, emergency_stop: emergencyOn });
      await refresh();
    }
    el("rule-approval").addEventListener("click", () => setMode("approval").catch((error) => showError(error.message)));
    el("rule-auto").addEventListener("click", () => setMode("auto").catch((error) => showError(error.message)));

    function paintClock() {
      const now = new Date();
      el("clock-date").textContent = now.toLocaleDateString(undefined, {
        weekday: "long", day: "numeric", month: "short"
      });
      el("clock-time").textContent = now.toLocaleTimeString(undefined, {
        hour: "numeric", minute: "2-digit"
      });
    }
    paintClock();
    setInterval(paintClock, 30000);

    const tokenKey = "jarvis_token";
    let poll = null;
    let creating = false;
    function paintAccount(account) {
      const name = (account.name || "Account").trim();
      const email = account.email || "";
      const mark = (name[0] || "J").toUpperCase();
      el("profile-avatar").textContent = mark;
      el("head-mark").textContent = mark;
      el("head-name").textContent = name;
      el("head-email").textContent = email;
      el("welcome-name").textContent = name;
      el("session-line").textContent = `${name} · ${email} · this browser`;
      el("profile-heading").textContent = name;
      el("profile-mail").textContent = email;
      el("profile-detail-name").textContent = name;
      el("profile-detail-email").textContent = email;
    }
    async function loadProfile() {
      const token = localStorage.getItem(tokenKey) || sessionStorage.getItem(tokenKey);
      if (!token) return;
      const response = await fetch("/api/account/me", { headers: { Authorization: "Bearer " + token } });
      if (!response.ok) return;
      paintAccount(await response.json());
    }
    function showDesk() {
      el("gate").hidden = true;
      document.querySelector(".app").hidden = false;
      loadProfile();
      if (!poll) {
        refresh();
        poll = setInterval(refresh, 3000);
      }
    }
    function showGate() {
      el("gate").hidden = false;
      document.querySelector(".app").hidden = true;
      if (poll) {
        clearInterval(poll);
        poll = null;
      }
    }
    function keepToken(token, remember) {
      localStorage.removeItem(tokenKey);
      sessionStorage.removeItem(tokenKey);
      (remember ? localStorage : sessionStorage).setItem(tokenKey, token);
    }
    el("gate-switch").addEventListener("click", () => {
      creating = !creating;
      el("gate-name").hidden = !creating;
      el("gate-title").innerHTML = creating ? "Create <span>account</span>" : "Admin <span>Login</span>";
      el("gate-submit").textContent = creating ? "Create account" : "Login";
      el("gate-switch").textContent = creating
        ? "Already have an account? Login"
        : "Naya account banao";
      el("gate-error").textContent = "";
    });
    el("gate-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const email = el("gate-email").value.trim();
      const password = el("gate-password").value;
      const name = el("gate-name").value.trim();
      const remember = el("gate-remember").checked;
      const error = el("gate-error");
      error.textContent = "";
      try {
        const response = await fetch(creating ? "/api/account/register" : "/api/account/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, password, name })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || "Sign in failed.");
        keepToken(data.token, remember);
        paintAccount(data);
        showDesk();
      } catch (err) {
        error.textContent = err.message;
      }
    });
    el("gate-forgot").addEventListener("click", () => {
      el("gate-error").textContent = "Email se password reset nahi hota. Account delete karke naya bana sakte ho.";
    });
    el("gate-google").addEventListener("click", () => {
      el("gate-error").textContent = "Google login connected nahi hai.";
    });
    function signOut() {
      localStorage.removeItem(tokenKey);
      sessionStorage.removeItem(tokenKey);
      showGate();
    }
    el("profile-sign-out").addEventListener("click", signOut);
    const savedToken = localStorage.getItem(tokenKey) || sessionStorage.getItem(tokenKey);
    if (!savedToken) showGate();
    else {
      fetch("/api/account/me", { headers: { Authorization: "Bearer " + savedToken } })
        .then((response) => {
          if (!response.ok) throw new Error("signed out");
          showDesk();
        })
        .catch(() => {
          localStorage.removeItem(tokenKey);
          sessionStorage.removeItem(tokenKey);
          showGate();
        });
    }
  