// @vitest-environment jsdom
/**
 * Following a job, as BEHAVIOUR (E7 fix round, the review's m2): the real `useFollowedJob` mounted under
 * jsdom (`timeline/testing/mountHook`), its `api` answering from a scripted server that can be up, down (every
 * request fails to connect) or restarted (every job forgotten: a 404, and no job in flight). The intervals
 * are shortened (JOB_POLL_MS 20 ms, PROJECT_JOB_POLL_MS 40 ms) so an outage is a fraction of a second; the
 * rules are the same. Pinned here: adoption on load; the lost job (a 404) from poll to line; an outage for an
 * adopted job and for one the page started - at most MAX_POLL_FAILURES polls, the "Could not reach" line
 * kept, then the restart line once the server is back; the line a done job leaves; an ended job never turned
 * into a lost one; the Cancel rule and its error; the update's scope on the Settings page.
 */
import { act } from "react";
import { onlineManager } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api/client";
import { mountHook } from "../components/project/timeline/testing/mountHook";
import { LOST_JOB_MESSAGE, MAX_POLL_FAILURES } from "./jobs";
import { useFollowedJob, type FollowedJobOptions } from "./useFollowedJob";

vi.mock("./jobs", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./jobs")>()),
  JOB_POLL_MS: 20,
  PROJECT_JOB_POLL_MS: 40,
}));

vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  return { ...real, api: { ...real.api, get: vi.fn(), post: vi.fn() } };
});

type Phase = "up" | "down" | "restarted";
interface ServerJob {
  id: string;
  kind: string;
  status: string;
  progress: number;
  message: string;
  user_id: string | null;
  result?: unknown;
  error?: string | null;
  cancel_requested?: boolean;
}

const SCOPE = "/api/projects/p1/job";
const UPDATE_SCOPE = "/api/system/update/job";
const server = {
  phase: "up" as Phase,
  jobs: {} as Record<string, ServerJob>,
  /** The scope's job in flight, by the scope's URL. */
  active: {} as Record<string, string | null>,
  calls: [] as { path: string; method: string; phase: Phase }[],
  /** Finer than `phase`: the scope's route cannot be reached, or one job's poll cannot. */
  scopeDown: false,
  pollDown: new Set<string>(),
};

function reset() {
  server.phase = "up";
  server.jobs = {};
  server.active = {};
  server.calls = [];
  server.scopeDown = false;
  server.pollDown = new Set();
}

async function answer(method: string, path: string): Promise<unknown> {
  server.calls.push({ path, method, phase: server.phase });
  if (server.phase === "down") throw new TypeError("Failed to fetch");
  if (server.scopeDown && (path === SCOPE || path === UPDATE_SCOPE)) throw new TypeError("Failed to fetch");
  const polled = path.match(/^\/api\/jobs\/(\w+)$/);
  if (polled && server.pollDown.has(polled[1])) throw new TypeError("Failed to fetch");
  const lost = server.phase === "restarted";
  if (path === SCOPE || path === UPDATE_SCOPE) {
    const id = lost ? null : server.active[path] ?? null;
    const job = id ? server.jobs[id] : null;
    return { active_job: job ? { id: job.id, kind: job.kind, status: job.status, user_id: job.user_id } : null };
  }
  const cancel = path.match(/^\/api\/jobs\/(\w+)\/cancel$/);
  if (cancel && method === "POST") {
    const job = lost ? undefined : server.jobs[cancel[1]];
    if (!job) throw new ApiError(404, "Job not found.");
    if (job.user_id === "u2") throw new ApiError(403, "Only the user who started this job, or an admin, can cancel it.");
    job.cancel_requested = true;
    return { ...job };
  }
  const poll = path.match(/^\/api\/jobs\/(\w+)$/);
  if (poll) {
    const job = lost ? undefined : server.jobs[poll[1]];
    if (!job) throw new ApiError(404, "Job not found.");
    return { ...job };
  }
  throw new ApiError(404, `no route ${path}`);
}

beforeEach(() => {
  reset();
  vi.mocked(api.get).mockReset();
  vi.mocked(api.post).mockReset();
  vi.mocked(api.get).mockImplementation((path: string) => answer("GET", path) as never);
  vi.mocked(api.post).mockImplementation((path: string) => answer("POST", path) as never);
});

let mounted: { unmount: () => void } | null = null;
afterEach(() => {
  mounted?.unmount();
  mounted = null;
  onlineManager.setOnline(true);
});

