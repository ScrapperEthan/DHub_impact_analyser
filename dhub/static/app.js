"use strict";

const roles = {
  main: "主 Agent",
  domain: "业务调查",
  code: "代码调查",
};

const statuses = {
  queued: "等待执行",
  running: "运行中",
  succeeded: "已完成",
  partial: "部分完成",
  failed: "失败",
  timeout: "超时",
  supported: "已支持",
  partially_supported: "部分支持",
  change_required: "需要变更",
  confirmed: "有证据",
  potential: "潜在",
  unknown: "待确认",
};

const kinds = {
  process: "决策过程",
  business_rule: "业务规则",
  code: "代码",
  configuration: "配置",
  test: "测试",
  downstream: "下游",
  conflict: "冲突",
};

const effects = {
  direct_change: "直接变更",
  indirect_effect: "间接影响",
  verify: "需要核对",
};

const modes = {
  demo: "演示 · 合成资料",
  project: "项目资料",
};

const coverageLabels = {
  knowledge_snapshot_id: "知识快照",
  snapshot_id: "快照",
  knowledge: "知识资料",
  documents: "文档",
  repositories: "仓库",
  repo_id: "仓库",
  branch: "分支",
  commit_sha: "提交",
  available: "可用",
  codegraph_available: "调用关系可用",
  limitations: "限制",
  missing: "缺失资料",
  notes: "调查说明",
};

const form = document.getElementById("requirement-form");
const requirement = document.getElementById("requirement");
const submit = document.getElementById("submit");
const statusTag = document.getElementById("run-status");
const modeTag = document.getElementById("data-mode");
const runLabel = document.getElementById("run-id");
const notice = document.getElementById("notice");
const reportBody = document.getElementById("report");
const traceBody = document.getElementById("trace");
const traceCount = document.getElementById("trace-count");
const terminal = new Set(["succeeded", "partial", "failed"]);
let activeRun;

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

function setNotice(message = "") {
  notice.textContent = message;
  notice.hidden = !message;
}

function errorMessage(error) {
  if (error && typeof error === "object") {
    return asText(error.message || error.error_summary || error.summary || error.detail || error);
  }
  return asText(error);
}

async function fetchJson(path, options) {
  const response = await fetch(path, { cache: "no-store", ...options });
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload) {
    const detail = response.status === 404 ? "找不到该分析运行。" : errorMessage(payload?.detail ?? payload?.error);
    const error = new Error(detail || ("请求失败（HTTP " + response.status + "）"));
    error.status = response.status;
    throw error;
  }
  return payload;
}

function section(parent, title) {
  const group = element("section", "report-section");
  group.append(element("h3", "", title));
  parent.append(group);
  return group;
}

function formatList(value) {
  const list = element("ul", "bullet-list");
  const entries = Array.isArray(value)
    ? value.map((item) => ["", item])
    : Object.entries(value ?? {});

  for (const [key, item] of entries) {
    const name = key ? label(coverageLabels, key) : "";
    const row = element("li");
    if (item && typeof item === "object") {
      if (name) row.append(element("span", "", name));
      row.append(formatList(item));
    } else {
      const text = item === true ? "是" : item === false ? "否" : (item ?? "未知");
      row.textContent = name ? name + " · " + text : text;
    }
    list.append(row);
  }
  return list;
}

function formatLocator(evidence) {
  if (evidence.source_type === "code") {
    const lines = evidence.start_line
      ? " L" + evidence.start_line + (evidence.end_line > evidence.start_line ? "–" + evidence.end_line : "")
      : "";
    return [
      evidence.repo_id,
      (evidence.path || "") + lines,
      evidence.commit_sha ? "提交 " + evidence.commit_sha : "",
      evidence.symbol,
    ].filter(Boolean).join(" · ");
  }
  return [
    evidence.title || evidence.page_id,
    evidence.section,
    evidence.version ? "版本 " + evidence.version : "",
    evidence.feature_drop,
    evidence.title && evidence.page_id ? "页面 " + evidence.page_id : "",
  ].filter(Boolean).join(" · ");
}

function evidenceUrl(value) {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}

