import { renderChatReply } from "./chat-render/markdown.js";

// Sessions live in this browser only. The server receives the active session's recent turns.
const SESSIONS_KEY = "dhub.chat.sessions.v1";
const ACTIVE_KEY = "dhub.chat.active.v1";
const LEGACY_HISTORY_KEY = "dhub.chat.history.v1";
const MAX_SESSIONS = 30;
const MAX_CONTEXT_TURNS = 12;
const form = document.querySelector("#chat-form");
const input = document.querySelector("#chat-input");
const send = document.querySelector("#chat-send");
const reset = document.querySelector("#new-chat");
const scrollArea = document.querySelector("#chat-scroll");
const transcript = document.querySelector("#chat-history");
const empty = document.querySelector("#chat-empty");
const feedback = document.querySelector("#chat-status");
const sessionList = document.querySelector("#session-list");
const sessionEmpty = document.querySelector("#session-empty");
const sessions = loadSessions();
let active = sessions.find(session => session.id === readStorage(sessionStorage, ACTIVE_KEY)) ?? newSession();
let pending = false;

function readStorage(storage, key) {
  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

function validTurns(value) {
  return Array.isArray(value) ? value.filter(turn =>
    typeof turn?.user === "string" && typeof turn.assistant === "string"
  ) : [];
}

function newSession(turns = []) {
  const id = globalThis.crypto?.randomUUID?.() ?? String(Date.now()) + Math.random().toString(16).slice(2);
  return { id, title: turns[0]?.user.slice(0, 80) ?? "", updated_at: new Date().toISOString(), turns };
}

function loadSessions() {
  let saved = [];
  try {
    const data = JSON.parse(readStorage(localStorage, SESSIONS_KEY));
    saved = Array.isArray(data) ? data.filter(session => typeof session?.id === "string") : [];
  } catch {
    saved = [];
  }
  for (const session of saved) session.turns = validTurns(session.turns);
  // Keep a conversation saved by the previous single-session page.
  try {
    const legacy = validTurns(JSON.parse(sessionStorage.getItem(LEGACY_HISTORY_KEY)));
    sessionStorage.removeItem(LEGACY_HISTORY_KEY);
    if (legacy.length) {
      const session = newSession(legacy);
      saved.unshift(session);
      sessionStorage.setItem(ACTIVE_KEY, session.id);
    }
  } catch {
    // Ignore unreadable storage.
  }
  return saved.filter(session => session.turns.length);
}

function saveSessions() {
  sessions.sort((left, right) => right.updated_at.localeCompare(left.updated_at));
  sessions.splice(MAX_SESSIONS);
  for (;;) {
    try {
      localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions));
      return;
    } catch {
      // Drop the oldest other session when browser storage is full.
      const index = sessions.findLastIndex(session => session !== active);
      if (index < 0) return;
      sessions.splice(index, 1);
    }
  }
}

function rememberActive() {
  try {
    sessionStorage.setItem(ACTIVE_KEY, active.id);
  } catch {
    // The active session stays in memory.
  }
}

function formatSessionTime(value) {
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? "" : time.toLocaleString([], { dateStyle: "short", timeStyle: "short", hour12: false });
}

function renderSessions() {
  sessionEmpty.hidden = sessions.length > 0;
  sessionList.replaceChildren(...sessions.map(session => {
    const button = element("button", "session-item");
    button.type = "button";
    button.disabled = pending;
    button.title = session.title;
    button.toggleAttribute("aria-current", session === active);
    const turns = session.turns.length === 1 ? "1 message" : `${session.turns.length} messages`;
    button.append(
      element("span", "session-title", session.title || "Untitled chat"),
      element("span", "muted session-meta", `${formatSessionTime(session.updated_at)} · ${turns}`),
    );
    button.addEventListener("click", () => openSession(session));
    const item = element("li");
    item.append(button);
    return item;
  }));
}

