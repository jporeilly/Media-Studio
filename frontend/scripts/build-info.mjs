// Writes dist/build-info.json after `vite build`.
//
// frontend/dist is COMMITTED (the installed app is a git checkout that updates
// itself with `git pull`, so the built UI has to travel with the source). That
// only works while the committed build matches the committed source, so this
// records a fingerprint of every file that can change the bundle, and
// tests/test_frontend_dist.py recomputes it: a source change committed without
// a rebuild fails the test suite.
//
// The output is deterministic on purpose (no timestamps): rebuilding an
// unchanged tree leaves git clean, which the desktop staging gate relies on.
// Keep the file set and the hashing rule IDENTICAL to the Python side:
//   files  = frontend/src/** (minus *.test.ts / *.test.tsx, and minus any
//            testing/ folder - test-only helpers nothing outside a test
//            imports, so the bundle never carries them) + frontend/public/**
//            + index.html, package.json, package-lock.json, vite.config.ts,
//            tsconfig.json, tsconfig.node.json
//   hash   = sha256 over, for each file sorted by its forward-slash relative
//            path: path + "\0" + bytes-with-CRLF-normalised-to-LF + "\0"
import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url))); // frontend/
const EXTRA = ["index.html", "package.json", "package-lock.json", "vite.config.ts", "tsconfig.json", "tsconfig.node.json"];
const TEST_FILE = /\.test\.tsx?$/;
const TEST_DIR = /(^|\/)testing\//;

function walk(dir, out) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

function fingerprint() {
  const files = [];
  for (const d of ["src", "public"]) {
    const p = join(root, d);
    if (existsSync(p)) walk(p, files);
  }
  for (const f of EXTRA) {
    const p = join(root, f);
    if (existsSync(p)) files.push(p);
  }
  const rels = [...new Set(files.map((p) => relative(root, p).split("\\").join("/")))]
    .filter((rel) => !TEST_FILE.test(rel) && !TEST_DIR.test(rel))
    .sort();
  const h = createHash("sha256");
  for (const rel of rels) {
    // latin1 maps bytes 1:1 to chars, so the CRLF replacement is exact and binary-safe.
    const bytes = readFileSync(join(root, rel));
    const normalised = Buffer.from(bytes.toString("latin1").replace(/\r\n/g, "\n"), "latin1");
    h.update(Buffer.from(rel + "\0", "utf8"));
    h.update(normalised);
    h.update(Buffer.from("\0", "utf8"));
  }
  return { srcHash: h.digest("hex"), files: rels.length };
}

const info = fingerprint();
if (process.argv.includes("--print")) {
  console.log(JSON.stringify(info));
} else {
  const outDir = join(root, "dist");
  if (!existsSync(join(outDir, "index.html"))) {
    console.error("build-info: frontend/dist/index.html does not exist - run vite build first");
    process.exit(1);
  }
  writeFileSync(join(outDir, "build-info.json"), JSON.stringify(info, null, 2) + "\n");
  console.log(`build-info: ${info.files} source files fingerprinted (${info.srcHash.slice(0, 12)}...)`);
}
