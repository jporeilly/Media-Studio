import { describe, it, expect } from "vitest";
import { ApiError } from "../api/client";
import {
  JOB_POLL_MS,
  LOST_JOB_MESSAGE,
  MAX_POLL_FAILURES,
  cardForJob,
  isLostJob,
  isServerAnswer,
  jobPollInterval,
  jobToAdopt,
  jobToAskAgain,
  lineToKeep,
  mayCancelJob,
  noticeCard,
  pollFailureMessage,
  retryJobPoll,
  wasCancelled,
  type ActiveJob,
  type FollowState,
} from "./jobs";

const NOT_FOUND = new ApiError(404, "Job not found.");
const SERVER_ERROR = new ApiError(500, "Internal Server Error");
const OFFLINE = new TypeError("Failed to fetch");

/** Drive `retryJobPoll` as React Query does for one poll: attempt, and retry while it says so. */
function attempts(errors: unknown[]): number {
  let failuresBefore = 0;
  for (const error of errors) {
    if (!retryJobPoll(failuresBefore, error)) return failuresBefore + 1;
    failuresBefore += 1;
  }
  return failuresBefore;
}

describe("a lost job (a 404 after a restart)", () => {
  it("is recognised by the 404 alone", () => {
    expect(isLostJob(NOT_FOUND)).toBe(true);
    expect(isLostJob(SERVER_ERROR)).toBe(false);
    expect(isLostJob(new ApiError(403, "Only the user who started this job, the owner of its project, or an admin can see it."))).toBe(false);
    expect(isLostJob(OFFLINE)).toBe(false);
    expect(isLostJob(undefined)).toBe(false);
  });

  it("is never retried: one poll, then the page lets go", () => {
    expect(retryJobPoll(0, NOT_FOUND)).toBe(false);
    expect(attempts([NOT_FOUND, NOT_FOUND, NOT_FOUND])).toBe(1);
  });

  it("stops the polling", () => {
    expect(jobPollInterval("running", true)).toBe(false);
  });

  it("is said in the one plain line the card shows", () => {
    expect(pollFailureMessage(NOT_FOUND)).toBe(LOST_JOB_MESSAGE);
    expect(LOST_JOB_MESSAGE).toBe("The job stopped when the server restarted. Start it again.");
  });
});

describe("isServerAnswer: a refused poll against a connection that failed", () => {
  it("is true for any error status the server answered, false for a request that never reached it", () => {
    expect(isServerAnswer(SERVER_ERROR)).toBe(true);
    expect(isServerAnswer(NOT_FOUND)).toBe(true);
    expect(isServerAnswer(OFFLINE)).toBe(false);
    expect(isServerAnswer(undefined)).toBe(false);
  });
});

describe("any other failed poll", () => {
  it("is tried again, and given up after MAX_POLL_FAILURES attempts in a row", () => {
    expect(MAX_POLL_FAILURES).toBe(3);
    expect(retryJobPoll(0, SERVER_ERROR)).toBe(true);
    expect(retryJobPoll(1, OFFLINE)).toBe(true);
    expect(retryJobPoll(2, SERVER_ERROR)).toBe(false);
    expect(attempts([SERVER_ERROR, OFFLINE, SERVER_ERROR, SERVER_ERROR, SERVER_ERROR])).toBe(MAX_POLL_FAILURES);
  });

  it("that turns out to be the 404 stops at once, whatever came before it", () => {
    expect(attempts([SERVER_ERROR, NOT_FOUND, SERVER_ERROR])).toBe(2);
  });

  it("shows its own message on the card, and says the page stopped following", () => {
    const text = pollFailureMessage(SERVER_ERROR);
    expect(text).toContain("Internal Server Error");
    expect(text).toContain("stopped following it");
    expect(pollFailureMessage(OFFLINE)).toContain("Failed to fetch");
  });
});

describe("jobPollInterval", () => {
  it("polls every 1.2 s while the job is queued or running, or not known yet", () => {
    expect(JOB_POLL_MS).toBe(1200);
    for (const status of ["queued", "running", undefined, null]) expect(jobPollInterval(status, false)).toBe(JOB_POLL_MS);
  });

  it("stops once the job has ended", () => {
    expect(jobPollInterval("done", false)).toBe(false);
    expect(jobPollInterval("error", false)).toBe(false);
  });

  it("polls at the interval it is given", () => {
    expect(jobPollInterval("running", false, 25)).toBe(25);
    expect(jobPollInterval("running", true, 25)).toBe(false);
  });
});

/** A page's follow state, with only what a case sets. */
const state = (over: Partial<FollowState> = {}): FollowState => ({
  followed: null, inFlight: false, ended: new Set(), unreached: null, answeredAt: 0, askedAgain: new Set(), ...over,
});