function evidenceSources(message, evidence, activity) {
  const byId = new Map();
  if (!evidence?.length) return byId;
  const sources = element("details", "chat-sources");
  sources.append(element("summary", "", evidence.length === 1 ? "1 source" : `${evidence.length} sources`));
  const list = element("div", "result-list");
  let highlighted;
  for (const entry of evidence) {
    const details = element("details", "result-item evidence-item");
    details.append(element("summary", "", [entry.evidence_id, formatLocator(entry)].filter(Boolean).join(" · ")));
    if (entry.excerpt) {
      details.append(element("pre", "", entry.excerpt + (entry.excerpt_truncated ? "\n…" : "")));
    }
    if (activity && entry.tool_span_id) {
      const jump = element("button", "button subtle", "View Tool Call");
      jump.type = "button";
      jump.addEventListener("click", () => {
        activity.steps.open = true;
        activity.trace.highlightSpan(entry.tool_span_id, true);
      });
      const actions = element("div", "evidence-actions");
      actions.append(jump);
      details.append(actions);
    }
    list.append(details);
    byId.set(entry.evidence_id, () => {
      sources.open = true;
      details.open = true;
      highlighted?.classList.remove("evidence-target");
      details.classList.add("evidence-target");
      highlighted = details;
      details.scrollIntoView({ block: "center" });
      if (activity) activity.trace.highlightSpan(entry.tool_span_id);
    });
  }
  sources.append(list);
  message.append(sources);
  return byId;
}

function linkCitations(content, openers) {
  if (!openers.size) return;
  const walker = document.createTreeWalker(content, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (!node.parentElement.closest("pre, a, button") && /\bE\d+\b/.test(node.data)) nodes.push(node);
  }
  for (const node of nodes) {
    const parts = node.data.split(/\b(E\d+)\b/);
    const fragment = document.createDocumentFragment();
    parts.forEach((part, index) => {
      const open = index % 2 ? openers.get(part) : null;
      if (!open) {
        if (part) fragment.append(part);
        return;
      }
      const button = element("button", "citation-link", part);
      button.type = "button";
      button.title = "Show evidence " + part;
      button.addEventListener("click", open);
      fragment.append(button);
    });
    node.replaceWith(fragment);
  }
}

function appendMessage(role, text, evidence = [], activity = null) {
  const message = element("article", `chat-message chat-message-${role}`);
  const content = role === "assistant"
    ? renderChatReply(text, updateReplyLayout)
    : element("p", "chat-text", text);
  message.append(
    element("p", "chat-speaker", role === "user" ? "You" : "DHub Agent"),
    content,
  );
  if (role === "assistant") linkCitations(content, evidenceSources(message, evidence, activity));
  transcript.append(message);
  return message;
}

function appendActivity() {
  const container = element("div", "chat-activity");
  const planHost = element("div");
  const steps = element("details", "chat-steps");
  steps.open = true;
  steps.hidden = true;
  const header = element("summary");
  const count = element("span", "muted trace-count");
  header.append(element("span", "", "Investigation Trace"), count);
  const list = element("div", "trace-list chat-trace-list");
  steps.append(header, list);
  container.append(planHost, steps);
  transcript.append(container);
  const trace = createTraceView(list, count);
  const plan = createPlanView(planHost, id => {
    steps.open = true;
    trace.focusEvidence(id);
  });
  return { container, plan, steps, trace };
}

function setStatus(message = "", error = false) {
  feedback.textContent = message;
  feedback.classList.toggle("error-text", error);
  feedback.hidden = !message;
}

function updateControls() {
  input.readOnly = pending;
  send.disabled = pending || !input.value.trim();
  reset.disabled = pending || (!active.turns.length && !input.value);
  empty.hidden = pending || active.turns.length > 0;
  for (const button of sessionList.querySelectorAll(".session-item")) button.disabled = pending;
}

function scrollToLatest() {
  scrollArea.scrollTop = scrollArea.scrollHeight;
}

