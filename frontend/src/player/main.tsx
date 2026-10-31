/**
 * The interactive player bundle's entry (W2c, C7): built on its own by `vite build --mode player` (vite.config.ts)
 * into ONE file, `dist/walkthrough-player.html` - this script and the theme's styles inline - which the server fills
 * with a walkthrough (`services/course_export.py`). It renders the same pure `Player` the app plays with, the
 * pictures read from the files beside the page; no router, no API, no network: it opens from the disk.
 */
import { createRoot } from "react-dom/client";
import { Player } from "../components/walkthrough/Player";
import { DATA_ELEMENT_ID, UNREADABLE_MESSAGE, bundleEmbedded, bundleImageUrl, bundleTheme, readBundle } from "../lib/playerBundle";
import "../styles/theme.css";

const root = document.getElementById("root");
const walkthrough = readBundle(document.getElementById(DATA_ELEMENT_ID)?.textContent);
document.documentElement.setAttribute("data-theme", bundleTheme(typeof window.matchMedia === "function" ? window.matchMedia.bind(window) : undefined));
if (walkthrough) document.title = walkthrough.name;
// B2: framed in a content manager's lesson (the export's figure holds the page in an iframe), or asked with ?embed=1,
// the player is embedded - no Close, a compact header, Escape leaves nothing. Download player's own tab is as before.
const embedded = bundleEmbedded(window);

if (root && !walkthrough) {
  root.setAttribute("data-player-unreadable", "");
  root.textContent = UNREADABLE_MESSAGE;
} else if (root && walkthrough) {
  // Close (the header's, Escape's, the end card's): a page opened on its own has nowhere to go back to; a window a
  // script opened is closed, any other stays on the end card. Embedded, nothing calls it.
  createRoot(root).render(<Player walkthrough={walkthrough} imageUrl={bundleImageUrl} onExit={() => window.close()} embedded={embedded} />);
}
