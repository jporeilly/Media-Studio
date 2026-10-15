/**
 * "Leaving the page with unsaved changes asks first" - for in-app navigation, for Back / Forward, and for
 * the window where the browser lets a page ask.
 *
 * In-app: the app routes with `BrowserRouter`, which has no `useBlocker` (that needs a data router), so
 * the router's navigator - the history object every `navigate()` and `<Link>` goes through - has its
 * `push` and `replace` wrapped while there are unsaved changes: the wrapper asks (the app's own confirm
 * dialog, never the browser's, which the desktop shell swallows) and carries the navigation out only on a
 * yes. The originals are put back the moment the guard comes down, the page unmounts, or `release()` is
 * called (the page calls it before a navigation that must not be asked about: its still is gone).
 *
 * Back / Forward (a `pop`): while dirty, a SENTINEL entry is pushed under the page's own URL, so Back
 * lands on the same page. The `popstate` listener pushes a fresh sentinel AT ONCE, before it asks, so the
 * user always stands one entry above the real one while the dialog is open: a second Back, or a Forward,
 * only pops to the real entry and asks again, never past the page. Cancel therefore needs nothing; Leave
 * goes back two - past the fresh sentinel and the real entry - to the entry the user wanted. The entry's
 * state keeps whatever the router stored there (`idx`), so the router's own bookkeeping is undisturbed.
 *
 * The window: a `beforeunload` guard - a browser's reload or closed tab asks through the browser's own
 * prompt. The desktop shell's window close never consults the page (capture.rs: the host window closes
 * without asking the webview), so closing the app does NOT ask - the guide says so. The shell's recording
 * close-guard (CaptureContext) is a different thing and is not touched.
 */
import { useCallback, useContext, useEffect, useRef } from "react";
import { UNSAFE_NavigationContext } from "react-router-dom";

/** The history entry's mark - generic, since the guard serves the annotation editor and the walkthrough builder alike. */
export const SENTINEL = "unsaved-changes";

type HistoryState = Record<string, unknown> | null;

function pushSentinel(): void {
  const state = (window.history.state ?? null) as HistoryState;
  window.history.pushState({ ...(state ?? {}), sentinel: SENTINEL }, "", window.location.href);
}

/** Returns `release`: takes every guard down at once, synchronously, for a navigation that must not ask. */
export function useLeaveGuard(when: boolean, ask: () => Promise<boolean>): () => void {
  const { navigator } = useContext(UNSAFE_NavigationContext);
  const askRef = useRef(ask);
  askRef.current = ask;
  const releaseRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (!when) return;
    const push = navigator.push, replace = navigator.replace;
    let released = false;
    const unload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    const onPop = () => {
      // Re-arm first, synchronously: the pop landed on the real entry, so put a sentinel back on top before
      // the dialog opens - another Back or a Forward while it is open then pops to the real entry and asks
      // again, and the page never unmounts without a yes.
      pushSentinel();
      void askRef.current().then((ok) => {
        if (ok) {
          release();
          window.history.go(-2);
        }
      });
    };
    const release = () => {
      if (released) return;
      released = true;
      navigator.push = push;
      navigator.replace = replace;
      window.removeEventListener("beforeunload", unload);
      window.removeEventListener("popstate", onPop);
    };
    navigator.push = (...args: Parameters<typeof push>) => {
      void askRef.current().then((ok) => { if (ok) { release(); push.apply(navigator, args); } });
    };
    navigator.replace = (...args: Parameters<typeof replace>) => {
      void askRef.current().then((ok) => { if (ok) { release(); replace.apply(navigator, args); } });
    };
    window.addEventListener("beforeunload", unload);
    const state = (window.history.state ?? null) as HistoryState;
    if (!state || state.sentinel !== SENTINEL) pushSentinel();
    window.addEventListener("popstate", onPop);
    releaseRef.current = release;
    return () => {
      release();
      if (releaseRef.current === release) releaseRef.current = null;
    };
  }, [when, navigator]);

  return useCallback(() => releaseRef.current?.(), []);
}
