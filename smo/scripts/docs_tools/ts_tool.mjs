// ts_tool.mjs - the TypeScript / JavaScript half of scripts/verify_docs_only.py and scripts/check_code_docs.py.
//
// Usage:  node ts_tool.mjs canon   < jobs.json   > out.json
//         node ts_tool.mjs docs    < jobs.json   > out.json
// Input is JSON on stdin: a list of {id, lang, text}; lang is one of ts, tsx, js, jsx. Output is a JSON object keyed by id.
//
// It parses with the parser that ships with the GUI's toolchain: `rolldown/parseAst` (Oxc), resolved from
// smo/gui/node_modules (override the directory with SMO_GUI_DIR). The plan named esbuild and the TypeScript compiler API, but
// neither is installed there: Vite 8 brought rolldown, and `typescript` is the native 7.x port without a JavaScript API.
//
//   canon: the syntax tree without positions, comments or cosmetic spelling, as a string. Two versions with equal text differ
//          in comments and layout only. Unlike an esbuild transform, the tree keeps the type annotations, so a changed
//          interface or type alias is a difference, as it should be. JSX: a `{/* comment */}` child and the whitespace-only
//          text between elements are dropped, since neither reaches the DOM.
//   docs:  for each top-level declaration, each `it(...)`/`test(...)` call and the file as a whole, whether a `/** */` (declarations)
//          or any comment (tests) sits directly above. A light parse: class members and nested functions are not listed.
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";

const here = dirname(fileURLToPath(import.meta.url));
const guiDir = process.env.SMO_GUI_DIR || resolve(here, "..", "..", "gui");
const req = createRequire(resolve(guiDir, "package.json"));
let parseAst;
try {
  ({ parseAst } = await import(pathToFileURL(req.resolve("rolldown/parseAst")).href));
} catch (e) {
  console.error(`cannot load rolldown/parseAst from ${guiDir}/node_modules (run npm ci in smo/gui): ${e.message}`);
  process.exit(3);
}

/** Positions and the spelling of literals are not part of the meaning of a program. */
function clean(node) {
  if (Array.isArray(node)) {
    return node.map(clean).filter((n) => n !== undefined);
  }
  if (node === null || typeof node !== "object") return node;
  if (node.type === "JSXExpressionContainer" && node.expression && node.expression.type === "JSXEmptyExpression") return undefined;
  if (node.type === "JSXText" && /^\s*$/.test(node.value) && /[\r\n]/.test(node.value)) return undefined;
  const out = {};
  for (const [k, v] of Object.entries(node)) {
    if (k === "start" || k === "end" || k === "range" || k === "loc") continue;
    if (k === "raw" && node.type === "Literal" && !node.regex && !node.bigint) continue;
    if (k === "raw" && node.type === "JSXText") continue;
    out[k] = clean(v);
  }
  return out;
}

function canon(text, lang) {
  return JSON.stringify(clean(parseAst(text, { lang })));
}

/** True when the text just before `pos` ends with a comment: a JSDoc block when `jsdocOnly`, otherwise a block or line comment. */
function commentAbove(text, pos, jsdocOnly) {
  let i = pos;
  while (i > 0 && /\s/.test(text[i - 1])) i--;
  if (text.slice(Math.max(0, i - 2), i) === "*/") {
    const open = text.lastIndexOf("/*", i - 2);
    return open >= 0 && (!jsdocOnly || text.startsWith("/**", open));
  }
  if (jsdocOnly) return false;
  const lineStart = text.lastIndexOf("\n", i - 1) + 1;
  return text.slice(lineStart, i).trimStart().startsWith("//");
}

const lineOf = (text, pos) => text.slice(0, pos).split("\n").length;

function isFunctionInit(init) {
  return init && (init.type === "ArrowFunctionExpression" || init.type === "FunctionExpression");
}