describe("jobToAdopt: the page follows the scope's running job, whoever started it", () => {
  const running: ActiveJob = { id: "j2", kind: "revoice", status: "running", user_id: "someone-else" };

  it("adopts the project's job when the page follows none (after a reload, another tab, another user)", () => {
    expect(jobToAdopt(running, state())).toBe("j2");
    expect(jobToAdopt({ ...running, status: "queued" }, state())).toBe("j2");
  });

  it("adopts it over a job the page followed that has ended or was dropped", () => {
    expect(jobToAdopt(running, state({ followed: "j1" }))).toBe("j2");
  });

  it("leaves things alone when there is nothing to follow, or it already follows that job", () => {
    expect(jobToAdopt(null, state())).toBeNull();
    expect(jobToAdopt(undefined, state({ followed: "j1" }))).toBeNull();
    expect(jobToAdopt(running, state({ followed: "j2", inFlight: true }))).toBeNull();
    expect(jobToAdopt({ ...running, status: "done" }, state())).toBeNull();
  });

  it("never takes a job away from one still in flight", () => {
    expect(jobToAdopt(running, state({ followed: "j1", inFlight: true }))).toBeNull();
  });

  it("never re-adopts a job the page has seen end, which the answer may still name for a moment", () => {
    expect(jobToAdopt(running, state({ ended: new Set(["j2"]) }))).toBeNull();
  });

  describe("a job the page gave up on because the server could not be reached", () => {
    const gaveUp = { id: "j2", kind: "revoice", since: 5000 };

    it("is not taken back from the answer the page already had: React Query keeps it while the server is down", () => {
      // The answer that named the job was fetched before the give-up and cannot be refreshed while the
      // server is down; adopting from it restarted the failing polls for as long as the outage lasted.
      expect(jobToAdopt(running, state({ unreached: gaveUp, answeredAt: 4000 }))).toBeNull();
      expect(jobToAdopt(running, state({ unreached: gaveUp, answeredAt: 5000 }))).toBeNull();
    });

    it("is taken back from a newer answer that still names it: the server is back and the job running", () => {
      expect(jobToAdopt(running, state({ unreached: gaveUp, answeredAt: 5001 }))).toBe("j2");
    });

    it("after a poll the server refused: once, until one of its polls answers - a job whose own poll keeps failing cannot loop", () => {
      const refused = { ...gaveUp, answered: true };
      expect(jobToAdopt(running, state({ unreached: refused, answeredAt: 9000 }))).toBe("j2");
      expect(jobToAdopt(running, state({ unreached: refused, answeredAt: 9000, askedAgain: new Set(["j2"]) }))).toBeNull();
    });

    it("after a connection that failed: always, from a newer answer - a flap must not leave a running job unfollowed", () => {
      // Followed again once (askedAgain), the server dropped before its poll answered: a second give-up by a
      // failed connection. The job is still followed again once the server answers with it still running.
      const dropped = { ...gaveUp, answered: false };
      expect(jobToAdopt(running, state({ unreached: dropped, answeredAt: 9000, askedAgain: new Set(["j2"]) }))).toBe("j2");
      expect(jobToAdopt(running, state({ unreached: dropped, answeredAt: 5000, askedAgain: new Set(["j2"]) }))).toBeNull();
    });

    it("does not hold back a different job", () => {
      expect(jobToAdopt(running, state({ unreached: { ...gaveUp, id: "j1" }, answeredAt: 1000 }))).toBe("j2");
    });
  });
});

describe("jobToAskAgain: a job given up on while the server was unreachable", () => {
  const unreached = { id: "j1", kind: "revoice", since: 1000 };

  it("is asked after once more once the server has answered since", () => {
    expect(jobToAskAgain(state({ unreached, answeredAt: 6000 }))).toBe("j1");
  });

  it("waits while the server has not answered since the page gave up", () => {
    expect(jobToAskAgain(state({ unreached, answeredAt: 1000 }))).toBeNull();
    expect(jobToAskAgain(state({ unreached, answeredAt: 0 }))).toBeNull();
  });

  it("after a poll the server refused, is asked after only once; never while the page follows another job", () => {
    expect(jobToAskAgain(state({ unreached: { ...unreached, answered: true }, answeredAt: 6000, askedAgain: new Set(["j1"]) }))).toBeNull();
    expect(jobToAskAgain(state({ unreached, answeredAt: 6000, followed: "j2" }))).toBeNull();
    expect(jobToAskAgain(state({ answeredAt: 6000 }))).toBeNull();
  });

  it("after a connection that failed, is asked after again whenever the server has answered since", () => {
    expect(jobToAskAgain(state({ unreached: { ...unreached, answered: false }, answeredAt: 6000, askedAgain: new Set(["j1"]) }))).toBe("j1");
  });
});

