/* A small, deliberately incomplete markdown renderer.
 *
 * Jarvis asks the model for "a compact markdown list or table when several
 * values are involved", so tables arrive constantly and must render. Anything
 * the model does not realistically emit is out of scope.
 *
 * Security: the input is escaped *first* and every transform afterwards emits
 * only tags this file controls. No user or model text ever reaches innerHTML
 * unescaped.
 */
(() => {
  "use strict";

  function escapeHtml(text) {
    return String(text ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /** Bold, italic and inline code, applied inside already-escaped text. */
  function inline(text) {
    return text
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
  }

  const isTableRow = (line) => /^\s*\|.*\|\s*$/.test(line);
  const isTableDivider = (line) => /^\s*\|[\s:|-]+\|\s*$/.test(line);

  function splitRow(line) {
    return line
      .trim()
      .replace(/^\||\|$/g, "")
      .split("|")
      .map((cell) => inline(cell.trim()));
  }

  function renderTable(lines, start) {
    const header = splitRow(lines[start]);
    const rows = [];
    let index = start + 2; // skip the header and the divider
    while (index < lines.length && isTableRow(lines[index])) {
      rows.push(splitRow(lines[index]));
      index += 1;
    }
    const head = header.map((cell) => `<th>${cell}</th>`).join("");
    const body = rows
      .map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join("")}</tr>`)
      .join("");
    // Tables can be wider than a phone; give them their own scroll container.
    const html =
      `<div class="table-wrap"><table><thead><tr>${head}</tr></thead>` +
      `<tbody>${body}</tbody></table></div>`;
    return [html, index];
  }

  function renderList(lines, start, ordered) {
    const pattern = ordered ? /^\s*\d+[.)]\s+(.*)$/ : /^\s*[-*•]\s+(.*)$/;
    const items = [];
    let index = start;
    while (index < lines.length && pattern.test(lines[index])) {
      items.push(`<li>${inline(lines[index].match(pattern)[1])}</li>`);
      index += 1;
    }
    const tag = ordered ? "ol" : "ul";
    return [`<${tag}>${items.join("")}</${tag}>`, index];
  }

  function render(markdown) {
    const source = escapeHtml(markdown).replace(/\r\n/g, "\n");
    const lines = source.split("\n");
    const out = [];
    let index = 0;
    let paragraph = [];

    const flush = () => {
      if (paragraph.length) {
        out.push(`<p>${inline(paragraph.join("<br>"))}</p>`);
        paragraph = [];
      }
    };

    while (index < lines.length) {
      const line = lines[index];

      if (/^\s*```/.test(line)) {
        flush();
        const code = [];
        index += 1;
        while (index < lines.length && !/^\s*```/.test(lines[index])) {
          code.push(lines[index]);
          index += 1;
        }
        index += 1; // closing fence
        out.push(`<pre class="code"><code>${code.join("\n")}</code></pre>`);
        continue;
      }

      if (isTableRow(line) && isTableDivider(lines[index + 1] || "")) {
        flush();
        const [html, next] = renderTable(lines, index);
        out.push(html);
        index = next;
        continue;
      }

      if (/^\s*[-*•]\s+/.test(line)) {
        flush();
        const [html, next] = renderList(lines, index, false);
        out.push(html);
        index = next;
        continue;
      }

      if (/^\s*\d+[.)]\s+/.test(line)) {
        flush();
        const [html, next] = renderList(lines, index, true);
        out.push(html);
        index = next;
        continue;
      }

      const heading = line.match(/^\s*(#{1,4})\s+(.*)$/);
      if (heading) {
        flush();
        const level = Math.min(heading[1].length + 2, 6);
        out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
        index += 1;
        continue;
      }

      if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
        flush();
        out.push("<hr>");
        index += 1;
        continue;
      }

      if (!line.trim()) {
        flush();
        index += 1;
        continue;
      }

      paragraph.push(line.trim());
      index += 1;
    }

    flush();
    return out.join("");
  }

  window.JarvisMarkdown = { render, escapeHtml };
})();