function highlightTrace(run, spanId, moveFocus = false) {
  const row = run.spans.get(spanId)?.row;
  if (!row) return false;
  run.highlightedSpan?.classList.remove("trace-highlight");
  row.classList.add("trace-highlight");
  row.open = true;
  run.highlightedSpan = row;
  if (moveFocus) {
    row.scrollIntoView({ block: "center" });
    row.querySelector("summary").focus({ preventScroll: true });
  }
  return true;
}

function evidenceLinks(parent, ids, entries, run) {
  if (!ids?.length) return;
  const group = element("div", "evidence-links");
  for (const id of ids) {
    const entry = entries.get(id);
    if (!entry) continue;
    const button = element("button", "button subtle evidence-link", "证据 " + id);
    button.type = "button";
    button.addEventListener("click", () => {
      run.highlightedEvidence?.classList.remove("evidence-target");
      const { evidence, details } = entry;
      details.open = true;
      details.classList.add("evidence-target");
      run.highlightedEvidence = details;
      details.scrollIntoView({ block: "center" });
      details.querySelector("summary").focus({ preventScroll: true });
      highlightTrace(run, evidence.tool_span_id);
    });
    group.append(button);
  }
  if (group.childElementCount) parent.append(group);
}

function renderReport(run, report, sourceSnapshot) {
  const content = document.createDocumentFragment();
  const entries = new Map();
  if (report.summary) content.append(element("p", "lead", report.summary));

  for (const evidence of report.evidence ?? []) {
    const details = element("details", "result-item evidence-item");
    const locator = formatLocator(evidence);
    details.append(element("summary", "", [evidence.evidence_id, locator].filter(Boolean).join(" · ")));
    if (evidence.excerpt) details.append(element("pre", "", asText(evidence.excerpt)));

    const actions = element("div", "evidence-actions");
    const url = evidenceUrl(evidence.source_url || evidence.commit_url);
    if (url) {
      const link = element("a", "button subtle", "打开来源");
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      actions.append(link);
    }

    if (evidence.tool_span_id) {
      const jump = element("button", "button subtle", "查看工具调用");
      jump.type = "button";
      jump.addEventListener("click", () => {
        const found = highlightTrace(run, evidence.tool_span_id, true);
        setNotice(found ? "" : "对应工具记录尚未加载。");
      });
      actions.append(jump);
    }
    if (actions.childElementCount) details.append(actions);
    entries.set(evidence.evidence_id, { evidence, details });
  }

  if (report.requirement_summary || report.assumptions?.length || report.current_support) {
    const group = section(content, "需求与现有能力");
    if (report.requirement_summary) group.append(element("p", "text-block", report.requirement_summary));
    if (report.current_support) {
      const support = report.current_support;
      const line = element("div", "inline-tags");
      if (support.status) line.append(tag(support.status));
      if (support.reason) line.append(element("span", "text-block", support.reason));
      if (line.childElementCount) group.append(line);
      evidenceLinks(group, support.evidence_ids, entries, run);
    }
    if (report.assumptions?.length) group.append(formatList(report.assumptions));
  }

  if (report.impact_items?.length) {
    const list = element("div", "result-list");
    const targets = new Map(report.impact_items.map((item) => [item.id, item.target]));
    for (const impact of report.impact_items) {
      const card = element("article", "result-item");
      card.append(element("p", "result-title", impact.target || impact.id));
      const tags = element("div", "inline-tags");
      if (impact.kind) tags.append(tag(impact.kind, kinds));
      if (impact.effect) tags.append(tag(impact.effect, effects));
      if (impact.status) tags.append(tag(impact.status));
      if (tags.childElementCount) card.append(tags);
      if (impact.reason) card.append(element("p", "text-block", impact.reason));
      if (impact.related_item_ids?.length) {
        const related = impact.related_item_ids.map((id) => targets.get(id) || id);
        card.append(element("p", "muted", "关联影响 · " + related.join("、")));
      }
      evidenceLinks(card, impact.evidence_ids, entries, run);
      list.append(card);
    }
    section(content, "影响项").append(list);
  }

  if (report.regression_scenarios?.length) {
    const list = element("div", "result-list");
    for (const scenario of report.regression_scenarios) {
      const card = element("article", "result-item");
      card.append(element("p", "result-title", scenario.name || "回归场景"));
      for (const [key, title] of [["given", "条件"], ["before", "当前预期"], ["proposed", "拟议预期"]]) {
        if (scenario[key] !== null && scenario[key] !== undefined && scenario[key] !== "") {
          card.append(element("p", "text-block", title + " · " + asText(scenario[key])));
        }
      }
      evidenceLinks(card, scenario.evidence_ids, entries, run);
      list.append(card);
    }
    section(content, "回归建议").append(list);
  }

  if (report.open_questions?.length) {
    section(content, "待确认").append(formatList(report.open_questions));
  }

  if (entries.size) {
    const list = element("div", "result-list");
    for (const { details } of entries.values()) list.append(details);
    section(content, "证据来源").append(list);
  }

  if (report.coverage || sourceSnapshot) {
    const group = section(content, "调查范围");
    if (typeof report.coverage === "string") {
      group.append(element("p", "text-block", report.coverage));
    } else if (report.coverage) {
      group.append(formatList(report.coverage));
    }
    if (sourceSnapshot) {
      const snapshot = element("details", "result-item evidence-item");
      snapshot.append(element("summary", "", "资料快照"));
      snapshot.append(formatList(sourceSnapshot));
      group.append(snapshot);
    }
  }
  reportBody.replaceChildren(content);
}

