// Markdown rendering for model output. `marked` and `DOMPurify` are classic
// scripts loaded before the modules (see index.html).

export const escapeHtml = (s) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Full Markdown via marked, then DOMPurify so LLM output can never inject
// scripts/handlers. Links open in a new tab; only http(s)/mailto allowed.
marked.use({ gfm: true, breaks: true });
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});
const PURIFY_OPTS = {
  ALLOWED_TAGS: ["p", "br", "strong", "em", "del", "code", "pre", "blockquote", "ul", "ol", "li",
    "h1", "h2", "h3", "h4", "h5", "h6", "a", "hr", "table", "thead", "tbody", "tr", "th", "td", "span"],
  ALLOWED_ATTR: ["href", "title", "class", "align", "target", "rel"],
  ALLOWED_URI_REGEXP: /^(?:https?:|mailto:)/i,
};

// Tables and code blocks get a horizontal scroll wrapper so only they scroll,
// with a persistent scrollbar plus a right-edge fade while more content exists.
export function renderMarkdown(text) {
  const tpl = document.createElement("template");
  tpl.innerHTML = DOMPurify.sanitize(marked.parse(text || ""), PURIFY_OPTS);
  for (const node of tpl.content.querySelectorAll("table, pre")) {
    const wrap = document.createElement("div");
    wrap.className = "scroll-x";
    const inner = document.createElement("div");
    inner.className = "scroll-x-inner";
    node.replaceWith(wrap);
    wrap.append(inner);
    inner.append(node);
  }
  return tpl.innerHTML;
}

export function updateScrollHint(inner) {
  const more = inner.scrollWidth - inner.clientWidth - inner.scrollLeft > 2;
  inner.parentElement.classList.toggle("more-right", more);
}

export function updateScrollHints(root) {
  for (const inner of root.querySelectorAll(".scroll-x-inner")) updateScrollHint(inner);
}