function mountFollow(over: Partial<FollowedJobOptions> = {}) {
  const ended = vi.fn();
  const options: FollowedJobOptions = { activeUrl: SCOPE, viewer: { id: "u1", role: "editor" }, onEnded: ended, ...over };
  const m = mountHook(useFollowedJob, () => options);
  mounted = m;
  return { ...m, ended };
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Let `ms` of real time pass with React and React Query flushing as they go. */
async function pass(ms: number) {
  await act(async () => {
    await sleep(ms);
  });
}

/** Wait (up to `ms`) until `check` holds; fail with `what` otherwise. */
async function until(check: () => boolean, what: string, ms = 2000) {
  for (let waited = 0; waited < ms; waited += 10) {
    if (check()) return;
    await pass(10);
  }
  expect(check(), what).toBe(true);
}

const polls = (id: string, phase?: Phase) => server.calls.filter((c) => c.path === `/api/jobs/${id}` && c.method === "GET" && (!phase || c.phase === phase)).length;

function running(id: string, kind = "transcribe", user_id: string | null = "u2", scope = SCOPE): ServerJob {
  const job = { id, kind, status: "running", progress: 0.3, message: "Working…", user_id };
  server.jobs[id] = job;
  server.active[scope] = id;
  return job;
}

describe("adoption", () => {
  it("follows the scope's running job on load, whoever started it: the job, its kind, in flight", async () => {
    running("J", "revoice");
    const m = mountFollow();
    await until(() => m.current().job?.id === "J", "the project's running job is followed");
    expect(m.current().jobId).toBe("J");
    expect(m.current().activeKind).toBe("revoice");
    expect(m.current().jobActive).toBe(true);
    expect(m.current().notice).toBeNull();
  });

  it("follows nothing while the scope has no job in flight, and a job started elsewhere once it appears", async () => {
    const m = mountFollow();
    await pass(100);
    expect(m.current().jobId).toBeNull();
    expect(m.current().jobActive).toBe(false);
    running("J");
    await until(() => m.current().jobId === "J", "a job started in another tab is followed within a scope poll");
  });

  it("follows the Settings page's update the same way, from its own scope", async () => {
    running("U", "update", null, UPDATE_SCOPE);
    const m = mountFollow({ activeUrl: UPDATE_SCOPE });
    await until(() => m.current().job?.id === "U", "the update already running is followed on load");
    expect(m.current().activeKind).toBe("update");
  });
});

describe("a lost job (the server restarted: a 404)", () => {
  it("is polled once more, then let go: the restart line, nothing in flight, no further polls", async () => {
    running("J");
    const m = mountFollow();
    await until(() => m.current().job?.id === "J", "adopted");
    server.phase = "restarted";
    await until(() => m.current().notice !== null, "the page notices the lost job");
    expect(m.current().notice).toEqual({ kind: "transcribe", reason: "lost", text: LOST_JOB_MESSAGE });
    expect(m.current().jobId).toBeNull();
    expect(m.current().jobActive).toBe(false);
    const after = polls("J", "restarted");
    expect(after).toBe(1);
    await pass(200);
    expect(polls("J", "restarted"), "never polled again").toBe(1);
  });
});

describe("an outage (every request fails to connect)", () => {
  it("an ADOPTED job: at most MAX_POLL_FAILURES polls, the line kept, the job not taken back from the stale answer", async () => {
    running("J");
    const m = mountFollow();
    await until(() => m.current().job?.id === "J", "adopted");
    server.phase = "down";
    await until(() => m.current().notice?.reason === "unreached", "the page gives up on the job");
    expect(m.current().notice?.text).toContain("Could not reach the job (Failed to fetch)");
    // The scope's last good answer still names J; it must not be followed again from it.
    await pass(400);
    expect(polls("J", "down"), "no more than the attempts of one give-up").toBeLessThanOrEqual(MAX_POLL_FAILURES);
    expect(m.current().jobId).toBeNull();
    expect(m.current().jobActive).toBe(false);
    expect(m.current().notice?.reason, "the line stays").toBe("unreached");

    // The server is back, restarted: J is asked after once more, answers 404, and the restart line replaces it.
    server.phase = "restarted";
    await until(() => m.current().notice?.reason === "lost", "the restart line once the server answers again");
    expect(m.current().notice?.text).toBe(LOST_JOB_MESSAGE);
    expect(polls("J", "restarted")).toBe(1);
  });

  it("an adopted job still running when the server comes back is followed again", async () => {
    running("J");
    const m = mountFollow();
    await until(() => m.current().job?.id === "J", "adopted");
    server.phase = "down";
    await until(() => m.current().notice?.reason === "unreached", "given up");
    server.phase = "up";
    await until(() => m.current().jobId === "J" && m.current().jobActive, "followed again from a newer answer");
    expect(m.current().notice).toBeNull();
  });

  it("a second outage later on is handled like the first: the one more try comes back once the job answered", async () => {
    running("J");
    const m = mountFollow();
    await until(() => m.current().job?.id === "J", "adopted");
    for (const round of [1, 2]) {
      server.phase = "down";
      await until(() => m.current().notice?.reason === "unreached", `given up (outage ${round})`);
      server.phase = "up";
      await until(() => m.current().jobId === "J" && m.current().jobActive, `followed again (outage ${round})`);
      await until(() => polls("J", "up") > 0 && m.current().job?.status === "running", `answered (outage ${round})`);
    }
  });

  it("a flap: followed again, the server drops before the job's poll answers - the job is followed again once it is back", async () => {
    running("J");
    const m = mountFollow();
    await until(() => m.current().job?.id === "J", "adopted");
    server.scopeDown = true;
    server.pollDown.add("J");
    await until(() => m.current().notice?.reason === "unreached", "given up (outage 1)");
    server.scopeDown = false; // the scope answers again; J's poll still cannot connect
    await until(() => m.current().jobId === "J", "followed again from the newer answer");
    server.scopeDown = true; // the server drops again before J's poll answered
    await until(() => m.current().notice?.reason === "unreached" && m.current().jobId === null, "given up (outage 2)");
    server.scopeDown = false;
    server.pollDown.clear(); // fully back, J still running
    await until(() => m.current().jobId === "J" && m.current().job?.status === "running", "followed again after the flap");
    expect(m.current().notice).toBeNull();
  });

  it("a poll the SERVER refuses while its scope answers is followed again once, then left with its line: no loop", async () => {
    running("J");
    const m = mountFollow();
    await until(() => m.current().job?.id === "J", "adopted");
    const refuse = vi.mocked(api.get).getMockImplementation()!;
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === "/api/jobs/J") {
        server.calls.push({ path, method: "GET", phase: server.phase });
        return Promise.reject(new ApiError(500, "Internal Server Error")) as never;
      }
      return refuse(path);
    });
    await until(() => m.current().notice?.reason === "unreached", "given up (refused)");
    await pass(600);
    expect(polls("J"), "one give-up, one more try, one give-up - then nothing").toBeLessThanOrEqual(1 + 2 * MAX_POLL_FAILURES);
    expect(m.current().jobId).toBeNull();
    expect(m.current().notice?.text).toContain("Internal Server Error");
  });

  it("a job the page STARTED: at most MAX_POLL_FAILURES polls, then the restart line once the server is back", async () => {
    const m = mountFollow();
    await pass(60);
    server.jobs.K = { id: "K", kind: "transcribe", status: "running", progress: 0.1, message: "Transcribing…", user_id: "u1" };
    server.active[SCOPE] = "K";
    act(() => m.current().follow("K", "transcribe"));
    await until(() => m.current().job?.id === "K", "the started job is polled");
    server.phase = "down";
    await until(() => m.current().notice?.reason === "unreached", "given up");
    await pass(300);
    expect(polls("K", "down")).toBeLessThanOrEqual(MAX_POLL_FAILURES);
    server.phase = "restarted";
    await until(() => m.current().notice?.reason === "lost", "the restart line once the server answers again");
    expect(polls("K", "restarted")).toBe(1);
  });
});

