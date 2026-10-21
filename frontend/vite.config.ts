import { defineConfig } from "vitest/config";
import type { Plugin, UserConfig } from "vite";
import react from "@vitejs/plugin-react";

/*
 * Production chunking: vendor chunks that change rarely, so page chunks stay small and the vendor files
 * cache well across releases. A module belongs to a group when its package matches the group's pattern,
 * or when it can only be reached through packages that do (unified, micromark and friends through
 * react-markdown and remark-gfm). The icon set is one chunk rather than dozens of tiny per-icon files
 * shared between lazily loaded pages. Anything else is left to Rollup.
 */
const VENDOR_GROUPS = [
  { name: "vendor-react", test: /\/node_modules\/(react|react-dom|react-router|react-router-dom|scheduler|@tanstack\/[^/]+)\// },
  { name: "vendor-markdown", test: /\/node_modules\/(react-markdown|remark-gfm)\// },
  { name: "vendor-icons", test: /\/node_modules\/lucide-react\// },
];

type ModuleInfo = { importers: readonly string[]; dynamicImporters: readonly string[] } | null;
type GetModuleInfo = (id: string) => ModuleInfo;

const caches = VENDOR_GROUPS.map(() => new Map<string, boolean>());

/** True when every import path to `id` passes through a package matching the group (or `id` matches itself). */
function reachedOnlyVia(id: string, group: (typeof VENDOR_GROUPS)[number], cache: Map<string, boolean>, getModuleInfo: GetModuleInfo): boolean {
  const cached = cache.get(id);
  if (cached !== undefined) return cached;
  if (group.test.test(id.replace(/\\/g, "/"))) { cache.set(id, true); return true; }
  cache.set(id, true); // provisional, so an import cycle does not disqualify the module
  const info = getModuleInfo(id);
  const importers = info ? [...info.importers, ...info.dynamicImporters] : [];
  const result = importers.length > 0 && importers.every((importer) => reachedOnlyVia(importer, group, cache, getModuleInfo));
  cache.set(id, result);
  return result;
}

function vendorChunk(id: string, getModuleInfo: GetModuleInfo): string | undefined {
  if (!id.replace(/\\/g, "/").includes("/node_modules/")) return undefined;
  for (let i = 0; i < VENDOR_GROUPS.length; i++) {
    if (reachedOnlyVia(id, VENDOR_GROUPS[i], caches[i], getModuleInfo)) return VENDOR_GROUPS[i].name;
  }
  return undefined;
}

/*
 * The interactive player bundle (W2c, C7): `vite build --mode player` builds src/player/main.tsx on its own -
 * one IIFE (a classic script, so a page opened from the disk runs it), the theme's CSS beside it - and this
 * plugin folds both into ONE page, dist/walkthrough-player.html, writing nothing else. The server fills the
 * page's data slot with a walkthrough (services/course_export.py: PLAYER_DATA_SLOT and PLAYER_TITLE_SLOT, the
 * two strings below), and it opens offline, the pictures beside it. `</script` and `</style` inside the code
 * are escaped so no string can end its element early.
 */
export const PLAYER_PAGE = "walkthrough-player.html";

function playerPage(js: string, css: string): string {
  const script = js.replace(/<\/script/gi, "<\\/script");
  const style = css.replace(/<\/style/gi, "<\\/style");
  return [
    "<!doctype html>",
    '<html lang="en">',
    "<head>",
    '<meta charset="UTF-8" />',
    '<meta name="viewport" content="width=device-width, initial-scale=1.0" />',
    "<title>Walkthrough</title>",
    `<style>${style}</style>`,
    "</head>",
    "<body>",
    '<div id="root"></div>',
    '<script type="application/json" id="walkthrough-data">null</script>',
    `<script>${script}</script>`,
    "</body>",
    "</html>",
    "",
  ].join("\n");
}

function inlinePlayer(): Plugin {
  return {
    name: "media-studio-inline-player",
    apply: "build",
    // After Vite's own plugins: the stylesheet is emitted by its CSS plugin in this same hook.
    enforce: "post",
    generateBundle(_options, bundle) {
      let js = "";
      let css = "";
      for (const [name, item] of Object.entries(bundle)) {
        if (item.type === "chunk") js += item.code;
        else if (name.endsWith(".css")) css += typeof item.source === "string" ? item.source : new TextDecoder().decode(item.source);
        delete bundle[name];
      }
      this.emitFile({ type: "asset", fileName: PLAYER_PAGE, source: playerPage(js, css) });
    },
  };
}

const playerConfig: UserConfig = {
  plugins: [react(), inlinePlayer()],
  publicDir: false,
  build: {
    outDir: "dist",
    emptyOutDir: false,
    sourcemap: false,
    cssCodeSplit: false,
    assetsInlineLimit: Number.MAX_SAFE_INTEGER,
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      input: "src/player/main.tsx",
      output: { format: "iife", inlineDynamicImports: true, entryFileNames: "player.js", assetFileNames: "player[extname]" },
    },
  },
};

// Dev server proxies API and uploads to the FastAPI backend (python main.py, port 5680).
// changeOrigin stays false so the session cookie ("ms_session") is preserved across the proxy.
// 5681 pairs with the 5680 backend; 5273 (the previous choice) collided with the Pentaho
// Content Editor's Vite on the same machine.
export default defineConfig(({ mode }) => (mode === "player" ? playerConfig : {
  plugins: [react()],
  server: {
    port: 5681,
    proxy: {
      "/api": { target: "http://127.0.0.1:5680", changeOrigin: false },
      "/uploads": { target: "http://127.0.0.1:5680", changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks: (id, { getModuleInfo }) => vendorChunk(id, getModuleInfo),
      },
    },
  },
  // Unit tests cover the pure helpers (formatting, dates) and — since E4b — a handful of SSR RENDERS of the
  // real components (`*.test.tsx`), for the things only markup can prove: which lanes the selection band
  // paints under each of the eight lock combinations, whether a banner is there at all, whether a button is
  // disabled. That gap is why an E3 regression could be re-introduced with all 258 unit tests still green.
  // `renderToStaticMarkup` needs no DOM, so the environment stays "node" and nothing was added to the
  // dependencies; the browser WALK is still Playwright's job and the live check is still the owner's.
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
    // Vitest blanks every `.css` request by default - a `?raw` import of the
    // stylesheet came back as "" - and one pin needs the stylesheet's text
    // (E5b, the Reviewer's MINOR 3: which element of a marker's flag takes
    // the pointer is CSS, which no static render can compute). With CSS
    // processed, Vite's own `?raw` handling gives the text; nothing under
    // test imports a stylesheet (only main.tsx does), so nothing else changes.
    css: true,
  },
}));
