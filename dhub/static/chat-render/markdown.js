import { renderDiagrams } from "./diagrams.js";

let parser;

function createParser() {
  const markdown = window.markdownit({ html: false, breaks: true });
  markdown.renderer.rules.table_open = () => '<div class="chat-table" tabindex="0" role="region" aria-label="Table"><table>';
  markdown.renderer.rules.table_close = () => '</table></div>\n';
  return markdown;
}

export function renderChatReply(text, updateLayout) {
  const content = document.createElement("div");
  if (typeof window.markdownit !== "function") {
    content.className = "chat-text";
    content.textContent = text;
    return content;
  }

  parser ??= createParser();
  content.className = "chat-markdown";
  content.innerHTML = parser.render(text);
  renderDiagrams(content, updateLayout);
  return content;
}
