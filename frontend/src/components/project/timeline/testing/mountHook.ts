/**
 * Mount a hook for real, under jsdom (`// @vitest-environment jsdom` at the
 * top of the test that uses this, and nowhere else: the SSR render harness
 * beside it stays in the node environment). `react-dom/client` renders a
 * probe that calls the hook, `act` flushes its state, its effects and the
 * promises a mutation resolves through, and the test reads the hook's latest
 * return. No testing-library: this is thirty lines, and the hooks under test
 * take plain values, refs and setters.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

export interface Mounted<T> {
  /** The hook's return from the latest render. */
  current: () => T;
  /** Render again; the props thunk is read afresh, so a changed value reaches the hook. */
  rerender: () => void;
  /** Let every pending promise, batched notification and state update land. */
  settle: () => Promise<void>;
  unmount: () => void;
  qc: QueryClient;
}

export function mountHook<P, T>(useHook: (props: P) => T, props: () => P, qc?: QueryClient): Mounted<T> {
  const client = qc ?? new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  let latest: T | undefined;
  function Probe() {
    latest = useHook(props());
    return null;
  }
  const root: Root = createRoot(document.createElement("div"));
  const render = () => {
    act(() => {
      root.render(createElement(QueryClientProvider, { client }, createElement(Probe)));
    });
  };
  render();
  return {
    current: () => latest as T,
    rerender: render,
    settle: async () => {
      // Two macrotasks: react-query batches its notifications on one, and a
      // mutation's onSuccess sets state that lands on the next.
      for (let i = 0; i < 2; i++) {
        await act(async () => {
          await new Promise((resolve) => setTimeout(resolve, 0));
        });
      }
    },
    unmount: () => {
      act(() => root.unmount());
    },
    qc: client,
  };
}
