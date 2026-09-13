/* Tests for the reply renderer. Run with: node tests/markdown.test.js
 *
 * This file is security-relevant: everything it renders is either model output
 * or file content, so the escaping guarantees are tested here, not assumed.
 */
global.window = {};
require("../web/markdown.js");
const { render } = window.JarvisMarkdown;

let failures = 0;

function check(name, condition) {
  if (condition) {
    console.log(`  PASS  ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${name}`);
  }
}

console.log("markdown renderer");

// --- tables: the reason this renderer exists ---------------------------------
const table = render(
  ["| Drive | Free |", "| --- | --- |", "| C: | 282 GB |", "| D: | 12 GB |"].join("\n")
);
check("renders a table element", table.includes("<table>"));
check("uses header cells", table.includes("<th>Drive</th>"));
check("renders body rows", table.includes("<td>282 GB</td>"));
check("wraps for horizontal scroll", table.includes("table-wrap"));
check("leaves no raw pipes", !table.includes("| Drive |"));

const notATable = render("a | b | c");
check("a bare pipe line is not a table", !notATable.includes("<table>"));

// --- lists -------------------------------------------------------------------
const bullets = render("- first\n- second");
check("renders bullet lists", bullets.includes("<ul><li>first</li><li>second</li></ul>"));

const numbered = render("1. first\n2. second");
check("renders ordered lists", numbered.includes("<ol><li>first</li>"));

// --- inline ------------------------------------------------------------------
check("renders bold", render("**loud**").includes("<strong>loud</strong>"));
check("renders inline code", render("use `ls`").includes("<code>ls</code>"));
check(
  "does not italicise inside a word",
  !render("file_name*x").includes("<em>")
);

// --- code blocks -------------------------------------------------------------
const fenced = render("```\nline one\nline two\n```");
check("renders fenced code", fenced.includes("<pre class=\"code\"><code>line one"));

// --- structure ---------------------------------------------------------------
check("renders headings", render("## Summary").includes("<h4>Summary</h4>"));
check("keeps paragraphs separate", render("one\n\ntwo").includes("<p>one</p><p>two</p>"));

// --- escaping: model output and file contents are untrusted ------------------
const script = render("<script>alert(1)</script>");
check("escapes script tags", !script.includes("<script"));

const image = render("<img src=x onerror=alert(1)>");
check("escapes img tags", !image.includes("<img"));

const inCell = render("| a |\n| --- |\n| <b>bold</b> |");
check("escapes markup inside table cells", !inCell.includes("<b>bold</b>"));

const inCode = render("```\n<script>x</script>\n```");
check("escapes markup inside code blocks", !inCode.includes("<script>x"));

const quote = render('say "hi" & <br>');
check("escapes quotes and ampersands", quote.includes("&quot;") && quote.includes("&amp;"));

// --- robustness --------------------------------------------------------------
check("handles empty input", render("") === "");
check("handles null input", render(null) === "");
const ragged = render("| a | b |\n| --- | --- |\n| only-one |");
check("survives a ragged table", ragged.includes("<table>"));

console.log(failures ? `\n${failures} failure(s)` : "\nall markdown tests passed");
process.exit(failures ? 1 : 0);