describe("a job that ends", () => {
  it("is handed over once and let go; a cancelled one leaves its closing line", async () => {
    const job = running("R", "revoice", "u1");
    const m = mountFollow();
    await until(() => m.current().job?.id === "R", "adopted");
    Object.assign(job, {
      status: "done", progress: 1, message: "Re-voice cancelled before the new video was written; the project is as it was.",
      result: { cancelled: true, stage: "synthesis" },
    });
    server.active[SCOPE] = null;
    await until(() => m.current().jobId === null, "a done job is let go");
    expect(m.current().notice).toEqual({
      kind: "revoice", reason: "ended", text: "Re-voice cancelled before the new video was written; the project is as it was.",
    });
    expect(m.ended).toHaveBeenCalledTimes(1);
    expect(m.ended.mock.calls[0][0]).toMatchObject({ id: "R", status: "done" });
    await pass(150);
    expect(m.ended).toHaveBeenCalledTimes(1);
  });

  it("a transcription that dropped adjustments leaves its count; one that dropped none leaves nothing", async () => {
    const job = running("T", "transcribe", "u1");
    const m = mountFollow();
    await until(() => m.current().job?.id === "T", "adopted");
    Object.assign(job, {
      status: "done", progress: 1, message: "Transcribed 2 segments; the adjustments on 3 sentences were dropped",
      result: { segments: 2, adjustments_dropped: 3 },
    });
    server.active[SCOPE] = null;
    await until(() => m.current().notice !== null, "the count is kept");
    expect(m.current().notice?.text).toBe("Transcribed 2 segments; the adjustments on 3 sentences were dropped");

    const next = running("T2", "transcribe", "u1");
    await until(() => m.current().jobId === "T2", "the next job is followed");
    expect(m.current().notice, "the next job replaces the line").toBeNull();
    Object.assign(next, { status: "done", progress: 1, message: "Transcribed 2 segments", result: { adjustments_dropped: 0 } });
    server.active[SCOPE] = null;
    await until(() => m.current().jobId === null, "let go");
    expect(m.current().notice).toBeNull();
  });

  it("an errored job stays followed with its error, and is never turned into a lost one after a restart", async () => {
    const job = running("E", "revoice", "u1");
    const m = mountFollow();
    await until(() => m.current().job?.id === "E", "adopted");
    Object.assign(job, { status: "error", message: "Re-voice failed", error: "Re-voice failed" });
    server.active[SCOPE] = null;
    await until(() => m.current().job?.status === "error", "the error arrives");
    expect(m.current().jobId).toBe("E");
    expect(m.ended).toHaveBeenCalledTimes(1);

    server.phase = "restarted";
    const before = polls("E");
    // A reconnect does not refetch it behind the page's back...
    act(() => {
      onlineManager.setOnline(false);
      onlineManager.setOnline(true);
    });
    await pass(150);
    expect(polls("E"), "no refetch on reconnect").toBe(before);
    // ...and a refetch that does happen (a 404 now) leaves the ended job as it was, with no restart line.
    await act(async () => {
      await m.qc.refetchQueries({ queryKey: ["job", "E"] });
    });
    await pass(60);
    expect(m.current().notice).toBeNull();
    expect(m.current().job?.status).toBe("error");
    expect(m.current().jobId).toBe("E");
  });
});

