import { renderChatReply } from "./chat-render/markdown.js";

const HISTORY_KEY = "dhub.chat.history.v1";
const MAX_CONTEXT_TURNS = 12;
const form = document.querySelector("#chat-form");
const input = document.querySelector("#chat-input");
const send = document.querySelector("#chat-send");
const reset = document.querySelector("#new-chat");
const scrollArea = document.querySelector("#chat-scroll");
const transcript = document.querySelector("#chat-history");
const empty = document.querySelector("#chat-empty");
const feedback = document.querySelector("#chat-status");
const turns = loadTurns();
let pending = false;

function loadTurns() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(HISTORY_KEY));
    return Array.isArray(saved) ? saved.filter(turn =>
      typeof turn?.user === "string" && typeof turn.assistant === "string"
    ) : [];
  } catch {
    return [];
  }
}

function saveTurns() {
  try {
    sessionStorage.setItem(HISTORY_KEY, JSON.stringify(turns));
  } catch {
    // Keep the active conversation in memory if storage is unavailable.
  }
}

function appendMessage(role, text) {
  const message = element("article", `chat-message chat-message-${role}`);
  const content = role === "assistant"
    ? renderChatReply(text, updateReplyLayout)
    : element("p", "chat-text", text);
  message.append(
    element("p", "chat-speaker", role === "user" ? "You" : "DHub Agent"),
    content,
  );
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
  reset.disabled = pending || (!transcript.childElementCount && !input.value);
  empty.hidden = pending || turns.length > 0;
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

form.addEventListener("submit", async event => {
  event.preventDefault();
  const message = input.value.trim();
  if (pending || !message) return;

  pending = true;
  input.value = "";
  const userMessage = appendMessage("user", message);
  setStatus("Waiting for the agent…");
  updateControls();
  input.focus({ preventScroll: true });
  scrollToLatest();

  let activity;
  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ message, history: turns.slice(-MAX_CONTEXT_TURNS) }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      throw new Error(typeof data?.detail === "string" ? data.detail : "Unable to send the message. Please try again.");
    }
    let reply;
    await readEventStream(response, event => {
      if (event.type === "chat.error") throw new Error(event.detail);
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
    turns.push({ user: message, assistant: reply });
    saveTurns();
    appendMessage("assistant", reply);
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
  turns.length = 0;
  saveTurns();
  transcript.replaceChildren();
  input.value = "";
  setStatus();
  updateControls();
  input.focus();
});

document.querySelector("#chat-example").addEventListener("click", event => {
  input.value = event.currentTarget.textContent;
  updateControls();
  form.requestSubmit();
});

for (const turn of turns) {
  appendMessage("user", turn.user);
  appendMessage("assistant", turn.assistant);
}
updateControls();
scrollToLatest();

fetch("/api/health", { cache: "no-store" })
  .then(response => response.json())
  .then(({ chat_mode }) => {
    document.querySelector("#chat-mode").hidden = chat_mode !== "offline";
    scrollToLatest();
  })
  .catch(() => {});
