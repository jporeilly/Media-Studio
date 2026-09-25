/**
 * The audition's three fakes (R1b, for `useAudition.test.ts`): a Web Audio
 * context that records every node it makes and every instruction a GainNode
 * is given, a `<video>` that records the writes the transport makes to it in
 * order, and a frame clock that drives `requestAnimationFrame` and
 * `performance.now()` by hand. Nothing here simulates timing on its own: a
 * test steps the clock, advances the context's time and reads what was
 * written. Test-only, like `mountHook.ts` beside it.
 */

export interface GainCall {
  method: "setValueAtTime" | "linearRampToValueAtTime";
  value: number;
  at: number;
}

/**
 * The `gain` AudioParam. It has NO `exponentialRampToValueAtTime`: the
 * audition must never call it (a target of 0 is a RangeError there, and
 * `fadePoints` ends every fade-out at 0), so a fake without the method makes
 * that mutation throw inside the effect rather than pass unnoticed.
 */
export class FakeGainParam {
  value = 1;
  calls: GainCall[] = [];
  setValueAtTime(value: number, at: number) { this.calls.push({ method: "setValueAtTime", value, at }); }
  linearRampToValueAtTime(value: number, at: number) { this.calls.push({ method: "linearRampToValueAtTime", value, at }); }
}

export class FakeGainNode {
  gain = new FakeGainParam();
  connectedTo: unknown = null;
  disconnected = 0;
  connect(to: unknown) { this.connectedTo = to; }
  disconnect() { this.disconnected += 1; }
}

export interface StartCall {
  when: number;
  offset: number | undefined;
  duration: number | undefined;
}

export interface FakeAudioBuffer {
  duration: number;
}

export class FakeBufferSource {
  buffer: FakeAudioBuffer | null = null;
  onended: (() => void) | null = null;
  connectedTo: unknown = null;
  starts: StartCall[] = [];
  stopped = 0;
  disconnected = 0;
  connect(to: unknown) { this.connectedTo = to; }
  disconnect() { this.disconnected += 1; }
  start(when: number, offset?: number, duration?: number) { this.starts.push({ when, offset, duration }); }
  stop() { this.stopped += 1; }
}

/** What the mocked `api.blob` answers with: a blob whose bytes "decode" (below) to `seconds`. */
export const blobOf = (seconds: number): Blob =>
  ({ arrayBuffer: async () => ({ seconds }) as unknown as ArrayBuffer }) as unknown as Blob;

export class FakeAudioContext {
  /** Every context made since the test reset this - the hook makes one, lazily, on the first Play. */
  static instances: FakeAudioContext[] = [];
  state: "suspended" | "running" | "closed" = "suspended";
  /** Advanced by the test, as the real clock runs. */
  currentTime = 0;
  destination = { node: "destination" };
  resumed = 0;
  closed = 0;
  sources: FakeBufferSource[] = [];
  gains: FakeGainNode[] = [];
  constructor() { FakeAudioContext.instances.push(this); }
  async resume() { this.resumed += 1; this.state = "running"; }
  async close() { this.closed += 1; this.state = "closed"; }
  createBufferSource() { const node = new FakeBufferSource(); this.sources.push(node); return node; }
  createGain() { const node = new FakeGainNode(); this.gains.push(node); return node; }
  /** A "decode" that reads the seconds the fake blob carries (`blobOf`). */
  async decodeAudioData(bytes: ArrayBuffer): Promise<FakeAudioBuffer> {
    return { duration: (bytes as unknown as { seconds: number }).seconds };
  }
  /** Every `start(` any source of this context was given, in the order the sources were made. */
  get starts(): StartCall[] { return this.sources.flatMap((node) => node.starts); }
}

/** The transport's `<video>`: `paused`, the two writable properties and the two methods, every write logged in order. */
export class FakeVideo {
  paused = true;
  /** `play`, `pause`, `currentTime=<seconds>`, `playbackRate=<rate>`, in the order the transport wrote them. */
  writes: string[] = [];
  private time = 0;
  private rate = 1;
  get currentTime() { return this.time; }
  set currentTime(value: number) { this.time = value; this.writes.push(`currentTime=${value}`); }
  get playbackRate() { return this.rate; }
  set playbackRate(value: number) { this.rate = value; this.writes.push(`playbackRate=${value}`); }
  async play() { this.paused = false; this.writes.push("play"); }
  pause() { this.paused = true; this.writes.push("pause"); }
  /** The seeks alone, as seconds. */
  get seeks(): number[] { return this.writes.filter((w) => w.startsWith("currentTime=")).map((w) => Number(w.slice("currentTime=".length))); }
  /** The rate writes alone. */
  get rates(): number[] { return this.writes.filter((w) => w.startsWith("playbackRate=")).map((w) => Number(w.slice("playbackRate=".length))); }
}

/**
 * `requestAnimationFrame` by hand: a frame runs only when the test steps the
 * clock, once per step, and a frame that re-arms waits for the next step -
 * so a loop is exactly one armed frame at a time, and a test can count them.
 */
export class FrameClock {
  /** Milliseconds, what `performance.now()` answers. */
  now = 0;
  private queue = new Map<number, FrameRequestCallback>();
  private nextId = 1;
  request = (callback: FrameRequestCallback): number => {
    const id = this.nextId++;
    this.queue.set(id, callback);
    return id;
  };
  cancel = (id: number): void => { this.queue.delete(id); };
  /** How many frames are armed right now - one loop is one. */
  get armed(): number { return this.queue.size; }
  /** Advance by `dtMs` and run every frame that was armed before the step. */
  step(dtMs: number): void {
    this.now += dtMs;
    const due = [...this.queue.values()];
    this.queue.clear();
    for (const callback of due) callback(this.now);
  }
}
