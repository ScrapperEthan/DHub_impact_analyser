"use strict";

const roles = { main: "Main Agent", domain: "Domain Investigator", code: "Code Investigator" };
const statuses = {
  queued: "Queued",
  running: "Running",
  succeeded: "Completed",
  partial: "Partial",
  failed: "Failed",
  timeout: "Timed out",
  supported: "Supported",
  partially_supported: "Partially supported",
  change_required: "Change required",
  confirmed: "Evidence-backed",
  potential: "Potential",
  unknown: "Unverified",
};
const taskStatuses = {
  pending: "Pending",
  in_progress: "In progress",
  completed: "Completed",
  blocked: "Blocked",
  skipped: "Skipped",
};
const traceTypes = new Set(["agent.started", "agent.finished", "tool.started", "tool.finished"]);

function element(tag, className = "", text = "") {
  const item = document.createElement(tag);
  item.className = className;
  item.textContent = text;
  return item;
}

function label(labels, value) {
  return Object.hasOwn(labels, value) ? labels[value] : (value ?? "");
}

function setTag(item, value, text) {
  item.className = "tag";
  if (typeof value === "string" && /^[a-z_]+$/.test(value)) {
    item.classList.add("tag-" + value);
  }
  item.textContent = text;
  item.hidden = !text;
}

function tag(value, labels = statuses) {
  const item = element("span");
  setTag(item, value, label(labels, value));
  return item;
}

function asText(value) {
  if (value === null || value === undefined) return "";
  return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
}