describe("defensive rules", () => {
  it("hands an ended job to onEnded once, however often its ended state is read again", async () => {
    const job = running("E", "revoice", "u1");
    const m = mountFollow();
    await until(() => m.current().job?.id === "E", "adopted");
    Object.assign(job, { status: "error", message: "Re-voice failed", error: "Re-voice failed" });
    server.active[SCOPE] = null;
    await until(() => m.current().job?.status === "error", "the error arrives");
    expect(m.ended).toHaveBeenCalledTimes(1);
    // The errored job stays followed; its state read again (a new answer) must not hand it over again.
    for (const message of ["Re-voice failed (1)", "Re-voice failed (2)"]) {
      job.message = message;
      await act(async () => {
        await m.qc.refetchQueries({ queryKey: ["job", "E"] });
      });
      await until(() => m.current().job?.message === message, "the new answer is read");
    }
    expect(m.ended).toHaveBeenCalledTimes(1);
  });

  it("does not ask the scope while the job it follows is in flight", async () => {
    running("J");
    const m = mountFollow();
    await until(() => m.current().job?.id === "J" && m.current().jobActive, "adopted");
    await pass(60);
    const scopeCalls = () => server.calls.filter((c) => c.path === SCOPE).length;
    const before = scopeCalls();
    await pass(400); // ten scope intervals
    expect(scopeCalls(), "the scope pauses while a job is in flight").toBe(before);
    expect(polls("J")).toBeGreaterThan(5);
  });
});

describe("Cancel", () => {
  it("is offered to the job's starter and an administrator, and to no one else", async () => {
    running("J", "revoice", "u2");
    const other = mountFollow({ viewer: { id: "u1", role: "editor" } });
    await until(() => other.current().job?.id === "J", "adopted");
    expect(other.current().mayCancel, "read-only for a viewer who did not start it").toBe(false);
    other.unmount();
    mounted = null;

    const admin = mountFollow({ viewer: { id: "a1", role: "admin" } });
    await until(() => admin.current().job?.id === "J", "adopted");
    expect(admin.current().mayCancel).toBe(true);
    admin.unmount();
    mounted = null;

    const starter = mountFollow({ viewer: { id: "u2", role: "editor" } });
    await until(() => starter.current().job?.id === "J", "adopted");
    expect(starter.current().mayCancel).toBe(true);
  });

  it("asks the server to stop the followed job and shows the flag at once; its error goes with the job", async () => {
    running("J", "revoice", "u1");
    const m = mountFollow();
    await until(() => m.current().job?.id === "J", "adopted");
    act(() => m.current().cancel());
    await until(() => m.current().job?.cancel_requested === true, "the card reads Cancelling… at once");
    expect(server.calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual(["/api/jobs/J/cancel"]);

    // A refused Cancel's error belongs to its job: the next job starts without it.
    server.jobs.J.user_id = "u2";
    act(() => m.current().cancel());
    await until(() => m.current().cancelError !== null, "the refusal is shown");
    server.jobs.J.status = "done";
    server.active[SCOPE] = null;
    await until(() => m.current().jobId === null, "the job ends");
    running("J2", "revoice", "u1");
    await until(() => m.current().jobId === "J2", "the next job is followed");
    expect(m.current().cancelError).toBeNull();
  });
});
