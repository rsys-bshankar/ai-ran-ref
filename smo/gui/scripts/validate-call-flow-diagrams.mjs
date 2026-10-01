#!/usr/bin/env node
// Parses every mermaid code block in docs/call-flows/*.md with mermaid's
// own parser — the same check this build's own GitHub PR process has
// been running by hand from a throwaway scratch script every time a call
// flow doc changes (a bare `;` inside a Note/message, for one, breaks
// GitHub's sequence-diagram renderer without breaking anything a human
// skimming the markdown would notice). Wired into CI (smo-tests.yml's
// `call-flow-diagrams` job) so this is no longer a step someone has to
// remember to run locally.
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { JSDOM } from "jsdom";
import mermaid from "mermaid";

const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>");
global.window = dom.window;
global.document = dom.window.document;

const here = path.dirname(fileURLToPath(import.meta.url));
const callFlowsDir = path.resolve(here, "../../docs/call-flows");
const files = fs.readdirSync(callFlowsDir)
  .filter((f) => f.endsWith(".md"))
  .map((f) => path.join(callFlowsDir, f));

let failed = false;
let blockCount = 0;

for (const file of files) {
  const text = fs.readFileSync(file, "utf8");
  const matches = [...text.matchAll(/```mermaid\n([\s\S]*?)```/g)];
  for (let i = 0; i < matches.length; i++) {
    blockCount++;
    const diagram = matches[i][1];
    try {
      await mermaid.parse(diagram);
    } catch (e) {
      failed = true;
      console.error(`FAIL ${path.relative(process.cwd(), file)} [block ${i + 1}]: ${e.message}`);
    }
  }
}

if (failed) {
  process.exit(1);
}
console.log(`OK: ${blockCount} mermaid diagram(s) across ${files.length} call-flow doc(s) parsed clean.`);