function renderSnapshot(run, snapshot) {
  const status = snapshot.status || "running";
  setTag(statusTag, status, label(statuses, status));
  setTag(modeTag, snapshot.data_mode, label(modes, snapshot.data_mode));
  if (!requirement.value && typeof snapshot.requirement === "string") {
    requirement.value = snapshot.requirement;
  }

  const signature = JSON.stringify([status, snapshot.report, snapshot.source_snapshot]);
  if (signature !== run.reportSignature) {
    run.reportSignature = signature;
    if (snapshot.report) {
      renderReport(run, snapshot.report, snapshot.source_snapshot);
    } else {
      const message = terminal.has(status) ? "本次运行暂无可展示的报告。" : "调查正在进行，报告完成后会显示在这里。";
      reportBody.replaceChildren(element("p", "muted empty-state", message));
    }
  }
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

function createTraceRow(run, event) {
  const row = element("details", "trace-row");
  const summary = element("summary");
  const body = element("div", "trace-detail");
  row.append(summary, body);

  const tool = event.type.startsWith("tool.");
  const parent = run.spans.get(event.parent_span_id);
  const depth = parent ? parent.depth + 1 : 0;
  row.style.setProperty("--depth", String(depth));
  run.traceEmpty.hidden = true;
  traceBody.append(row);
  if (tool) {
    run.toolCount += 1;
    traceCount.textContent = run.toolCount + " 次工具调用";
  }
  return { row, summary, body, depth, tool };
}

function renderTraceEvent(run, event) {
  if (!["tool.started", "tool.finished", "agent.started", "agent.finished"].includes(event.type)) {
    return;
  }
  if (!event.span_id || run.seen.has(event.seq)) return;
  run.seen.add(event.seq);

  let span = run.spans.get(event.span_id);
  if (!span) {
    span = createTraceRow(run, event);
    run.spans.set(event.span_id, span);
  }
  if (event.type.endsWith(".started")) span.started = event;
  if (event.type.endsWith(".finished")) span.finished = event;
  const current = span.finished || span.started;
  const agentId = current.agent_id || span.started?.agent_id;
  const agentName = label(roles, agentId);
  if (Object.hasOwn(roles, agentId)) span.row.classList.add("role-" + agentId);

  const name = span.tool
    ? current.tool_name || span.started?.tool_name || "工具调用"
    : current.stage || span.started?.stage || agentName || "Agent 任务";
  const status = current.status || "running";
  const detail = element("span", "trace-line");
  if (agentName) detail.append(element("span", "trace-role", agentName));
  detail.append(element("span", "trace-name", name), tag(status));
  const timing = [
    formatTime(span.started?.at || current.at),
    formatDuration(current.elapsed_ms),
  ].filter(Boolean).join(" · ");
  if (timing) detail.append(element("span", "trace-duration", timing));
  span.summary.replaceChildren(detail);

  span.body.replaceChildren();
  addTraceDetail(span.body, "参数摘要", span.started?.input_summary ?? current.input_summary);
  addTraceDetail(span.body, "结果摘要", current.output_summary);
  const error = [current.error_code, current.error_summary].filter(Boolean).join(" · ");
  if (error) span.body.append(element("p", "error-text text-block", error));
  if (!span.body.childElementCount) {
    span.body.append(element("p", "muted", span.tool && status === "running" ? "等待工具返回。" : "暂无展开详情。"));
  }
}

async function readEventPages(run) {
  for (let page = 0; page < 6; page += 1) {
    const payload = await fetchJson(run.path + "/events?after_seq=" + run.cursor, { signal: run.controller.signal });
    if (activeRun !== run) return true;
    const events = payload.events ?? [];
    if (!events.length) return true;
    for (const event of events) renderTraceEvent(run, event);
    const cursor = Math.max(run.cursor, Number(payload.next_after_seq) || 0, ...events.map((event) => event.seq));
    if (cursor <= run.cursor) return true;
    run.cursor = cursor;
  }
  return false;
}

async function pollRun(run) {
  let delay = 1000;
  try {
    const snapshot = await fetchJson(run.path, { signal: run.controller.signal });
    if (activeRun !== run) return;
    renderSnapshot(run, snapshot);
    const latest = await readEventPages(run);
    if (activeRun !== run) return;
    setNotice(errorMessage(snapshot.error));
    if (terminal.has(snapshot.status) && latest) {
      if (!run.spans.size) run.traceEmpty.textContent = "这次运行没有调查记录。";
      return;
    }
    if (!latest && terminal.has(snapshot.status)) delay = 0;
  } catch (error) {
    if (activeRun !== run || error.name === "AbortError") return;
    if (error.status === 404) {
      setTag(statusTag, "failed", "运行不可用");
      reportBody.replaceChildren(element("p", "muted empty-state", error.message));
      setNotice(error.message);
      return;
    }
    setNotice("读取运行记录失败，正在重试。 " + error.message);
    delay = 2000;
  }
  run.timer = setTimeout(() => pollRun(run), delay);
}

function openRun(id, status = "queued") {
  if (activeRun) {
    clearTimeout(activeRun.timer);
    activeRun.controller.abort();
  }
  const url = new URL(window.location.href);
  url.searchParams.set("run", id);
  window.history.replaceState(null, "", url);
  const run = {
    path: "/api/runs/" + encodeURIComponent(id),
    cursor: 0,
    toolCount: 0,
    seen: new Set(),
    spans: new Map(),
    controller: new AbortController(),
    traceEmpty: element("p", "muted empty-state", "等待调查记录。"),
  };
  activeRun = run;
  setTag(statusTag, status, label(statuses, status));
  setTag(modeTag, "", "");
  runLabel.textContent = "运行 " + id;
  setNotice();
  traceBody.replaceChildren(run.traceEmpty);
  traceCount.textContent = "0 次工具调用";
  reportBody.replaceChildren(element("p", "muted empty-state", "正在读取分析记录。"));
  void pollRun(run);
}

document.getElementById("fill-example").addEventListener("click", () => {
  requirement.value = "Mandatory outbound campaigns should no longer bypass the frequency cap, even when Allow mandate campaign override is enabled.";
  requirement.focus();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = requirement.value.trim();
  if (!text) {
    setNotice("请输入一条变更需求。");
    requirement.focus();
    return;
  }
  submit.disabled = true;
  setNotice();
  try {
    const created = await fetchJson("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ requirement: text }),
    });
    if (!created.run_id) throw new Error("接口未返回运行 ID。");
    openRun(created.run_id, created.status);
  } catch (error) {
    setNotice("提交失败。 " + error.message);
  } finally {
    submit.disabled = false;
  }
});

const requestedRun = new URLSearchParams(window.location.search).get("run");
if (requestedRun) openRun(requestedRun);
