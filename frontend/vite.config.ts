import { defineConfig } from "vitest/config";
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

// Dev server proxies API and uploads to the FastAPI backend (python main.py, port 5680).
// changeOrigin stays false so the session cookie ("ms_session") is preserved across the proxy.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5273,
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
  // Unit tests cover the pure helpers (formatting, dates); the browser walk is Playwright's job.
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
  },
});
