// The player bundle opened OFFLINE from the disk (W2c, C7) - run by tests/test_course_export.py:
//
//   node tests/player_bundle_check.mjs <folder holding index.html>
//
// Opens <folder>/index.html as a file:// page in jsdom (the frontend's own dev dependency) with its inline
// scripts run and every resource request recorded and refused, waits for the player, reads the first step's
// label and picture, presses Next, reads the second's, and prints one JSON line with what it saw. Nothing is
// fetched from anywhere: a request at all is reported, and the test fails on it.
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(join(here, "..", "frontend", "package.json"));
const { JSDOM, VirtualConsole, requestInterceptor } = require("jsdom");

const folder = resolve(process.argv[2] || ".");
const requests = [];
const errors = [];
// What jsdom itself cannot do (CSS it does not parse, a method it does not implement) is noted, not a failure.
const notes = [];

const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (e) => {
  const line = `${e?.type ?? "error"}: ${e?.message ?? e}`;
  if (e?.type === "css-parsing" || e?.type === "not-implemented") notes.push(line);
  else errors.push(line);
});
virtualConsole.on("error", (...args) => errors.push(args.map(String).join(" ")));

const dom = await JSDOM.fromFile(join(folder, "index.html"), {
  url: pathToFileURL(join(folder, "index.html")).href,
  runScripts: "dangerously",
  // Every subresource request is recorded and answered 404 here: the page must make none.
  resources: {
    interceptors: [requestInterceptor((request) => {
      requests.push(request.url);
      return new Response("", { status: 404 });
    })],
  },
  pretendToBeVisual: true,
  virtualConsole,
});
const { document } = dom.window;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(test, ms = 5000) {
  for (let waited = 0; waited < ms; waited += 25) {
    if (test()) return true;
    await sleep(25);
  }
  return false;
}
const progress = () => document.querySelector("[data-player-progress]")?.textContent ?? null;
const picture = () => document.querySelector("[data-player-stage] img")?.src ?? null;
const shown = () => (document.querySelector("[data-player-tooltip]") ?? document.querySelector("[data-player-modal]"))?.textContent ?? null;

const out = { title: null, steps: [], unreadable: false, requests, errors, notes };
await until(() => progress() !== null || document.querySelector("[data-player-unreadable]") !== null);
out.unreadable = document.querySelector("[data-player-unreadable]") !== null;
out.title = document.title;
if (!out.unreadable) {
  out.steps.push({ progress: progress(), picture: picture(), shown: shown() });
  const first = progress();
  document.querySelector("[data-player-next]")?.click();
  await until(() => progress() !== first);
  out.steps.push({ progress: progress(), picture: picture(), shown: shown() });
}
console.log(JSON.stringify(out));
dom.window.close();
