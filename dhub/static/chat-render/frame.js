"use strict";

const diagramFont = 'system-ui, "Microsoft YaHei", sans-serif';
let nextDiagram = 0;
let mermaidReady;
let flowchartReady;

function loadVendor(file) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `../vendor/${file}`;
    script.onload = resolve;
    script.onerror = reject;
    document.head.append(script);
  });
}

function serializeDiagram(svg) {
  if (svg?.localName !== "svg") throw new Error("The renderer returned no SVG.");
  const viewBox = svg.getAttribute("viewBox")?.trim().split(/[\s,]+/).map(Number);
  if (viewBox?.[2] > 0 && viewBox[3] > 0) {
    svg.setAttribute("width", viewBox[2]);
    svg.setAttribute("height", viewBox[3]);
  }
  return new XMLSerializer().serializeToString(svg);
}

async function renderMermaid(source, stage) {
  mermaidReady ??= loadVendor("mermaid-12.3.0.min.js").then(() => {
    const engine = window.mermaid;
    engine.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      suppressErrorRendering: true,
      htmlLabels: false,
      fontFamily: diagramFont,
    });
    return engine;
  });
  const engine = await mermaidReady;
  const { svg } = await engine.render(`chart-${++nextDiagram}`, source, stage);
  return new DOMParser().parseFromString(svg, "image/svg+xml").documentElement;
}

async function renderFlowchart(source, stage) {
  flowchartReady ??= loadVendor("flowchart-1.18.1.bundle.min.js");
  await flowchartReady;
  window.flowchart.parse(source).drawSVG(stage, { "font-family": diagramFont });
  return stage.querySelector("svg");
}

window.addEventListener("message", async event => {
  const port = event.ports[0];
  if (window.origin !== "null" || event.source !== window.parent || !port) return;

  const { kind, source } = event.data ?? {};
  if (typeof source !== "string" || (kind !== "mermaid" && kind !== "flowchart")) {
    port.postMessage(null);
    port.close();
    return;
  }

  const stage = document.createElement("div");
  document.body.append(stage);
  try {
    const render = kind === "mermaid" ? renderMermaid : renderFlowchart;
    port.postMessage(serializeDiagram(await render(source, stage)));
  } catch {
    port.postMessage(null);
  } finally {
    stage.remove();
    port.close();
  }
});