/** One entry per top-level declaration. `trivial`: at most five lines and one statement (or an expression-bodied arrow). */
function declItems(text, stmt, exported, at) {
  const items = [];
  const n = (s) => lineOf(text, s.end) - lineOf(text, s.start) + 1;
  const documented = commentAbove(text, at, true);
  const line = lineOf(text, stmt.start);
  const body1 = (b) => !b || b.type !== "BlockStatement" || b.body.length <= 1;
  if (stmt.type === "FunctionDeclaration" && stmt.id) {
    items.push({ name: stmt.id.name, kind: "func", line, lines: n(stmt), public: exported, trivial: n(stmt) <= 5 && body1(stmt.body), documented });
  } else if (stmt.type === "ClassDeclaration" && stmt.id) {
    items.push({ name: stmt.id.name, kind: "class", line, lines: n(stmt), public: exported, trivial: false, documented });
  } else if (stmt.type === "VariableDeclaration") {
    for (const d of stmt.declarations) {
      if (d.id.type === "Identifier" && isFunctionInit(d.init)) {
        items.push({ name: d.id.name, kind: "func", line, lines: n(stmt), public: exported, trivial: n(stmt) <= 5 && body1(d.init.body), documented });
      }
    }
  } else if (["TSInterfaceDeclaration", "TSTypeAliasDeclaration", "TSEnumDeclaration"].includes(stmt.type) && stmt.id) {
    items.push({ name: stmt.id.name, kind: "type", line, lines: n(stmt), public: exported, trivial: n(stmt) <= 1, documented });
  }
  return items;
}

/** The identifier a call chain starts from: `it` for `it.each([...])("x", fn)` and `it.skip("x", fn)`, null when it is not a plain chain. */
function calleeRoot(callee) {
  let c = callee;
  while (c) {
    if (c.type === "Identifier") return c.name;
    if (c.type === "MemberExpression") c = c.object;
    else if (c.type === "CallExpression") c = c.callee;
    else return null;
  }
  return null;
}

/** What check_code_docs.py needs about one file: the header flag, the declarations with their documentation, and the tests. */
function docs(text, lang) {
  const ast = parseAst(text, { lang });
  const items = [];
  const tests = [];
  const body = ast.body;
  // The file description: a /** */ comment before the first statement (the hashbang line is not a statement).
  let header = false;
  if (body.length === 0) {
    header = /\/\*\*[\s\S]*?\*\//.test(text);
  } else {
    header = /\/\*\*[\s\S]*?\*\//.test(text.slice(0, body[0].start));
    // A /** */ that is the first statement's own doc comment does not describe the file unless something else precedes it.
    if (header && body[0].type !== "ImportDeclaration" && commentAbove(text, body[0].start, true)) {
      const open = text.lastIndexOf("/**", body[0].start);
      header = /\/\*\*[\s\S]*?\*\//.test(text.slice(0, open));
    }
  }
  for (const stmt of body) {
    let decl = stmt;
    let exported = false;
    if (stmt.type === "ExportNamedDeclaration" || stmt.type === "ExportDefaultDeclaration") {
      exported = true;
      decl = stmt.declaration;
    }
    if (decl && !Array.isArray(decl)) items.push(...declItems(text, decl, exported, stmt.start));
  }
  // it(...) / test(...) calls at any depth: a test needs a comment above it (the string is the name; the comment says why it matters).
  const walk = (node) => {
    if (Array.isArray(node)) return node.forEach(walk);
    if (!node || typeof node !== "object") return;
    if (node.type === "ExpressionStatement" && node.expression && node.expression.type === "CallExpression") {
      const root = calleeRoot(node.expression.callee);
      if (root === "it" || root === "test") {
        const arg = node.expression.arguments[0];
        const name = arg && arg.type === "Literal" ? String(arg.value) : `test@${lineOf(text, node.start)}`;
        tests.push({ name, line: lineOf(text, node.start), documented: commentAbove(text, node.start, false) });
      }
    }
    for (const [k, v] of Object.entries(node)) if (k !== "start" && k !== "end") walk(v);
  };
  walk(body);
  return { header, items, tests, empty: body.length === 0 && !/\S/.test(text) };
}

const mode = process.argv[2];
const jobs = JSON.parse(readFileSync(0, "utf8"));
const out = {};
for (const job of jobs) {
  try {
    out[job.id] = mode === "canon" ? { canon: canon(job.text, job.lang) } : docs(job.text, job.lang);
  } catch (e) {
    out[job.id] = { error: String(e && e.message ? e.message : e).split("\n")[0] };
  }
}
process.stdout.write(JSON.stringify(out));
