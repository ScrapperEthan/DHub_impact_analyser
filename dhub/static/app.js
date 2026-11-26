"use strict";

const kinds = {
  process: "Process step",
  business_rule: "Business rule",
  code: "Code",
  configuration: "Configuration",
  test: "Test",
  downstream: "Downstream",
  conflict: "Conflict",
};

const effects = {
  direct_change: "Direct change",
  indirect_effect: "Indirect impact",
  verify: "Verify",
};

const modes = {
  demo: "Demo · Synthetic data",
  project: "Project data",
};

const coverageLabels = {
  knowledge_snapshot_id: "Knowledge snapshot",
  snapshot_id: "Snapshot",
  knowledge: "Knowledge sources",
  documents: "Documents",
  repositories: "Repositories",
  repo_id: "Repository",
  branch: "Branch",
  commit_sha: "Commit",
  available: "Available",
  codegraph_available: "CodeGraph available",
  limitations: "Limitations",
  missing: "Missing sources",
  notes: "Notes",
};

const form = document.getElementById("requirement-form");
const requirement = document.getElementById("requirement");
const submit = document.getElementById("submit");
const statusTag = document.getElementById("run-status");
const modeTag = document.getElementById("data-mode");
const runLabel = document.getElementById("run-id");
const notice = document.getElementById("notice");
const reportBody = document.getElementById("report");
const planHost = document.getElementById("plan");
const traceBody = document.getElementById("trace");
const traceCount = document.getElementById("trace-count");
const terminal = new Set(["succeeded", "partial", "failed"]);
let activeRun;

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
    const detail = response.status === 404 ? "Analysis run not found." : errorMessage(payload?.detail ?? payload?.error);
    const error = new Error(detail || `Request failed (HTTP ${response.status}).`);
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
      const text = item === true ? "Yes" : item === false ? "No" : (item ?? "Unknown");
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
  const row = run.trace.spans.get(spanId)?.row;
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
    const button = element("button", "button subtle evidence-link", "Evidence " + id);
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
      const link = element("a", "button subtle", "Open Source");
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      actions.append(link);
    }

    if (evidence.tool_span_id) {
      const jump = element("button", "button subtle", "View Tool Call");
      jump.type = "button";
      jump.addEventListener("click", () => {
        const found = highlightTrace(run, evidence.tool_span_id, true);
        setNotice(found ? "" : "The corresponding tool call is still loading.");
      });
      actions.append(jump);
    }
    if (actions.childElementCount) details.append(actions);
    entries.set(evidence.evidence_id, { evidence, details });
  }

  if (report.requirement_summary || report.assumptions?.length || report.current_support) {
    const group = section(content, "Request and Current Support");
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
        card.append(element("p", "muted", "Related impacts · " + related.join(", ")));
      }
      evidenceLinks(card, impact.evidence_ids, entries, run);
      list.append(card);
    }
    section(content, "Impacts").append(list);
  }

  if (report.regression_scenarios?.length) {
    const list = element("div", "result-list");
    for (const scenario of report.regression_scenarios) {
      const card = element("article", "result-item");
      card.append(element("p", "result-title", scenario.name || "Regression scenario"));
      for (const [key, title] of [["given", "Conditions"], ["before", "Current behavior"], ["proposed", "Proposed behavior"]]) {
        if (scenario[key] !== null && scenario[key] !== undefined && scenario[key] !== "") {
          card.append(element("p", "text-block", title + " · " + asText(scenario[key])));
        }
      }
      evidenceLinks(card, scenario.evidence_ids, entries, run);
      list.append(card);
    }
    section(content, "Regression Scenarios").append(list);
  }

  if (report.open_questions?.length) {
    section(content, "Open Questions").append(formatList(report.open_questions));
  }

  if (entries.size) {
    const list = element("div", "result-list");
    for (const { details } of entries.values()) list.append(details);
    section(content, "Evidence").append(list);
  }

  if (report.coverage || sourceSnapshot) {
    const group = section(content, "Investigation Coverage");
    if (typeof report.coverage === "string") {
      group.append(element("p", "text-block", report.coverage));
    } else if (report.coverage) {
      group.append(formatList(report.coverage));
    }
    if (sourceSnapshot) {
      const snapshot = element("details", "result-item evidence-item");
      snapshot.append(element("summary", "", "Source Snapshot"));
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
      const message = terminal.has(status)
        ? "This run has no report."
        : "Investigation is underway. The report will appear here when it is ready.";
      reportBody.replaceChildren(element("p", "muted empty-state", message));
    }
  }
}