describe("cardForJob", () => {
  it("routes each kind to the card that runs it", () => {
    expect(cardForJob("generate", "deck")).toBe("generate");
    expect(cardForJob("transcribe", "video")).toBe("transcript");
    expect(cardForJob("revoice", "video")).toBe("revoice");
    expect(cardForJob("render-slides", "deck")).toBe("slides");
    expect(cardForJob("ai-enhance", "pdf")).toBe("slides");
  });

  it("sends a kind not known yet to the card that starts such jobs", () => {
    expect(cardForJob(undefined, "deck")).toBe("slides");
    expect(cardForJob(null, "pdf")).toBe("slides");
    expect(cardForJob(undefined, "video")).toBe("transcript");
  });
});

describe("mayCancelJob: the cancel route's own rule", () => {
  const editor = { id: "u1", role: "editor" };
  const admin = { id: "a1", role: "admin" };

  it("lets the user who started the job cancel it", () => {
    expect(mayCancelJob({ user_id: "u1" }, editor)).toBe(true);
  });

  it("lets an administrator cancel anyone's", () => {
    expect(mayCancelJob({ user_id: "u1" }, admin)).toBe(true);
  });

  it("shows anyone else the job read-only", () => {
    expect(mayCancelJob({ user_id: "a1" }, editor)).toBe(false);
  });

  it("keeps a job that records no starter (an update) for an administrator, as the server does", () => {
    expect(mayCancelJob({ user_id: null }, editor)).toBe(false);
    expect(mayCancelJob({}, editor)).toBe(false);
    expect(mayCancelJob({ user_id: null }, admin)).toBe(true);
  });

  it("offers nothing without a job or a viewer", () => {
    expect(mayCancelJob(undefined, admin)).toBe(false);
    expect(mayCancelJob({ user_id: "u1" }, null)).toBe(false);
  });
});

describe("wasCancelled", () => {
  it("reads the result the jobs that honour a cancel report", () => {
    expect(wasCancelled({ cancelled: true, stage: "synthesis" })).toBe(true);
    expect(wasCancelled({ cancelled: false, saved: true })).toBe(false);
    expect(wasCancelled({ video: "clip_revoiced.mp4" })).toBe(false);
    expect(wasCancelled(null)).toBe(false);
    expect(wasCancelled("ok")).toBe(false);
  });
});

describe("lineToKeep: the line a done job leaves on its card", () => {
  it("keeps a cancelled job's closing line, which says what it left", () => {
    const line = "Re-voice cancelled before the new video was written; the project is as it was.";
    expect(lineToKeep({ kind: "revoice", status: "done", message: line, result: { cancelled: true, stage: "synthesis" } })).toBe(line);
    expect(lineToKeep({ kind: "ai-enhance", status: "done", message: "", result: { cancelled: true } })).toBe("Cancelled");
  });

  it("keeps a transcription's line when it dropped sentences' adjustments, and only then", () => {
    const line = "Transcribed 42 segments; the adjustments on 3 sentences were dropped";
    expect(lineToKeep({ kind: "transcribe", status: "done", message: line, result: { segments: 42, adjustments_dropped: 3 } })).toBe(line);
    expect(lineToKeep({ kind: "transcribe", status: "done", message: "Transcribed 42 segments", result: { adjustments_dropped: 0 } })).toBeNull();
    expect(lineToKeep({ kind: "transcribe", status: "done", message: "Transcribed 42 segments", result: {} })).toBeNull();
  });

  it("keeps nothing for a job that ran to its end, or one that failed (its error stays on the card)", () => {
    expect(lineToKeep({ kind: "revoice", status: "done", message: "Complete", result: { video: "clip_revoiced.mp4" } })).toBeNull();
    expect(lineToKeep({ kind: "revoice", status: "error", message: "Re-voice failed", result: null })).toBeNull();
  });
});

describe("noticeCard: which card shows the line", () => {
  it("shows a lost or unreached job's line on the card that ran it", () => {
    expect(noticeCard({ kind: "revoice", reason: "lost" }, "video")).toBe("revoice");
    expect(noticeCard({ kind: "ai-enhance", reason: "unreached" }, "deck")).toBe("slides");
    expect(noticeCard({ reason: "lost" }, "video")).toBe("transcript");
  });

  it("shows a finished job's line on its card, but not on the Slides card, which keeps its own result line", () => {
    expect(noticeCard({ kind: "revoice", reason: "ended" }, "video")).toBe("revoice");
    expect(noticeCard({ kind: "transcribe", reason: "ended" }, "video")).toBe("transcript");
    expect(noticeCard({ kind: "ai-qa", reason: "ended" }, "deck")).toBeNull();
    expect(noticeCard(null, "video")).toBeNull();
  });
});