function isNearLatest() {
  return scrollArea.scrollHeight - scrollArea.scrollTop - scrollArea.clientHeight < 48;
}

function updateReplyLayout(update) {
  const follow = isNearLatest();
  update();
  if (follow) scrollToLatest();
}

function renderTranscript() {
  transcript.replaceChildren();
  for (const turn of active.turns) {
    appendMessage("user", turn.user);
    appendMessage("assistant", turn.assistant, turn.evidence);
  }
  updateControls();
  scrollToLatest();
}

function openSession(session) {
  if (pending || session === active) return;
  active = session;
  rememberActive();
  input.value = "";
  setStatus();
  renderTranscript();
  renderSessions();
  input.focus();
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  const message = input.value.trim();
  if (pending || !message) return;

  pending = true;
  const session = active;
  input.value = "";
  const userMessage = appendMessage("user", message);
  setStatus("Waiting for the agent…");
  updateControls();
  input.focus({ preventScroll: true });
  scrollToLatest();

  let activity;
  try {
    const history = session.turns.slice(-MAX_CONTEXT_TURNS).map(({ user, assistant }) => ({ user, assistant }));
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ message, history }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      throw new Error(typeof data?.detail === "string" ? data.detail : "Unable to send the message. Please try again.");
    }
    let reply;
    let evidence = [];
    await readEventStream(response, event => {
      if (event.type === "chat.error") throw new Error(event.detail);
      if (event.type === "chat.evidence") {
        evidence = Array.isArray(event.evidence) ? event.evidence : [];
        return;
      }
      if (event.type === "chat.completed") {
        reply = event.reply;
        return false;
      }
      const isPlan = event.type === "plan.updated";
      const isStep = traceTypes.has(event.type) && (event.agent_id !== "main" || event.type.startsWith("tool."));
      if (!isPlan && !isStep) return;
      const follow = isNearLatest();
      activity ??= appendActivity();
      if (isPlan) activity.plan.update(event.plan);
      if (isStep) {
        activity.steps.hidden = false;
        activity.trace.render(event);
      }
      setStatus("The agent is investigating…");
      if (follow) scrollToLatest();
    });
    if (typeof reply !== "string" || !reply.trim()) throw new Error("The reply was interrupted. Please try again.");
    const follow = isNearLatest();
    session.turns.push({ user: message, assistant: reply, evidence });
    session.title ||= message.slice(0, 80);
    session.updated_at = new Date().toISOString();
    if (!sessions.includes(session)) sessions.unshift(session);
    saveSessions();
    rememberActive();
    renderSessions();
    appendMessage("assistant", reply, evidence, activity);
    setStatus();
    if (follow) scrollToLatest();
  } catch (error) {
    const detail = error instanceof TypeError || error instanceof SyntaxError
      ? "Connection failed. Please try again." : (error.message || "Unable to send the message. Please try again.");
    if (activity) {
      activity.container.append(element("p", "chat-status error-text", detail));
    } else {
      userMessage.remove();
    }
    input.value = message;
    setStatus(detail, true);
  } finally {
    pending = false;
    updateControls();
  }
});

input.addEventListener("input", updateControls);
input.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!send.disabled) form.requestSubmit();
  }
});

reset.addEventListener("click", () => {
  if (pending) return;
  active = newSession();
  rememberActive();
  input.value = "";
  setStatus();
  renderTranscript();
  renderSessions();
  input.focus();
});

document.querySelector("#chat-example").addEventListener("click", event => {
  input.value = event.currentTarget.textContent;
  updateControls();
  form.requestSubmit();
});

renderSessions();
renderTranscript();

fetch("/api/health", { cache: "no-store" })
  .then(response => response.json())
  .then(({ chat_mode }) => {
    document.querySelector("#chat-mode").hidden = chat_mode !== "offline";
    scrollToLatest();
  })
  .catch(() => {});