async function followRun(run) {
  const options = { signal: run.controller.signal, cache: "no-store" };
  try {
    let snapshot = await fetchJson(run.path, options);
    if (activeRun !== run) return;
    renderSnapshot(run, snapshot);
    setNotice();
    if (!run.finished) {
      const response = await fetch(run.path + "/stream?after_seq=" + run.cursor, options);
      if (!response.ok) throw new Error("Unable to open the investigation stream.");
      await readEventStream(response, event => {
        if (activeRun !== run) return false;
        if (event.seq <= run.cursor) return;
        if (event.type === "plan.updated") run.plan.update(event.plan);
        if (event.type === "run.started" && !terminal.has(snapshot.status)) {
          setTag(statusTag, "running", statuses.running);
        }
        run.trace.render(event);
        run.cursor = event.seq;
        if (event.type === "run.finished") {
          run.finished = true;
          return false;
        }
      });
    }
    if (activeRun !== run) return;
    if (!run.finished) throw new Error("The investigation stream disconnected.");
    if (!terminal.has(snapshot.status)) snapshot = await fetchJson(run.path, options);
    if (activeRun !== run) return;
    renderSnapshot(run, snapshot);
    setNotice(errorMessage(snapshot.error));
    if (!run.trace.spans.size) run.traceEmpty.textContent = "This run has no investigation steps.";
  } catch (error) {
    if (activeRun !== run || error.name === "AbortError") return;
    if (error.status === 404) {
      setTag(statusTag, "failed", "Run unavailable");
      reportBody.replaceChildren(element("p", "muted empty-state", error.message));
      setNotice(error.message);
      return;
    }
    setNotice("Unable to load the run. Retrying. " + error.message);
    run.timer = setTimeout(() => followRun(run), 2000);
  }
}

function openRun(id, status = "queued") {
  if (activeRun) {
    clearTimeout(activeRun.timer);
    activeRun.controller.abort();
  }
  const url = new URL(window.location.href);
  url.searchParams.set("run", id);
  window.history.replaceState(null, "", url);
  const traceEmpty = element("p", "muted empty-state", "Waiting for investigation steps.");
  const run = {
    path: "/api/runs/" + encodeURIComponent(id),
    cursor: 0,
    finished: false,
    controller: new AbortController(),
    traceEmpty,
    trace: createTraceView(traceBody, traceCount, traceEmpty),
    plan: createPlanView(planHost),
  };
  activeRun = run;
  setTag(statusTag, status, label(statuses, status));
  setTag(modeTag, "", "");
  runLabel.textContent = "Run " + id;
  setNotice();
  planHost.replaceChildren();
  traceBody.replaceChildren(traceEmpty);
  traceCount.textContent = "0 tool calls";
  reportBody.replaceChildren(element("p", "muted empty-state", "Loading the analysis run."));
  void followRun(run);
}

document.getElementById("fill-example").addEventListener("click", () => {
  requirement.value = "Mandatory outbound campaigns should no longer bypass the frequency cap, even when Allow mandate campaign override is enabled.";
  requirement.focus();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = requirement.value.trim();
  if (!text) {
    setNotice("Enter a change request.");
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
    if (!created.run_id) throw new Error("The server did not return a run ID.");
    openRun(created.run_id, created.status);
  } catch (error) {
    setNotice("Unable to start analysis. " + error.message);
  } finally {
    submit.disabled = false;
  }
});

const requestedRun = new URLSearchParams(window.location.search).get("run");
if (requestedRun) openRun(requestedRun);
