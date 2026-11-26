const FLOWCHART_NODE = /^[^\s(=]+(?:\([^\r\n)]*\))?[ \t]*=>[ \t]*\w/;
const RENDER_TIMEOUT = 12_000;
let frameReady;

function diagramKind(code) {
  const language = [...code.classList].find(name => name.startsWith("language-"));
  switch (language?.slice("language-".length).toLowerCase()) {
    case "mermaid": return "mermaid";
    case "flow":
    case "flowchart.js": return "flowchart";
    case "flowchart": return FLOWCHART_NODE.test(code.textContent.trimStart()) ? "flowchart" : "mermaid";
    default: return null;
  }
}

function diagramFrame() {
  frameReady ??= new Promise(resolve => {
    const frame = document.createElement("iframe");
    frame.className = "chat-diagram-frame";
    frame.src = new URL("./frame.html", import.meta.url).href;
    frame.title = "Diagram renderer";
    frame.tabIndex = -1;
    frame.setAttribute("aria-hidden", "true");
    frame.sandbox.add("allow-scripts");
    frame.addEventListener("load", () => resolve(frame.contentWindow), { once: true });
    document.body.append(frame);
  });
  return frameReady;
}

function requestDiagram(kind, source) {
  return new Promise(resolve => {
    const { port1, port2 } = new MessageChannel();
    const timeout = setTimeout(() => finish(null), RENDER_TIMEOUT);

    function finish(svg) {
      clearTimeout(timeout);
      port1.close();
      resolve(svg);
    }

    port1.onmessage = event => finish(event.data);
    // The sandbox has an opaque origin. The addressed frame replies on this private port.
    diagramFrame()
      .then(frame => frame.postMessage({ kind, source }, "*", [port2]))
      .catch(() => finish(null));
  });
}

async function diagramImage(kind, source) {
  const svg = await requestDiagram(kind, source);
  if (!svg) return null;

  const image = new Image();
  image.alt = kind === "mermaid" ? "Mermaid diagram" : "Flowchart";
  image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  await image.decode();

  const diagram = document.createElement("div");
  diagram.className = "chat-diagram";
  diagram.tabIndex = 0;
  diagram.setAttribute("role", "region");
  diagram.setAttribute("aria-label", image.alt);
  diagram.append(image);
  return diagram;
}

async function renderBlock(code, kind, updateLayout) {
  const block = code.parentElement;
  const diagram = await diagramImage(kind, code.textContent).catch(() => null);
  if (!block.isConnected) return;

  updateLayout(() => {
    if (diagram) {
      block.replaceWith(diagram);
    } else {
      const note = document.createElement("p");
      note.className = "chat-diagram-error";
      note.textContent = "Could not render this diagram. Source shown below.";
      block.before(note);
    }
  });
}

export function renderDiagrams(content, updateLayout) {
  for (const code of content.querySelectorAll("pre > code[class]")) {
    const kind = diagramKind(code);
    if (kind) void renderBlock(code, kind, updateLayout);
  }
}
