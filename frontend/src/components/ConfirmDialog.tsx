import { useCallback, useRef, useState, type ReactNode } from "react";
import { Button, Modal } from "./ui";

/**
 * A confirmation the app draws itself, in place of the browser's `confirm()`.
 *
 * The native dialog is a WebView2 script dialog when the app runs in its
 * desktop shell, and there it was being swallowed: the Projects page's Delete
 * asked the browser's `confirm(...)`, the request to the server only fired on
 * a true, and the audit log showed that no delete had ever reached the backend
 * - the click simply did nothing, with nothing to tell the user why. The same
 * native dialog gated deactivating and reactivating an account and purging the
 * audit log. A dialog the app renders (`Modal`, already used by the slide
 * editor and the accounts card) cannot be swallowed by the host.
 *
 * Usage, once per component:
 *
 *   const { confirm, dialog } = useConfirm();
 *   ...
 *   onClick={async () => { if (await confirm({ title, message, confirmLabel, danger })) doIt(); }}
 *   ...
 *   {dialog}
 *
 * `confirm` resolves true on the confirm button (or Enter, since that button
 * takes focus), false on Cancel, Escape, the close button or a click on the
 * backdrop - exactly the answers the native dialog gave.
 */
export interface ConfirmAsk {
  title: ReactNode;
  message: ReactNode;
  /** The affirmative button's label. Say what will happen: "Delete", "Deactivate". */
  confirmLabel?: string;
  /** Draws the affirmative button in the danger style for a destructive act. */
  danger?: boolean;
}

export function useConfirm(): { confirm: (ask: ConfirmAsk) => Promise<boolean>; dialog: ReactNode } {
  const [ask, setAsk] = useState<ConfirmAsk | null>(null);
  const resolver = useRef<((ok: boolean) => void) | null>(null);

  const confirm = useCallback((next: ConfirmAsk) => new Promise<boolean>((resolve) => {
    // A second ask while one is open answers the first with false: the user
    // never saw it settle, and a promise left dangling would leave a handler
    // waiting for ever.
    resolver.current?.(false);
    resolver.current = resolve;
    setAsk(next);
  }), []);

  const settle = useCallback((ok: boolean) => {
    const resolve = resolver.current;
    resolver.current = null;
    setAsk(null);
    resolve?.(ok);
  }, []);

  const dialog = ask ? (
    <Modal
      open
      onClose={() => settle(false)}
      title={ask.title}
      width={440}
      footer={
        <>
          <Button onClick={() => settle(false)}>Cancel</Button>
          <Button variant={ask.danger ? "danger" : "primary"} onClick={() => settle(true)} autoFocus>
            {ask.confirmLabel ?? "OK"}
          </Button>
        </>
      }
    >
      <div style={{ lineHeight: 1.5 }}>{ask.message}</div>
    </Modal>
  ) : null;

  return { confirm, dialog };
}
