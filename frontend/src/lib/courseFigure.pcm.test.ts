// @vitest-environment node
/**
 * The figure Export to course writes, rendered by the content manager's REAL `MarkdownBody` (W2c, the review's
 * MINOR-1 / fix round K2). Its component is read in place from the sibling checkout (or `PCM_REPO`) - never written
 * to - and bundled here with Vite's own esbuild, its dependencies from the checkout's node_modules, into a
 * temporary folder of this machine; the bundle is rendered with the checkout's react-dom/server inside its
 * `ToolPanelProvider`, as the guide pane renders a guide. Without the checkout the block is skipped, and says why; a
 * checkout without the component or its packages fails, so a moved MarkdownBody cannot hide the contract as a skip.
 *
 * The TARGET - one numbered pin at the hotspot's centre (11.3 %, 8.9 %) and the caption, no visible hotspot list - is
 * a KNOWN FAILURE today (`it.fails`): the content manager's figure walk compares its children's types with "img" and
 * "figcaption" while react-markdown hands it its own `img` and `figcaption` overrides, so it draws a plain figure
 * (the picture, the pin's text as a numbered list, the caption). The day the content manager is fixed this test
 * goes red: take the `.fails` off.
 */
import { afterAll, beforeAll, describe, expect, it } from "vitest";

// Node's own modules and the checkout's packages: imported by name at run time, untyped (no Node typings here).
type Require = ((id: string) => unknown) & { resolve(id: string): string };
const nodeModule = (name: string): Promise<unknown> => import(/* @vite-ignore */ `node:${name}`);
const fs = (await nodeModule("fs")) as {
  existsSync(path: string): boolean; readFileSync(path: string, encoding: "utf8"): string; mkdtempSync(prefix: string): string;
  mkdirSync(path: string, options: { recursive: boolean }): void; writeFileSync(path: string, data: string): void;
  rmSync(path: string, options: { recursive: boolean; force: boolean }): void;
};
const { join, resolve } = (await nodeModule("path")) as { join(...parts: string[]): string; resolve(...parts: string[]): string };
const { fileURLToPath, pathToFileURL } = (await nodeModule("url")) as { fileURLToPath(url: string): string; pathToFileURL(path: string): URL };
const { createRequire } = (await nodeModule("module")) as { createRequire(path: string): Require };
const { tmpdir } = (await nodeModule("os")) as { tmpdir(): string };
const env = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env ?? {};

const FRONTEND = resolve(fileURLToPath(import.meta.url), "..", "..", "..");
const REPO = resolve(FRONTEND, "..");
const GOLDEN = fs.readFileSync(join(REPO, "tests", "fixtures", "course_export", "figure.golden.md"), "utf8").replace(/\r\n/g, "\n");
const PCM = env.PCM_REPO || resolve(REPO, "..", "Pentaho-Content-Manager");
const MARKDOWN_BODY = join(PCM, "src", "components", "MarkdownBody.tsx");
const PACKAGES = ["react", "react-dom", "react-markdown", "rehype-raw"];
// Skipped only when the checkout itself is not there; a checkout without the component or its packages FAILS (a
// moved or renamed MarkdownBody must not hide the contract as a skip - recheck NIT-4).
const PCM_PRESENT = fs.existsSync(PCM);
if (!PCM_PRESENT) console.warn(`courseFigure.pcm.test: skipped - the content manager's checkout is not at ${PCM} (set PCM_REPO)`);

/** The entry the bundle is built from: the real component and its provider, rendered to static markup. */
const ENTRY = `
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MarkdownBody } from "./components/MarkdownBody";
import { ToolPanelProvider } from "./components/ToolPanelContext";
export function render(body) {
  const md = createElement(MarkdownBody, { body, baseUrl: "asset://course/lab/" });
  return renderToStaticMarkup(createElement(ToolPanelProvider, { onShowPanel: () => {}, course: null }, md));
}
`;

let workDir = "";
let html = "";

describe.skipIf(!PCM_PRESENT)("the exported figure through the content manager's real MarkdownBody", () => {
  beforeAll(async () => {
    const missing = [MARKDOWN_BODY, ...PACKAGES.map((m) => join(PCM, "node_modules", m))].filter((p) => !fs.existsSync(p));
    if (missing.length) {
      throw new Error(`The content manager's checkout is at ${PCM} but lacks ${missing.join(", ")}: the contract cannot be checked (npm install there, or point PCM_REPO at a full checkout).`);
    }
    const esbuild = createRequire(join(FRONTEND, "package.json"))("esbuild") as {
      build(options: Record<string, unknown>): Promise<{ outputFiles: { text: string }[] }>;
    };
    const built = await esbuild.build({
      stdin: { contents: ENTRY, resolveDir: join(PCM, "src"), loader: "tsx", sourcefile: "contract-entry.tsx" },
      bundle: true, platform: "node", format: "esm", write: false, jsx: "automatic", logLevel: "silent",
      nodePaths: [join(PCM, "node_modules")],
      loader: { ".css": "empty", ".png": "empty", ".svg": "empty", ".woff": "empty", ".woff2": "empty" },
      define: { "import.meta.env": '{"DEV":false,"PROD":true}', "process.env.NODE_ENV": '"production"' },
      banner: { js: 'import { createRequire as __msRequire } from "node:module"; const require = __msRequire(import.meta.url);' },
    });
    // Under a node_modules folder of the temporary directory, so the test runner loads it as it is.
    workDir = fs.mkdtempSync(join(tmpdir(), "ms-w2c-contract-"));
    const bundle = join(workDir, "node_modules", "pcm-markdown-body", "bundle.mjs");
    fs.mkdirSync(join(workDir, "node_modules", "pcm-markdown-body"), { recursive: true });
    fs.writeFileSync(bundle, built.outputFiles[0].text);
    const mod = (await import(/* @vite-ignore */ pathToFileURL(bundle).href)) as { render(body: string): string };
    html = mod.render(GOLDEN);
  }, 120_000);

  afterAll(() => {
    if (workDir) fs.rmSync(workDir, { recursive: true, force: true });
  });

  it("renders the figure: our picture with its alt text, and the markers stay invisible", () => {
    expect(html).toContain("wt-01-first-aaaaaaaaaaaa-bbbbbbbbbbbb.png");
    expect(html).toContain('alt="Click &quot;Run&quot; &amp; &lt;wait&gt; for O&#x27;Brien&#x27;s [FIELD] rows."');
    expect(html).not.toContain("media-studio: walkthrough");
  });

  // KNOWN FAILURE until the content manager renders annotated figures (its figure walk matching its own `img` and
  // `figcaption` overrides): then this passes, `it.fails` turns red, and the `.fails` comes off.
  it.fails("draws one pin at the hotspot's centre and keeps the caption (the content manager's annotated image)", () => {
    const pins = [...html.matchAll(/class="pcm-annotated__pin[^"]*"[^>]*style="left:([0-9.]+)%;top:([0-9.]+)%"/g)].map((m) => [m[1], m[2]]);
    expect(pins).toEqual([["11.3", "8.9"]]);
    expect(html).toContain("<figcaption>3. Run it &amp; check</figcaption>");
    expect(html).not.toContain('class="pcm-annotated__hotspots"');
  });
});