async function readEventStream(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        if (line.trim() && onEvent(JSON.parse(line)) === false) return;
      }
      if (done) {
        if (buffer.trim()) onEvent(JSON.parse(buffer));
        return;
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

function formatLocator(evidence) {
  if (evidence.source_type === "code") {
    const lines = evidence.start_line
      ? " L" + evidence.start_line + (evidence.end_line > evidence.start_line ? "–" + evidence.end_line : "")
      : "";
    return [
      evidence.repo_id,
      (evidence.path || "") + lines,
      evidence.commit_sha ? "Commit " + evidence.commit_sha : "",
      evidence.symbol,
    ].filter(Boolean).join(" · ");
  }
  return [
    evidence.title || evidence.page_id,
    evidence.section,
    evidence.version ? "Version " + evidence.version : "",
    evidence.feature_drop,
    evidence.title && evidence.page_id ? "Page " + evidence.page_id : "",
  ].filter(Boolean).join(" · ");
}

function evidenceLinks(parent, ids, openEvidence) {
  if (!ids?.length) return;
  const group = element("div", "evidence-links");
  for (const id of ids) {
    const button = element("button", "button subtle evidence-link", "Evidence " + id);
    button.type = "button";
    button.addEventListener("click", () => openEvidence(id));
    group.append(button);
  }
  parent.append(group);
}

function createPlanView(host, openEvidence) {
  const panel = element("details", "investigation-plan");
  panel.open = true;
  const summary = element("summary");
  const progress = element("span", "muted plan-progress");
  progress.setAttribute("aria-live", "polite");
  summary.append(element("span", "plan-title", "Investigation Plan"), progress);
  const body = element("div", "plan-body");
  const navigation = element("div", "plan-navigation");
  const previous = element("button", "button subtle", "Previous Version");
  const next = element("button", "button subtle", "Next Version");
  previous.type = next.type = "button";
  navigation.append(previous, next);
  panel.append(summary, body, navigation);

  const versions = [];
  let selected = 0;

  function render() {
    const plan = versions[selected];
    const completed = plan.tasks.filter(task => task.status === "completed").length;
    const latest = versions.at(-1).version;
    const version = plan.version === latest ? "v" + latest : `v${plan.version} / v${latest}`;
    progress.textContent = `${version} · ${completed}/${plan.tasks.length} complete`;
    const children = [element("p", "plan-goal", plan.goal), element("p", "muted plan-reason", plan.reason)];
    const scope = Object.entries(plan.scope || {}).map(([key, value]) => `${key} · ${value}`);
    if (scope.length) children.push(element("p", "muted plan-context", scope.join(" / ")));
    if (plan.candidate_steps?.length) {
      children.push(element("p", "muted plan-context", "Candidate steps · " + plan.candidate_steps.join(" / ")));
    }
    const tasks = element("ul", "plan-tasks");
    for (const task of plan.tasks) {
      const row = element("li", "plan-task");
      const description = element("div");
      description.append(
        element("p", "plan-question", task.question),
        element("p", "muted plan-context", `${task.id} · ${label(roles, task.agent)}`),
      );
      if (task.note) description.append(element("p", "plan-note", task.note));
      evidenceLinks(description, task.evidence_ids, openEvidence);
      row.append(description, tag(task.status, taskStatuses));
      tasks.append(row);
    }
    body.replaceChildren(...children, tasks);
    navigation.hidden = versions.length < 2;
    previous.disabled = selected === 0;
    next.disabled = selected === versions.length - 1;
  }

  previous.addEventListener("click", () => { selected -= 1; render(); });
  next.addEventListener("click", () => { selected += 1; render(); });

  return {
    update(plan) {
      if (!plan || plan.version <= (versions.at(-1)?.version || 0)) return;
      const following = !versions.length || selected === versions.length - 1;
      versions.push(plan);
      if (following) selected = versions.length - 1;
      render();
      if (!panel.parentElement) host.append(panel);
    },
  };
}

function formatTime(value) {
  if (!value) return "";
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? "" : time.toLocaleTimeString([], { hour12: false });
}

function formatDuration(value) {
  if (!Number.isFinite(value)) return "";
  return value < 1000 ? value + " ms" : (value / 1000).toFixed(2) + " s";
}

function addTraceDetail(parent, title, value) {
  if (value === null || value === undefined || value === "") return;
  const block = element("div");
  block.append(element("strong", "", title), element("pre", "", asText(value)));
  parent.append(block);
}

function createTraceView(list, count, placeholder) {
  const spans = new Map();
  const evidenceSpans = new Map();
  let highlightedSpan;
  let toolCount = 0;

  function createRow(event) {
    const row = element("details", "trace-row");
    const summary = element("summary");
    const body = element("div", "trace-detail");
    row.append(summary, body);
    const tool = event.type.startsWith("tool.");
    const parent = spans.get(event.parent_span_id);
    const depth = parent ? parent.depth + 1 : 0;
    row.style.setProperty("--depth", String(depth));
    if (placeholder) placeholder.hidden = true;
    list.append(row);
    if (tool) {
      toolCount += 1;
      count.textContent = toolCount === 1 ? "1 tool call" : `${toolCount} tool calls`;
    }
    return { row, summary, body, depth, tool };
  }

  function render(event) {
    if (!traceTypes.has(event.type) || !event.span_id) return;
    let span = spans.get(event.span_id);
    if (!span) {
      span = createRow(event);
      spans.set(event.span_id, span);
    }
    if (event.type.endsWith(".started")) span.started = event;
    if (event.type.endsWith(".finished")) {
      span.finished = event;
      for (const id of event.output_summary?.evidence_ids ?? []) evidenceSpans.set(id, event.span_id);
    }
    const current = span.finished || span.started;
    const agentId = current.agent_id || span.started?.agent_id;
    const agentName = label(roles, agentId);
    if (Object.hasOwn(roles, agentId)) span.row.classList.add("role-" + agentId);
    const name = span.tool
      ? current.tool_name || span.started?.tool_name || "Tool call"
      : current.stage || span.started?.stage || agentName || "Agent task";
    const status = current.status || "running";
    const detail = element("span", "trace-line");
    if (agentName) detail.append(element("span", "trace-role", agentName));
    detail.append(element("span", "trace-name", name), tag(status));
    if (current.error_code) detail.append(element("span", "trace-error", current.error_code));
    const timing = [
      formatTime(span.started?.at || current.at),
      formatDuration(current.elapsed_ms),
    ].filter(Boolean).join(" · ");
    if (timing) detail.append(element("span", "trace-duration", timing));
    span.summary.replaceChildren(detail);

    span.body.replaceChildren();
    addTraceDetail(span.body, "Input summary", span.started?.input_summary ?? current.input_summary);
    addTraceDetail(span.body, "Result summary", current.output_summary);
    const error = [current.error_code, current.error_summary].filter(Boolean).join(" · ");
    if (error) span.body.append(element("p", "error-text text-block", error));
    if (!span.body.childElementCount) {
      span.body.append(element("p", "muted", span.tool && status === "running" ? "Waiting for the tool result." : "No details available."));
    }
  }

  function highlightSpan(spanId, moveFocus = false) {
    const row = spans.get(spanId)?.row;
    if (!row) return false;
    highlightedSpan?.classList.remove("trace-highlight");
    row.classList.add("trace-highlight");
    row.open = true;
    highlightedSpan = row;
    if (moveFocus) {
      row.scrollIntoView({ block: "center" });
      row.querySelector("summary").focus({ preventScroll: true });
    }
    return true;
  }

  return {
    spans, render, highlightSpan,
    focusEvidence: id => highlightSpan(evidenceSpans.get(id), true),
  };
}
