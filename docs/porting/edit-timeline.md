# Design spec — the edit timeline (vertical 6)

Read-only survey, 2026-09-16, plus measurements taken that day on the real 5m41s 1080p
source (`data/projects/6d808448772c/finished.mp4`). **E1 — the model and the render —, E2
— the gesture — and E3 — tracks — are built (2026-09-16, 2026-09-17 and 2026-09-18; E1 and E2
released together as 0.7.0, E3 released as 0.8.0 on 2026-09-18, alone and ahead of E4, beside
the narration transcript download; §7, §11); E4a — the music library, the model and the
render pass — is built too (2026-09-21) and is UNRELEASED, the version still 0.8.0 until E4b
lands (§7, §12); E4b, the lane itself, and E5 are not.** This is the answer to
"build something like Camtasia": a multi-track timeline on which the picture, the original
audio and the narration can be cut together — or, since E3, one track at a time. The survey
is written against the code as it stands at
`4f39ae1`, and every line number below is from that tree; E1's, E2's and E3's own notes in §7
cite symbols rather than lines, and where a build proved a section wrong the section is
corrected in place and says so.

The one-line version: **a project gains an ordered list of the ranges of its one source
that are kept — one per track since E3; the transcript never moves; everything else is
projection.**

## Headline

Camtasia is two products. Its timeline cuts material on tracks; its companion Audiate
transcribes narration and cuts the *words*. Media Studio is already the second half —
transcript editing, per-sentence voice and speed, TTS re-voice, and since phase 3a an
audition of the whole narration against the picture without a render. What it does not
have is the first half: material you can cut. This vertical adds it, and nothing else.

Four facts, each measured or cited, shape the whole design:

- **The transcript's identity is its window, so the transcript must never move.**
  `set_transcript` decides that two entries are the same sentence when they have the same
  `start` and the same `end` (`services/projects.py:303-316`), and carries a sentence's
  adjustments across only onto a match. Cut ten seconds out of the picture the obvious
  way — shift every later sentence earlier — and every adjustment on every later sentence
  is dropped by construction, surfaced as `timing_adjustments_dropped`. That is phase 1's
  corruption class, inverted. So the transcript stays in **source seconds** on disk, exactly
  as Whisper wrote it, and the edit is applied to it as a **projection** computed at plan
  time and render time. Nothing on disk changes coordinates; nothing is ever dropped.
- **One source per project is load-bearing, not incidental.** There is no route that adds a
  second media file; every consumer resolves `PROJECTS_DIR / pid / record["source_filename"]`
  (`services/transcription.py:29`, `services/revoice.py:51`, `api/routers/projects.py:219`);
  `TRACK_KINDS["picture"]` *is* `source_filename` and `original-audio` is the hard-coded
  `audio.wav` (`api/routers/projects.py:392-398`); every output name is the source stem
  (`utils/helpers.py:163-169`, `services/revoice.py:118`). So the edit is **ranges of the
  one source** — an ordered list of kept `[start, end]` pairs. That single list expresses
  a split, a trim and a ripple delete, and it leaves the filmstrip, the waveform and every
  `/tracks/*` route exactly as valid as they are today.
- **Cutting the picture forfeits stream copy, and that is fine because a re-encode is
  cheap.** The re-voice's whole speed comes from `-c:v copy` (`core/video_creator.py:289`;
  stated at `services/revoice.py:8`), and there is **no code path anywhere that re-encodes
  a source video's picture** — `_build_video` only ever encodes images and moviepy clips.
  A stream-copy cut at an arbitrary point does not work: cut at 47.300 s, the output begins
  on **P-frames** (`0.000,P 0.033,P …`) with no reference picture, and the source's
  keyframe interval is exactly **2.000 s** (GOP 60 at 30 fps), so snapping to a keyframe
  can land a cut up to two seconds off — longer than most sentences. Measured instead: a
  full picture re-encode of the 341 s source at `libx264 -preset ultrafast` (the generate
  path's own settings, `core/video_creator.py:1163-1173`) takes **15.8 s**; the real EDL
  render — `trim`/`atrim` + `concat`, picture re-encoded, audio to AAC — also **15.8 s**;
  on the customer's bundled **ffmpeg 7.1 essentials** binary, **16.1 s**, frame-accurate
  (duration 336.008005 = 341.008 − 5.000, opening on an I-frame, largest gap across the
  join one frame). About 2.8 s per minute of 1080p30. `h264_nvenc` was *slower* (24.4 s).
  Re-encode everything, every render, as a job.
- **The narration lane already exists; it must be fed, not rewritten.** `assemble_master`
  (`services/processing.py:251-275`) lays sentences by pin with silence between, a pin is
  a floor, an overrun eats the next gap — and the client's `schedule()` mirrors it exactly
  (`frontend/src/lib/timeline.ts:311-338`), pinned by an anti-drift test that runs a real
  `_revoice_video` beside a real plan (`tests/test_narration_plan.py:362`). Projecting the
  transcript *before* handing it to that machinery keeps every one of the 13 sync tests
  green and extends the anti-drift test rather than breaking it. Nothing in
  `_revoice_video` changes.

What this is **not**: not multi-source, not transitions or effects, not a general NLE.
Section 9 lists what is deliberately left out and why.

---

## 1. Survey — what an edit collides with

### 1.1 The project record

`import_upload` writes nine keys (`services/projects.py:176-187`): `id, name, kind,
source_filename, size_bytes, slide_count, created_at, owner_id, owner_name`. Every later
key is written by a job or an editor, and the full inventory is:

| Key | Writer | file:line |
|---|---|---|
| `transcript`, `language`, `duration`, `transcribed_model`, `transcribed_device` | transcribe job | `services/transcription.py:60-64` |
| `transcript` (words only, merged) | whole-list Save | `services/projects.py:367` |
| `transcript[i]` + `offset/muted/voice/provider/speed` | narration editor | `services/narration.py:328`; vocabulary at `:67` |
| `rendered_at`, `output_video`, `outputs` | generate job | `api/routers/projects.py:264-277` |
| `revoiced_video`, `revoiced_at`, `narration_audio`, `revoiced_language`, `revoice_failed_sentences` | re-voice job | `services/revoice.py:143-169` |
| `images_source`, `images_rendered_at`, `slide_count`, `engine_project_dir` | slide editor | `services/slides.py:177-178, 488-489` |
| `qa_review`, `outputs["qa_doc"]` | AI jobs | `services/ai_slides.py:743, 947-949` |

`GET /{pid}` returns the **whole** record including the full transcript
(`api/routers/projects.py:114-116`), and `save_project` rewrites the whole file every time
(`services/projects.py:293-297`). The record is one JSON blob; anything added to it is
served and rewritten with everything else. The edit must therefore be **small** — a list
of ranges, never anything per frame — and written on a committed change, never per
pointer movement.

The **inner** engine record (`ProjectState`, `core/project_manager.py:48-66`) is rebuilt
from scratch on every re-voice (`services/revoice.py:91-116`). Nothing may be stored
there. The edit lives on the outer record only.

### 1.2 Every writer, and the lock

`project_lock(pid)` (`services/projects.py:71-82`) guards read-modify-writes of the outer
record; its contract is quoted at `:73-77` and the reason it lives in that module and not
in a feature module at `:24-29`. Writers that take it: `set_transcript` (`:349`),
`narration.update_segment` (`services/narration.py:318`), `delete_project` (`:226`). The
three job writers deliberately do not — they re-read immediately before saving and rely on
`require_idle` (`services/transcription.py:52-58`, `services/revoice.py:136-141`,
`api/routers/projects.py:258-260`).

Two writers already take the **wrong** lock and two take none: `services/ai_slides.py:739-762,
945-950` write the outer record under `slides.project_lock` (the inner file's lock, which
`services/projects.py:28` explicitly names as different), and `services/slides.py:169-181,
478-490` write it under no outer lock at all. They are masked today because they run inside
jobs. **This spec does not fix them** — it is a pre-existing smell, recorded here so the new
writer is not mistaken for their cause — but the new writer is *not* masked, because a
committed edit is a route, not a job, and it takes `store.project_lock` without exception.

A new writer of the record must (per `services/projects.py`'s own rules and the guards
that enforce them): re-read and save inside `store.project_lock(pid)`; call
`jobs.require_idle(pid)` from the route (`api/routers/narration.py:63` is the precedent);
register the route in `PROJECT_SCOPED_ROUTES` with a body that validates
(`tests/test_project_ownership.py:32-68`); and audit it with a **new named constant** in
`api/audit.py` (`tests/test_audit.py:39-55` parses the source and rejects inline strings).

### 1.3 The render, and the ffmpeg the codebase actually has

`_revoice_video` (`services/processing.py:1061-1354`) is the only render a video project
gets: per-sentence synthesis, `assemble_master` into one pydub track, then
`replace_video_audio` (`core/video_creator.py:300-372`), whose command is
`_build_replace_audio_cmd` (`:273-297`): `-c:v copy`, the new audio mapped in,
`apad=whole_dur=<d>` and `-shortest`. Its `<d>` comes from `_probe_duration` (`:260-270`),
a bare `"ffprobe"` that returns `None` on any failure.

The whole repository contains **no trim, no concat and no `filter_complex`** (grep over
`core/`, `services/`, `utils/`). The only `-ss` is a single-frame grab
(`core/video_importer.py:542-545`); the only `-t` caps a GIF at 30 s
(`services/processing.py:1658`). Concatenation exists only as pydub `+` (audio) and
moviepy `concatenate_videoclips(method="compose")` (`core/video_creator.py:1115`). Mixing
is one music file looped at a flat volume (`:327-342` — the only music support in the
codebase, and where E4 starts, §7); `core/audio_mixer.py:33`
(`AudioMixer`) looks like a mixer and is **dead code** — never instantiated. Likewise
`extract_keyframes`, `get_video_chapters` and `get_video_duration`
(`core/video_importer.py:384-604`) sit on no API path. None of these count as capability.

Eleven call sites spawn `"ffmpeg"` or `"ffprobe"` **by bare name** (`services/processing.py:1149`;
`core/video_creator.py:264, 286`; `core/video_importer.py:390, 543, 554, 571, 584, 600`;
`core/pptx_exporter.py:410`) and work only because `utils/config.py:70-78` prepends the
resolved binary's directory to `PATH` at import. New code uses `FFMPEG_PATH`
(`utils/config.py:67`) and never the bare name — the narration spec already forbids the
pattern (`docs/porting/narration-timeline.md:722-726`).

### 1.4 ffprobe does not exist where it matters

`_get_ffmpeg_paths` (`utils/config.py:31-63`) prefers system PATH, then `static-ffmpeg`,
then `imageio-ffmpeg` — which ships ffmpeg **only**. The packaged app stages exactly one
binary, `app/bin/ffmpeg.exe` (`desktop/boot.py:93-103`; `tests/test_no_state_tracked.py:42`),
and `_FFPROBE_PATH` is private to `utils/config.py`, imported nowhere — **no module can even
ask whether a probe is possible.** On the development machine both resolve to a WinGet
build (system PATH wins), which is why every measurement here was repeated on the bundled
7.1 binary before being believed.

Two subsystems were built to avoid ffprobe and tests lock it in: the waveform takes its
scale from the WAV header (`services/waveform.py:7-12, 221-250`;
`tests/test_waveform.py:435`), and the plan refuses it in a long comment
(`services/narration.py:550-571`). **The edit inherits the rule absolutely.** It never needs
a probe: the source's length is the WAV header's; the output's length is the sum of the
kept ranges, known before ffmpeg runs; and the mux's `whole_dur` is that sum, passed in
rather than probed.

### 1.5 The job model

`services/jobs.py`: a two-worker pool shared with GPU transcription and the AI loops
(`:34`), in-memory state (`:35`, docstring `:6-7`), `jobs.start(kind, work, project_id,
user_id)` (`:183-197`), `progress(fraction, message)` (`:88-95`), and one job per project
enforced by `require_idle` (`:176-180`, 409 at `api/app.py:71-74`). Cancellation is
cooperative (`:134-163`) — and `_revoice_video` **never consults it**
(`services/processing.py:1061-1354`); its subprocesses have fixed timeouts (600 s for the
mux, `core/video_creator.py:353`). A render is one more job kind and fits the model
unchanged, with two consequences accepted for v1 and one requirement: while it runs every
edit is a 409 (the render takes ~16 s, so this is tolerable; it is not what Camtasia does,
and section 9 says so); it holds one of two workers; and the **new** picture step must
poll `cancel_requested_here()` while ffmpeg runs (and stop it), and use a timeout scaled to
the **decode reach** — from the first kept start to the last kept end, never the output
length; E1's second and eighth notes in §7 — because the existing step does neither.

### 1.6 The frontend

`NarrationTimeline.tsx` already draws three lanes on one `pps` scale — filmstrip from a
hidden `<video>` seeked to `frameTimes(duration, n)` (`:505-568`), the waveform as one SVG
path (`:694-702`; rationale `frontend/src/lib/timeline.ts:151-160`), sentence blocks with
ghosts and overrun markers (`:704-759`) — with a ref-painted playhead (`:280-287`), zoom
(`:43`), click-to-seek, and a Web Audio transport whose `schedule()` mirrors
`assemble_master`. Its pure helpers are reusable as they are:

```
pixelsPerSecond(duration, width, zoom)      poolPeaks(peaks, width)      waveformPath(pooled, h)
frameTimes(duration, count)                 thumbCount(width)            schedule(sentences, clipSeconds, squeeze)
clipsFrom(clips, position)                  auditionLength(duration, end)   clampTime(t, duration)
```
(`frontend/src/lib/timeline.ts:90-352`.) Two limits matter: `schedule()` is
one-dimensional and audio-only, skipping any sentence with no decoded buffer
(`:319-320`) and never re-sorting (`:301-304`); and the filmstrip and transport drive **one**
`<video src=/tracks/picture>` (`:532, :767-774`). Both are fine for ranges of one source,
and both are why multi-source is out of scope.

There is **no undo stack** anywhere in the frontend. The only undo in the product is the
engine's 20-deep `notes_history` for speaker notes (`core/project_manager.py:284-288`).

---

## 2. Data model

One new key on the outer record:

```json
"edit": { "version": 1, "keep": [[0.0, 47.3], [52.3, 341.008]] }
```

- `keep` is an ordered list of `[start, end]` in **source seconds**, ascending,
  non-overlapping, `0 <= start < end <= source_duration`, where `source_duration` is the
  WAV header's (`services/waveform.py::duration_for`). At most a few hundred entries in
  any plausible edit; the record stays small (E1 bounds the list at `MAX_RANGES` = 5000 —
  a bound on abuse, not on use).
- **Absent means keep everything.** Every existing project renders byte-identically to
  today, and the first cut a user makes creates the key. No migration.
- `version` is there so a later shape can change without guessing what an old record
  meant: E3's one-list-per-track shape is version 2 (§11.1, built), which E4's music lane
  joins additively (§7); multi-source and mixing the original audio come after that.
  `stored_keep` refuses any version but its own, so whoever bumps it must read the older
  shape as well, never refuse it. *(E3 did: `stored_tracks` reads version 1 as cut together
  — trap 22.)*
- The edit is the **only** thing stored. Split, trim and ripple delete are all changes to
  `keep`: a split adds a boundary (two ranges where there was one, contiguous); a trim
  moves a range's edge; a ripple delete removes a sub-range and the ranges either side
  stay where they are in source time. There is no clip object, no track object, no
  position field — position is *derived* (§3), which is what keeps the transcript's
  coordinates untouched.

Validation lives with the rest of the record's rules, in a new pure module
`services/edit.py`, and the schema for the route is a pydantic model with
`model_config = ConfigDict(extra="forbid")` — the same lesson `TranscriptSegment` learned
in phase 1, where the default `extra="ignore"` would have silently discarded fields.

The transcript is **not** touched by any of this. Its `start`/`end` remain source seconds.
That is the entire point.

---

## 3. Projection — the one piece of arithmetic

`services/edit.py` owns four pure functions, unit-tested to exhaustion, and both the plan
and the render call them — never a copy (E1 put them behind one entry point, `apply`, which
is the single call both `services/revoice.py` and `narration.plan` make; the module also
owns `validate_keep`, `whole_source` and the two store functions — §7):

```
output_duration(keep) -> float                 # sum of (end - start)
to_timeline(t_source, keep) -> float | None    # None when t falls in a removed range
to_source(t_timeline, keep) -> float           # the inverse, for the playhead and the filmstrip
project_transcript(transcript, keep) -> list   # the sentences the render will speak, in timeline seconds
```

`project_transcript` is the rule that decides what happens to a sentence at a cut, and it
is deliberately simple so it can be explained in one sentence to the person cutting:

- A sentence is **kept iff its `start` lies inside a kept range** (`[start, end)`, so a
  sentence beginning exactly where a cut begins goes with it). Its `start` becomes
  `to_timeline(start)` and its `end` becomes `min(end, range_end)` mapped likewise; its
  `offset` rides through **untouched**, so the pin the engine computes (`_pin`) is
  `to_timeline(start) + offset`, the offset applied once. *Corrected by E1:* this used to
  read `to_timeline(start + offset)`, which has no answer when an offset crosses a cut and
  would have applied the offset twice.
- A sentence whose `start` lies in a removed range is **left out**, exactly as a muted one
  is today (`services/processing.py:1167-1170` filters `spoken` before any window maths,
  so the room it occupied goes to the sentence before it — the existing rule, unchanged).
- Every override key rides through untouched, because the sentence object is the same
  dict with two numbers remapped. `_same_sentence` on disk is never consulted, because
  nothing on disk changed.

That output list is what `services/revoice.py` hands to the engine as
`slide0.original_segments` (`services/revoice.py:100-106`), and it is what `narration.plan`
reports. *As built:* the section is `transcript_section(projected sentences)` — its end is
the last kept sentence's clamped end, not `output_duration(keep)` as this first read — and
it is the plan's `duration` that is the output's length (E1's ninth note, §7). `_revoice_video` sees a transcript in a shorter
recording and does what it always does. The anti-drift test's job — "the plan's speeds are
the speeds the render really synthesises at" — gains a `keep` parameter and keeps holding.

The client gets the projection in `frontend/src/lib/edit.ts` (E2 — built; *as built* it is
three of the four, `outputDuration`, `toTimeline` and `toSource`, plus `wholeSource`, name for
name and rule for rule — `project_transcript` is **not** copied, because which sentences are
spoken and where they land is the plan's answer and is never re-derived in the browser),
tested against the same fixtures. This is the one deliberate duplication, and it is the same kind phase 3a
accepted for `schedule()`: the client needs `to_source`/`to_timeline` to draw and to seek,
and a round trip per pointer movement is not an option. The tests share one fixture,
`tests/fixtures/edit_projection.json`, read by `tests/test_edit.py` and by `edit.test.ts`, so
the two cannot drift silently.

---

## 4. The render — precisely what changes

**Nothing in `_revoice_video`.** The picture step is inserted *before* the mux and only
when `keep` does not cover the whole source; with no edit the path is today's, byte for
byte, and `tests/test_revoice_sync.py` never notices.

1. `services/revoice.py` reads `record.get("edit")`, validates it, computes
   `project_transcript` and `output_duration`, and builds the engine state from the
   projection (§3). Unchanged otherwise. (Shipped as one call, `edit.apply`, whose result
   also says whether the picture needs cutting at all.)
2. **New:** `core/video_creator.py::cut_picture(source, keep, dst, video_bitrate,
   cancel_check)` (as shipped — the plan's `fps` parameter never existed, since no rate is
   passed) — one ffmpeg invocation, `FFMPEG_PATH`, never the bare name:

   ```
   -ss S0 -i src                                   # an INPUT seek to the first kept start (E1)
   -filter_complex "[0:v]trim=start=S0−S0:end=E0−S0,setpts=PTS-STARTPTS[v0];
                    [0:v]trim=start=S1−S0:end=E1−S0,setpts=PTS-STARTPTS[v1]; …
                    [v0][v1]…concat=n=N:v=1:a=0[v]"   # every trim offset by S0: the seek resets the timestamps
   -map "[v]" -an -c:v libx264 -preset ultrafast [-b:v <preset bitrate>] -y dst
   ```
   Picture only, no audio (`-an`): the narration is muxed on afterwards by the step that
   already exists. **No `-r` at all.** `trim` + `setpts=PTS-STARTPTS` + `concat` carry the
   source's own cadence through — the proof render above passed no rate and came out at
   exactly 30 fps (10079 frames over 335.967 s) — and there is no ffprobe to learn a rate
   from anyway. What matters is that it is never `fps_for_transition`
   (`core/video_creator.py:35-41`), which chooses very low rates for static decks and would
   wreck a screen recording; not forcing a rate satisfies that by construction. The bitrate
   comes from the output preset (`services/output_presets.py`), `""` meaning codec default,
   exactly as `write_videofile` takes it (`core/video_creator.py:1169`); in E1 that is the
   default preset (`youtube_1080p`). *Corrected by E2:* this went on "and choosing one is
   E2's Render button" — E2 built no chooser; Render is the Re-voice card's button renamed
   when the edit removes anything, its `POST` is unchanged, and the cut picture still takes
   the default preset (§7, E2's list). *The next two
   rules were wrong as first written and are corrected here (E1's second and eighth notes,
   §7):* the step polls `cancel_requested_here()` every half second **while ffmpeg runs**
   and kills it on a cancel — there is no "between ranges", the cut is one run — and its
   timeout is `cut_timeout(keep) = 60 + 3 × (keep[-1][1] − keep[0][0])` seconds: the
   **decode reach**, never the output's length (measured rate ≈ 2.8 s per minute of footage
   decoded, with headroom). Writes to a `.part` and publishes with `replace_with_retry`.
3. `replace_video_audio(cut_picture_output, master, out, …)` as today. The one change this
   step asked for — the video duration as an **argument**, supplied by the caller as
   `output_duration(keep)`, with `_probe_duration` only when the caller passes `None`, so
   the edited mux never needs ffprobe — shipped as `video_duration` on
   `replace_video_audio` (`_build_replace_audio_cmd` already took one), tested, and
   **unwired**: "the caller" is `_revoice_video`, and this section's first sentence says
   nothing in it changes. The first sentence wins. An edited run still probes, or pads to
   infinity where there is no ffprobe, which is unobservable under `-shortest` (E1's third
   note, §7).
4. `record["edit_rendered_at"]` is stamped beside `revoiced_at`, for the same reason
   `revoiced_at` exists (`services/revoice.py:143-151`): the output filename does not
   change, so the page's cache-buster must. Shipped; a whole-source run clears it, so the
   record never claims an edit the file on disk does not carry.

Measured cost on the 341 s source: 15.8 s for the picture, ~3 s for the mux, plus
narration synthesis that is cached after the first audition. Under 25 s end to end. A
20-minute recording is about a minute.

The original audio is **not** part of the v1 output — the narration replaces it, exactly as
a re-voice does today. It is drawn on the timeline as reference only. Keeping it or ducking
it under the narration is v2 (§9); music over the top is **E4** (§7); and `AudioMixer` will
not be the starting point for either, because nothing has ever run it.

---

## 5. API

New router `api/routers/edit.py`, `prefix="/projects"`, following `narration.py`:

| Route | Notes |
|---|---|
| `GET /{pid}/edit` | `{"version": 1, "keep": [[…]] or null, "source_duration": …, "output_duration": …}` — `null` meaning everything, the source's length from the WAV header (`null` until the video is transcribed), the output's the sum of the ranges. `readable` only; allowed during a job — it is a read. 400 for an edit this version cannot read. |
| `PUT /{pid}/edit` | Replaces the whole list (it is small; a partial PATCH buys nothing). `require_project` → `jobs.require_idle` → validate → `store.project_lock` → re-read → save (validated before the lock, so a refused list leaves the record untouched). Audits the new constant `PROJECT_EDIT = "project.edit"` with detail = the range count and the seconds removed (`"2 ranges kept, 5.0 s removed"`), never the times. `extra="forbid"` and strict numbers (422). 400 naming the range for overlap, order, a bound past the source, NaN or infinity; 409 while a job holds the project **or while the video has no extracted audio** — the ranges are checked against its length. Answers the GET's shape. |
| `DELETE /{pid}/edit` | Back to keep-everything: the key is removed, not written empty. Same guards. Idempotent — a project with no edit is left as it is and nothing is audited. |
| `POST /{pid}/revoice` | The render. It **is** the re-voice job with the projection applied — one job kind, one code path — and E1 shipped exactly that: there is no `/render` route, the existing re-voice route starts it and the job reads the edit it renders. Listed here so the choice is explicit: **no new job kind.** |

`GET /{pid}/narration/plan` and `GET /{pid}/waveform` gain nothing in their URLs; the plan
applies the projection server-side and returns sentences already in timeline seconds — each
still carrying its `index` in the *stored* transcript, which is what the narration routes
address a sentence by — with `duration` the output's length, plus
`edit: {version, keep, source_duration, output_duration}` (the GET's shape) so the client
draws the same edit the plan was made from. *Since E3* the GET's shape — and so the plan's
`edit` block — is version 2, one `{keep, output_duration}` block per track (§11.1).
The waveform stays in source seconds — it is one WAV file — and the client projects it
(§6). Three routes join `PROJECT_SCOPED_ROUTES`; one joins the audit guard.

---

## 6. The UI — extend the timeline, add one gesture

Phase 3a's `NarrationTimeline` is extended, not replaced. `TranscriptCard` keeps its
List / Timeline switch. What changes:

- *Added by the build:* **the panel is laid out as the owner's Camtasia screenshot** — the
  canvas above, the transport beneath it (jump to start, step back, Play, step forward, jump
  to end, the clock), then the timeline: a toolbar (Undo, Redo | Cut | zoom out, the slider,
  zoom in, Fit), a timecode ruler, track headers (Video / Audio · original, reference only /
  Narration) beside the three lanes — dark in both app themes (`--tl-*` tokens in
  `theme.css`), as Camtasia's is whatever the app around it looks like. No Stop button
  (jump-to-start is the same thing) and no Export on the timeline: Render stays on the
  Re-voice card.
- **The strip is drawn in timeline seconds**, holes closed — the Camtasia feel, and the
  thing the owner asked for. The waveform is `poolPeaks` over the peaks array **sliced and
  concatenated by `keep`** (a bucket is 125 ms of one file, so keeping ranges is an array
  operation); the filmstrip seeks the hidden `<video>` to `to_source(frameTime)`; blocks
  come from the plan already projected. `pictureWidth` becomes `output_duration × pps`.
  *As built (E2):* the peaks are sliced by **cumulative output bucket index**, not one slice
  per range — rounding each range's span on its own let the error accumulate to 0.43 s
  after fifteen cuts (the Reviewer measured it; §7); now the projected array is exactly
  `round(output_duration / bucket)` long and each boundary is off by at most half a bucket.
  And the `keep` everything is drawn through is the plan's own `edit` block, never a
  separate `GET /edit`: a strip drawn from a plan of one moment and an edit of another
  would put the blocks over the wrong frames.
- **Range selection** — *corrected by E2, which built Camtasia's own gesture rather than
  the `I`/`O` keys this first named*: drag the playhead's green (in) or red (out) handle
  away from it, or Ctrl+drag on the ruler or the strip; Shift+Comma / Shift+Period grow
  the selection a frame, Ctrl+Shift+Home / End take it to the start or the end; double-click
  the head, Escape or Ctrl+Shift+D clears it. A plain drag on the ruler scrubs. Then
  **Cut** — the scissors, Ctrl+Delete, Backspace, Ctrl+X, and plain Delete too, because
  this timeline has no gaps to leave — is the ripple delete (`removeRange`), one `PUT /edit`
  with the new list, committed on release — never per frame
  (`docs/porting/narration-timeline.md:715-717`) — or a `DELETE` when the result keeps
  everything. A selection that would remove the whole video is refused in the browser
  ("keep at least one range"); a refusal from the server (400, 409) shows its message and
  leaves the strip on the plan the server still has.
- A removed range leaves a **thin marker** at the join, so a cut is visible after it has
  closed up, with the removed length in its tooltip — Camtasia shows nothing there, and
  nothing is how mistakes go unnoticed. *As built:* "5.0 s removed (0:47.300 – 0:52.300 of
  the source)"; a bare split — two touching ranges — gets a grey marker saying "Split —
  nothing removed." (E2's gesture never makes one; E3's split will.)
- **Undo/redo** is a client-side stack of `keep` snapshots (50 deep), because there is no
  server-side undo anywhere and the list is small enough to snapshot whole. `Ctrl+Z`
  re-`PUT`s the previous list. Lost on reload; the server holds only the current state.
  Honest about that in the UI. *As built:* a snapshot is pushed only when the commit
  succeeds, a snapshot of keep-everything goes back through `DELETE`, and the gesture —
  Cut, Undo, Redo, keys and buttons alike — is **locked from a successful commit until the
  plan it produced lands**, because until then the strip is still drawn from the previous
  plan and a second cut would be computed against it (the Reviewer's one MAJOR; §7).
- The transport plays the **projected** schedule — `schedule()` needs no change, it is
  given projected sentences — and the muted `<video>` is seeked to `to_source(position)`
  at every jump. Between ranges the picture skips; that is the edit. *As built:* the
  picture is re-seeked as the playhead crosses each join while playing, and paused once the
  narration outruns the cut picture rather than run into a removed tail; Play inside a
  selection stops at its end; a seek while playing resumes at the seek target.
- **Render** replaces "Re-voice" / "Re-voice again" on the card — *as built,* when the edit
  **removes** something; a bare split keeps the plain button and the untouched path — and
  says what it will do: "Cuts 1 range (5.0 s removed) and re-voices — about 25 s, plus any
  sentences the audition has not fetched yet." The estimate is `5 + reach × 0.05` s rounded
  up to five, from the **decode reach** (§7, second note), never the output length, and it
  leaves narration synthesis out because the audition caches it. The `POST` is unchanged and
  no output preset is chosen (§4).
- **A continuous zoom slider**, as Camtasia has, in place of the stepped buttons. Zoom
  exists today as four fixed steps — `ZOOMS = [1, 2, 4, 8]` (`NarrationTimeline.tsx:43`)
  behind + and − (`:652-666`) — and on a first open it appeared to do nothing, because the
  strip was drawing at zero scale (fixed at `4f39ae1`). The arithmetic already takes any
  factor: `pixelsPerSecond(duration, width, zoom)` (`frontend/src/lib/timeline.ts:102`)
  is continuous, and only the step array makes it discrete. The slider runs from **fit**
  (the whole edit in the strip's width, zoom 1) to a ceiling where one second is about
  200 px, on a logarithmic scale so the low end is not wasted; a **Fit** button beside it
  returns to 1. Zooming keeps the **playhead** at the same screen position — the strip's
  `scrollLeft` is recomputed from `to_px(position)` on every change, which is what makes
  zooming in to trim a cut and back out to see the whole feel like one motion rather than
  a hunt. `Ctrl`+wheel over the strip zooms about the pointer instead. The filmstrip
  regenerates only past `needsNewFrames`'s 120 px tolerance (`timeline.ts:207`), as now,
  so dragging the slider does not seek the video continuously. The peaks are pooled per
  zoom from the same cached array; nothing is fetched. *As built:* `MAX_PPS = 200`; the
  slider is `max^v` over 0–1; − and + step by a quarter (Ctrl+Shift+− / =); Fit is
  Ctrl+Shift+7, zoom to the selection Ctrl+Shift+8, the maximum Ctrl+Shift+9; Ctrl+wheel is
  scaled by the wheel's delta so a trackpad does not race to the ceiling; and the ruler
  draws only the visible window.

What stays typed rather than dragged: nothing new. Per-sentence offsets remain in the
List view as phase 3a left them; the edit gesture is a separate concern on a separate
lane. *Since E3:* an offset is dragged on the Narration lane as well (§11.3), and the List's
box is its readout — the number is still the contract.

---

## 7. Build order

**E1 — the model and the render, no UI.** **DONE**, 2026-09-16; see CHANGELOG
`#edit-timeline`. `record["edit"] = {"version": 1, "keep": [[start, end], …]}` in source
seconds, absent meaning keep everything; `services/edit.py` (`validate_keep`,
`output_duration`, `to_timeline` / `to_source`, `whole_source`, `project_transcript`,
`stored_keep`, `apply` — the one entry point both callers use —, `payload`, `describe`,
`set_edit`, `clear_edit`; `MAX_RANGES`, `PRECISION`, `EPSILON`); `EditIn` in
`api/schemas.py`; `GET` / `PUT` / `DELETE /{pid}/edit` in the new `api/routers/edit.py`
over `PROJECT_EDIT` in `api/audit.py`, the three routes in the ownership sweep; the render
as the re-voice job in `services/revoice.py` — `edit.apply`, `cut_picture` into the job's
scratch directory, the engine pointed at the cut, `edit_rendered_at` stamped and cleared;
`cut_picture` / `cut_filtergraph` / `cut_timeout` / `CUT_POLL_SECONDS` and the
`video_duration` argument on `replace_video_audio` in `core/video_creator.py` (this
phase's plan put the argument on `_build_replace_audio_cmd`, which already had one);
`narration.plan` returning the projected sentences (each with its `index` in the stored
transcript), the output's `duration` and the `edit` block; `readable_project` /
`writable_project` in `api/deps.py`; `NO_AUDIO_MESSAGE` in `services/waveform.py`. Proven
without a browser exactly as this paragraph asked — `PUT` a `keep`, `GET` the plan, render:
the 5 s cut on the corpus source came out 336.008 s by the WAV arithmetic and 10079 frames /
335.967 s by the picture (its video stream is 41 ms shorter than its audio), first frame an
I-frame at 0.000, framemd5-identical to the un-seeked graph. `frontend/` is untouched.
Tests: `tests/test_edit.py` (80 — 41 functions, three parametrised: the arithmetic to
exhaustion, the routes, the plan), `tests/test_generation_options.py` (+10, the picture
step against a fake ffmpeg it polls, and the mux told its length), `tests/test_revoice.py`
(+9, the render as the re-voice job), `tests/test_narration_plan.py` (+1, the anti-drift
pin's `keep` case — a real plan beside the real `revoice_project` under the same edit),
`tests/test_project_ownership.py` (+3 routes in the sweep) — **703 backend and 103 vitest
across the suites**, ruff clean. Deliberately left: **the UI** — no gesture, nothing on the
strip is drawn in timeline seconds, and a project carrying an edit shows the phase-3a
timeline with *projected* blocks over a filmstrip and waveform still in source seconds
(E2); the `video_duration` argument, which nothing passes (the third note below); the
client copy of the projection (`frontend/src/lib/edit.ts`, E2); and per-range input seeking
(the second note below).

Nine things the build changed about the design above. Read them before E2, because several
contradicted a line that read as written until E1 corrected it in place:

- **§3's pin formula was wrong and the build is right.** The projection remaps `start` and
  `end` only and leaves `offset` exactly as stored; the engine's `_pin`
  (`services/processing.py`) adds the offset once, so the pin is `to_timeline(start) +
  offset`. The formula as first written, `to_timeline(start + offset)`, has no answer when
  an offset crosses a cut — the moment it names is in the hole — and applying the offset in
  the projection as well would have applied it twice. §3 is corrected in place, because it
  is the formula the next reader (E2's `edit.ts`) will copy.
- **§4's timeout rule `60 + 3 × output_duration` was wrong, and the Reviewer proved it on
  real footage.** `trim` is a filter and runs *after* the decode, so the work is bound by
  how far into the source the last kept range **ends**, not by how much comes out: a 5 s
  keep at the tail of the 341 s corpus source cost 3.9 s, and a seekless cut on a two-hour
  source was killed by its own 75 s timeout (`cut_picture`'s docstring records both).
  Shipped: an **input seek**, `-ss <first kept start>` before `-i` — which resets the
  input's timestamps, so every trim in the graph is offset by that origin — and
  `cut_timeout(keep) = 60 + 3 × (keep[-1][1] − keep[0][0])`. The seek is frame-exact: the
  decoded frames' `-f framemd5` output is identical with and without it (a dev-only check,
  since the suite fakes ffmpeg; the recipe is in the docstring), and the tail keep fell to
  0.87 s. **The reusable rule:** size any filtergraph cut by its decode reach — the first
  kept start to the last kept end — and move the start of that reach with an input seek.
  The remaining limit: a head-plus-tail keep still decodes the whole file (correctly timed
  now, rather than wrongly killed), and the fix when it matters is one seeked input per
  range, concatenated — per-range input seeking.
- **§4 step 3 contradicted §4's own first sentence, and the first sentence wins.** "The
  caller supplies the duration" — but the caller of the mux *is* `_revoice_video`, and the
  section opens with "nothing in `_revoice_video`". The argument shipped (`video_duration`
  on `replace_video_audio`; `_build_replace_audio_cmd` already took one), is tested, and is
  **unwired**: an edited run still probes, or pads to infinity where there is no ffprobe.
  Measured unobservable: the mux is `-shortest`, and a plain `apad` and
  `apad=whole_dur=336.008` produce identical output on the edited corpus render. Recorded in
  the function's docstring and here rather than hidden; it is wired the day
  `_revoice_video` is opened for some other reason.
- **Trap 11 is false with offsets.** A sentence's *start* is clamped inside the output by
  the projection, but its pin is that start plus an offset the projection does not touch,
  so a sentence can still be pinned at or past the output's end under an edit and
  `past_end` fires exactly as it does unedited — the plan marks it, the audition skips it,
  the render's `-shortest` drops it. That machinery stays load-bearing; trap 11 is corrected
  in place.
- **Trap 14's "an edit can land mid-job" is false.** `PUT` and `DELETE /edit` take
  `jobs.require_idle` like every other write of the record, so nothing lands on a project
  while a job holds it. The re-read in the generate job still matters, for the reason the
  comment in `api/routers/projects.py` (fixed in E1, as trap 14 asked) now gives:
  `record_images_source` writes the record earlier in the *same* job, and a write that
  passed `require_idle` an instant before the job was registered can land on it too; the
  captured copy would revert either. §1.2's picture — job writers re-read and rely on
  `require_idle` — was right; the parenthetical was not.
- **`to_source` at a join returns the later range.** The end of one kept range and the
  start of the next are the same output instant, and `to_timeline` maps both to it; but the
  earlier range's end is a frame the output never shows (`trim`'s end is exclusive), so a
  filmstrip or a playhead seeked there would show a cut frame. The later range wins, the
  round trip still holds, and E2's `edit.ts` must copy that rule rather than take the first
  match.
- **Three consolidations the router forced.** The narration, slides and edit routers'
  `readable` / `writable` (`guard` / `writable`) pairs were one thing spelled three times;
  they live once in `api/deps.py` as `readable_project` / `writable_project`, with the
  order (403 before the kind, the kind before the job) pinned by the ownership sweep.
  `clear_edit` returns `(payload, had_edit)`, so a DELETE on a project with no edit writes
  nothing and is not audited — an audit row for a no-op would be a lie. And the
  "transcribe first" sentence the tracks route, the waveform's 404 and the edit's 409 all
  say is one constant, `waveform.NO_AUDIO_MESSAGE`, shared with `TRACK_KINDS`.
- **Cancel during the cut stops ffmpeg**, which is more than `_revoice_video` has ever
  done. §1.5 asked the step to check the flag "between ranges", but the cut is one ffmpeg
  run over every range, so there is no between: `cut_picture` runs it under `Popen`, polls
  the flag and its deadline every `CUT_POLL_SECONDS` (0.5 s), kills the process on either,
  and leaves nothing behind (the `.part` and the log are removed). The job ends as
  cancelled with nothing rendered and nothing stamped. The flag is read once more before
  the publish, so a cancel that lands between ffmpeg finishing and the rename discards the
  finished part.
- **The section end is the last kept sentence's, not `output_duration(keep)`.** §3 said
  the engine's section end would be set to the sum of the ranges; as built the section is
  `transcript_section(projected sentences)` — the one helper the plan and the render
  share — so its end is the last kept sentence's clamped end, and it is the plan's
  `duration` that is the output's length. The consequence is the one phase 3a already
  accepted: the last spoken sentence's window is bounded in the render by ffprobe of the
  cut picture (the sum less the 41 ms the video stream is shorter by), or by the section's
  end where there is no ffprobe, and in the plan by the WAV-derived sum. Blast radius
  unchanged: the last sentence, only when the bound would make it short enough to speed up.

Three refusals E1 added that the design did not specify, and E2 must show rather than
swallow: an edit written by a different `version` is a 400 on the GET and on the plan and
an error from the render, never a silent keep-everything (rendering the whole video while
the user believes a cut is applied is the one quiet failure this must never have); an edit
whose `audio.wav` has since gone is read back with `source_duration: null` but neither
applied nor ignored — the plan is a 400 and the render fails saying so; and an edit that
removes every spoken sentence fails the render with "keep at least one, or clear the edit".
Storing or clearing an edit also drops the memoised speaking rate (`forget_baseline`),
because the rate is measured over the sentences the render will speak and the edit decides
which those are.

**E2 — the gesture.** **DONE**, 2026-09-17, released as **0.7.0** together with E1 and
3a; see CHANGELOG `#edit-timeline-gesture`. The Timeline tab is a panel in Camtasia's
shape, dark in both app themes: the canvas; a transport (jump to start, step back, Play,
step forward, jump to end, the clock); a toolbar (Undo, Redo | Cut | zoom out, the slider,
zoom in, Fit); a timecode ruler (`m:ss` labels at whole-second steps, milliseconds below
that; only the visible window drawn); track headers (Video / Audio · original, reference
only / Narration) beside the three lanes — **everything drawn in timeline seconds**: the
filmstrip seeks the hidden `<video>` to `toSource(t)`, the waveform's peaks are sliced by
`keep` before pooling, the blocks arrive projected from the plan, and the plan's own `edit`
block is the `keep` it is all drawn through (never a separate `GET /edit`). The playhead's
head sits in the ruler with the green in / red out handles: drag one to select, double-click
to clear, Ctrl+drag on the ruler or the strip to select, drag the head to scrub; a band
spans every lane with its in/out times; a marker at every join says what was removed
("5.0 s removed (0:47.300 – 0:52.300 of the source)"; a bare split, "Split — nothing
removed."). **Cut** is the ripple delete (`removeRange`), committed as one `PUT /edit` on
release (`DELETE` when the result keeps everything); Undo/Redo a 50-deep client stack pushed
only on success ("Undo is for this visit"); the gesture **locked from a successful commit
until the plan it produced lands**; per-sentence clip survival across an edit, the playhead
remapped by `positionAfterEdit` rather than reset to 0; the transport re-seeks the picture at
each join and pauses it when the narration outruns the cut picture; Play inside a selection
plays to its end; continuous zoom (`MAX_PPS = 200`, a log slider, fit = 1, anchored on the
playhead; Ctrl+wheel anchored on the pointer and scaled by the delta); TechSmith's bindings —
Space; Comma / Period (a 1/30 s "frame"); Shift+Comma / Period; Ctrl+Home / End;
Ctrl+Shift+Home / End; Ctrl+Delete / Backspace / Delete / Ctrl+X (all ripple); Ctrl+Z;
Ctrl+Y / Ctrl+Shift+Z; Ctrl+Shift+D / Escape; Ctrl+Shift+= / −; Ctrl+Shift+7 fit / 8
selection / 9 max — held keys firing once. On the card (`ProjectDetail.tsx`): a
`["edit", id]` query, and the Re-voice button becomes **Render** when the edit removes
anything, with "Cuts N ranges (X s removed) and re-voices — about T s, plus any sentences the
audition has not fetched yet" (`renderEstimate` = 5 + reach × 0.05, rounded up to 5 s — the
reach rule of the second note above) and the edit query's error shown rather than swallowed.
Files: `frontend/src/lib/edit.ts` — the client copy of the projection (`outputDuration`,
`toTimeline`, `toSource`, `wholeSource`, `wholeKeep`) plus `joins`, `removeRange`,
`positionAfterEdit`, `projectPeaks`, `ticks` / `tickStep` / `rulerLabel`, the zoom maths,
`stepFrame`, `renderEstimate` / `renderSummary`; `edit.test.ts` (45);
`tests/fixtures/edit_projection.json`, read by `edit.test.ts` and by a new case in
`tests/test_edit.py`, both guarded so a truncation cannot drop the join case `[6.0, 7.5]`;
`NarrationTimeline.tsx` (816 → 1673 lines, around 3a's transport); `ProjectDetail.tsx`;
`TranscriptCard.tsx` (`jobActive` handed down); `EditPayload` and `edit` on `NarrationPlan`
in `lib/timeline.ts`; `theme.css` (`--tl-*` tokens, the `.os-tl-*` block). **Proven by the
Reviewer**, harnesses against `services/edit.py`: the client projection over 335,938
`to_timeline` and 338,702 `to_source` comparisons on 3000 random edits — identical but for
inputs at an exact decimal half-millisecond, 1 ms apart, never at a join, an end or a
pointer-like value; 184,452 ripple deletes against a 1 ms grid, every result accepted
unchanged by the server's `validate_keep`; and the waveform slicing fix — per-range rounding
drifted a burst up to 0.43 s over fifteen cuts, now exactly `round(output / bucket)` long
with no accumulation. **Verified live** by the supervisor (Vite against the dev backend, the
341 s corpus project): Ctrl+drag 47.3–52.3 → Cut → `PUT` 200 → 61 blocks, sentence 12
re-pinned 54.13 → 49.13 s (E1's measured value), the join marker "5.0 s removed", Render
and its summary; Ctrl+Z → `DELETE` → 62 blocks; during "Re-reading the plan…" a second
selection + Backspace / Ctrl+Z produced no request; zoom anchored within 0.4 px; the light
theme keeps the panel dark; playback runs. The packaged app is not in that record. Tests:
vitest **150** (was 105), `tests/test_edit.py` **81** (+1; 103 with the dist fingerprint and
version tests), the full backend suite **706** — run by the Developer and again by the
Reviewer; `tsc` and `eslint` clean; **no backend product code changed**. Deliberately left:
**E3** below; **E4** below; per-range input seeking (the second note above — a head-plus-tail
keep still decodes the whole file); and a preset on the render (§4).

Eleven things the build changed about the design above, listed as E1's were, because E3's
reader will otherwise copy a line the build contradicted:

- **The layout is the owner's Camtasia screenshot, not §6's list.** Canvas above, the
  transport beneath it, then the timeline (toolbar, ruler, headers, lanes). No Stop button —
  Camtasia has none; jump-to-start is the same thing — and no Export on the timeline: Render
  stays on the Re-voice card, where the voice, speed and language it renders with are chosen.
- **§6's `I`/`O` keys were not built.** The selection is Camtasia's own gesture — the
  playhead's green and red handles, Ctrl+drag on the ruler or the strip, Shift+Comma /
  Period, Ctrl+Shift+Home / End — and every binding is from TechSmith's published table,
  nothing invented. §6 is corrected in place.
- **Delete ripples.** Camtasia's plain Delete leaves a gap; this model has none — the edit is
  ranges of one source — so Delete, Backspace, Ctrl+Delete and Ctrl+X all remove-and-close,
  and the view says so once.
- **Play with a selection** resumes from the playhead when it is inside the selection, else
  from the selection's start, and halts at its end; **a seek while playing resumes at the
  seek target**, never at the selection's start — a seek is the user naming a position (the
  Reviewer's MINOR 1).
- **`removeRange` with a zero-length interval strictly inside a range is a bare split** —
  two touching ranges, the shape E3's split-at-the-playhead will store; E2's gesture never
  produces one, and a zero-length interval at a join leaves the list alone.
- **The waveform is sliced by cumulative output bucket, not per range.** §6's "sliced and
  concatenated by `keep`" read as one slice per range, and that rounded each span on its
  own: up to 0.43 s of drift after fifteen cuts, a burst visibly beside its sentence at the
  zoom ceiling (the Reviewer's MINOR 3). Range *k* now fills output buckets `round(at_k/b) …
  round((at_k + len_k)/b)`, slot *j* taking source bucket `round((start_k − at_k)/b) + j`,
  clamped to its own range so a hole's bucket is never shown.
- **The ruler draws only the visible window** — a zoomed two-hour strip has tens of
  thousands of minor ticks — and the tick table gained a **7200 s** row: below 0.0222 px/s
  the 3600 s labels fell under the 80 px rule.
- **A thumbnail every ~120 px** of strip (`THUMB_PX`) rather than 3a's count for the width:
  the lane is taller, and wider thumbs crop less.
- **The commit lock.** §6's undo paragraph did not foresee the window between a successful
  commit and the plan it produces. `awaiting` is stamped from the plan's `dataUpdatedAt` /
  `errorUpdatedAt` at the commit — not from `isFetching`, which is scheduler-timed and can
  read false before the refetch has begun — and cleared when either moves; `editLocked =
  jobActive || commit.isPending || applying` gates Cut, Undo, Redo, buttons and keys alike.
  Leaving the tab does not clear it: the plan is refetched on return, and that landing is
  what unlocks.
- **Undo goes back through `DELETE`** when the snapshot is keep-everything (§6 said
  "re-`PUT`s the previous list"), so the record reads as one that never had an edit; a
  whole-source *split* is still a `PUT`, because its marker is the point.
- **Render only when the edit removes something**, with a different line from §6's — "Cuts
  1 range (5.0 s removed) and re-voices — about 25 s, plus any sentences the audition has not
  fetched yet." — and a bare split keeps "Re-voice again" and the untouched path. **No
  output preset is chosen on Render**: §4 said choosing one was E2's Render button; the
  `POST` is unchanged and the cut picture still takes the default preset. §4 and §6 are
  corrected in place.

**The Reviewer's one MAJOR, and what closed it.** Between a successful commit and the plan
it produced, the strip is still drawn from the previous plan (`keepPreviousData` — the
playback-halt fix at `43d5ba3`), and because `set_edit` drops the memoised speaking rate the
refetch re-runs the calibration first: hundreds of milliseconds warm, seconds cold. The
gesture was unlocked in that window and computed against the stale keep. A second Cut
silently overwrote the first on the server (the history read `[K0, K0]`); a Ctrl+Z — one key
auto-repeat away, since `commit.isPending` is false between commits — snapshotted the plan's
keep as "before" and left the cut unredoable. Closed three ways: the lock above;
`committedKeepRef`, initialised from the plan and advanced by every successful commit, is
what the history snapshots — right even if the lock were ever bypassed; and `event.repeat`
is ignored for Space, Backspace / Delete, Ctrl+X and Ctrl+Z / Y (frame stepping and the
selection keys still repeat — holding them is how they are used). The supervisor reproduced
the original and confirmed the fix live.

**The clip-survival caveat, so the docs do not over-promise.** "The audition survives a cut"
holds for every sentence whose text and `preview_url` did not change. `set_edit` calls
`forget_baseline`, and `calibrate_tts_baseline` samples positions 0, n/3 and 2n/3 of the
long unmuted sentences; a cut that removes one of those before the last sample shifts the
sample set, the measured rate changes, every sentence not sitting on the job-speed floor gets
a new effective speed and so a new URL, and the signature effect drops all of them —
correctly, since the render would speak them at the new rate. Expect most clips re-fetched
after a cut early in the video and few after one near the end. **Clips whose speed did not
change survive**; keying the baseline memo on the sample texts would remove the effect, and
that is server work in `services/narration.py`, not E2's.

Honest limits, each said in the UI where it bites: a "frame" is 1/30 s (no ffprobe, so the
source's rate is unknown); the render estimate excludes narration synthesis; undo is per
visit; a selection is bounded to the picture (nothing past its end can be cut); Space with a
focused button is handled for Chromium (the handled keydown is default-prevented, so the
button never arms) and for Firefox (a `keyup` guard on buttons and links), and the owner's
real keyboard is the final check; a head-plus-tail keep still decodes the whole file.

**E3 — tracks: lock a track, cut the others, split at the playhead, drag the narration.**
**DONE**, 2026-09-18, built over `f637c1d` on top of 0.7.0 and released as **0.8.0** the same
day — E3 alone, ahead of E4, the owner's call on §11.7's sixth decision — together with the
narration transcript download (T1); see CHANGELOG `#edit-timeline-tracks` and
`#transcript-download` under 0.8.0. Asked for by the owner on 2026-09-17, the
day E2 shipped — *"I want to be able to select a channel and edit that channel, so for example
select video and edit just that track. That way I can adjust timings"* — and the same
afternoon, *"add a split, which splits the selected channel at playhead"*; chosen over E4
because it changes the edit's model and the music lane should be built on the final shape.
Designed in **§11** and built to it, with the notes below where the build differed (each
corrected in place there under *As built*). **The model:** `record["edit"]` version 2, one
kept list per track — `{"version": 2, "video": {"keep": […]}, "narration": {"keep": […]}}`, a
track key absent meaning whole; a version-1 record read as video = narration = K and written
back as version 2 on the next write; a version-2 record carrying a top-level `keep` refused
("mixes two shapes"); a foreign version still refused. `services/edit.py`: `TRACKS`,
`stored_tracks` (`stored_keep` kept as E1's name for the picture's list), `Applied` with
`video`, `narration`, `cut` (the video list removes something), `projected` (the narration
list does), `sentences` — `project_transcript` through the **narration** list iff it removes
anything, else the very transcript objects —, `output_duration` (the picture's) and
`narration_duration`, `keep` an alias of `video`; `payload(video, narration, source_duration)`
in the version-2 shape `{version, video: {keep, output_duration}, narration: {…},
source_duration, output_duration}`; `set_edit(pid, video=None, narration=None)` validating
both before the lock, storing `None` as absent, refusing both-`None`, and **merging** into the
stored edit; `clear_edit` and `describe` unchanged in behaviour. **The routes:** `PUT
/{pid}/edit` takes the version-2 body and still the version-1 `{"keep": […]}` (both tracks);
`keep` beside a track → 400; `{}` → 400 "at least one track"; a per-track refusal names the
track and the range; the audit summary is per track ("video: 2 ranges kept, 5.0 s removed;
narration: whole"). **New `PATCH /{pid}/narration/offsets`** (`api/routers/narration.py`,
`OffsetIn` / `OffsetsIn` in `api/schemas.py`, `narration.update_offsets` with
`MAX_OFFSET_BATCH` = 5000): `{"offsets": [{"index": i, "offset": x | null}, …]}`, strict,
indices unique and in range (400), the single PATCH's own `_offset` rule reused (±300 s, 3 dp,
0 stored absent), one read-modify-write under `store.project_lock`, 409 while a job holds the
project, audited once as `PROJECT_TRANSCRIPT_TIMING` with indices only, answering
`{"sentences": […]}` each with its `index`; the baseline memo not dropped; the route in the
ownership sweep. **The plan and the render:** `listed` keyed on `projected`, `duration` the
picture's output when cut, `edit` the version-2 payload, `past_end` for a sentence pinned at
or past the picture's end under a narration-only or a video-only cut; `revoice.py` cuts the
picture with `applied.video`, hands the engine `applied.sentences`, refuses "every sentence
cut" on `projected`, and stamps `edit_rendered_at` when **either** track removes anything (the
brief left the stamp to the build: the output differs from an unedited render either way).
**The timeline** (`NarrationTimeline.tsx`, 1673 → 2319 lines): lock icons on Video and
Narration (`localStorage` `ms:tl-locks:<pid>`, per visit and project), the track's **name**
selecting the channel (the other track locks; a second click unlocks both), Audio · original
"follows Video"; a locked lane hatched and dimmed, the band skipping it; Cut on the unlocked
tracks only (`nextEditForCut`, a per-track "keep at least one range", disabled with a reason
when both are locked); `S` / `Ctrl+Shift+S` (`nextEditForSplit`; a split on a boundary
commits nothing); pieces on all three lanes (`.os-tl-pieces.<lane>`), clickable once a lane
has more than one; join markers per lane; block selection by click / Ctrl+click / Shift+click
/ a marquee on empty Narration-lane space; the drag with lazy pointer capture, every selected
block by the same delta, ghost outlines at the origins, snapping within 8 px (Ctrl disables),
clamped to the audition, the label beside the block, one batch PATCH on release from what is
drawn, transforms held until the plan lands; `[` / `]` and Shift (or `{` `}`) nudges by code or
key; Reset timing; operation-based undo/redo (`{undo, redo}` entries, fifty deep, pushed on
success); the commit lock over offset commits; `onOffsetsSaved` folding the saved sentences
into `TranscriptCard`'s copy; the transport, `positionAfterEdit` and the join re-seek on the
**video** list; the `past_end` copy in two variants; Render when either track removes
anything, `renderSummary` per track (`ProjectDetail.tsx`). **Files:** `services/edit.py`,
`services/narration.py`, `services/revoice.py`, `api/routers/edit.py`,
`api/routers/narration.py`, `api/schemas.py`; `frontend/src/lib/edit.ts` (`splitAt`, `pieces`
/ `pieceAt`, `snap`, `dragOffsets` / `MAX_OFFSET`, `TrackEdit` / `TrackLocks` / `TRACKS`,
`trackList` / `trackBody` / `sameEdit`, `nextEditForCut` / `nextEditForSplit`,
`clickSelectsPiece`, `unmovedRelease` / `releaseSuppressesClick`, `renderSummary` per track),
`lib/timeline.ts` (`EditPayload` v2, `EditTrack`), `NarrationTimeline.tsx`,
`TranscriptCard.tsx` (`offsets`, `onOffsetsSaved`), `ProjectDetail.tsx`, `theme.css`;
`frontend/dist` rebuilt. **Tests:** `tests/test_narration_offsets.py` (new, 14 — the route's
validation, the one write under the held lock, the race against the single PATCH, 409,
ownership, the audit detail), `tests/test_edit.py` (+9 — the version-2 round trip, the
version-1 record read as cut together and written back as version 2, the per-track
projection, the shared fixture's `tracks` cases, the version-2 and version-1 bodies, the merge,
the per-track refusal and audit, the plan's `listed`), `tests/test_revoice.py` (+3: video-only,
narration-only, every sentence cut refused), `tests/test_narration_plan.py` (+1, the anti-drift
pin under a per-track edit), `tests/test_project_ownership.py` (+1 route),
`tests/fixtures/edit_projection.json` (`tracks`: video-only, narration-only, both, together —
read by both suites) — **734 backend (from 706) and 181 vitest (from 150; `edit.test.ts` 76,
from 45)**, `tsc`, `eslint` and ruff clean.

Sixteen things the build changed about the design in §11, listed as E1's and E2's were, and
each corrected in place there:

- **The pointer is captured lazily** — only once a drag has moved past the 3 px slop
  (`onBodyPointerMove`), never on pointer-down. The brief's "pointer capture on the body as
  E2's drags do" retargeted every click that followed a block's pointer-down to the strip body
  (the Reviewer's MAJOR, below), and the head's double-click with it. `unmovedRelease` and
  `releaseSuppressesClick` (`lib/edit.ts`, pure and tested) say what an unmoved release means
  per drag kind — seek on the ruler, pick on a marquee's start, the block's own click handler
  for a block, keep the selection for a handle or a Ctrl+click — and which follow-up clicks
  are swallowed.
- **A piece is clickable only once its lane has more than one** (`clickSelectsPiece`): a
  single-piece lane is the whole picture, and a click there seeks as it did in E2 — selecting
  the entire picture with one click would only ever arm a Cut that must refuse. §11.4's
  "clicking a piece selects it" holds from the first split or cut on.
- **A locked lane's piece seeks on click; it never arms a cut of the other track**
  (`pickOnLane`; the Reviewer's NIT 2) — Camtasia does not let a locked track's clips be
  selected either.
- **A block on a locked Narration lane can still be dragged.** The lock scopes cuts and
  splits — the lists; a drag commits an offset, not a list, and §11.3's rule that the drag
  never touches the lists holds either way. Recorded as decision 9 in §11.7 for the owner to
  overturn.
- **`set_edit` merges into the stored edit** rather than rewriting it: the version and the two
  tracks are its to write, a `None` track is removed, a version-1 `keep` is popped, and every
  other key rides through — so §11.1's promise that E4's `music` joins version 2 additively
  holds on write as well as on read (the Reviewer's NIT 7;
  `test_a_put_merges_into_the_stored_edit_and_keeps_the_keys_it_does_not_own`).
- **A version-2 record carrying a top-level `keep` is refused** ("mixes two shapes"), not read
  as either: it could mean two things, and "no edit" is the quiet answer to neither.
- **The drag's arithmetic is from what is drawn** (the Reviewer's MINOR 2): `dragOffsets` is
  given `pinned_start − start` — the plan's pin — never the page's stored offset, which can be
  a plan-refetch behind after a List-view edit; `committedOffsetsRef` serves undo's "before"
  only. §11.3's formula `round3(new pin − toTimeline(start))` is what ships; the input is the
  drawn pin.
- **`Applied` grew, and `keep` is an alias.** `video`, `narration`, `projected`,
  `narration_duration` beside E1's `cut`, `sentences`, `output_duration`; `keep` = `video`, so
  E1's callers and tests stand. `edit_rendered_at` is stamped when either track removes
  anything.
- **The offsets route answers `{"sentences": […]}`**, each sentence with its `index`, rather
  than the bare list §11.3 said ("returning the updated sentences"), so the answer can grow a
  key without changing shape; `MAX_OFFSET_BATCH` (5000) caps a body — a bound on abuse, not on
  use.
- **The service accepts a numeric-string offset** ("0.4"), as `update_segment` always has
  (`_offset` → `float`), where the route's `StrictFloat` answers 422. Consistent with "reuse
  the validator, never copy it"; recorded so nobody tightens one without the other (the
  Reviewer's NIT 8).
- **The render line** (`renderSummary(video, narration, sourceDuration)`): E2's line word for
  word when the two lists are equal; otherwise "Cuts 2 ranges of the picture (12.4 s removed)
  and shortens the narration's timeline by 0.6 s (sentences spoken in the removed stretch are
  left out) and re-voices — about 25 s, plus …"; narration-only, "… and re-voices — the
  picture is not cut, so it takes only as long as the sentences the audition has not fetched
  yet." The brief's "removes 5.0 s of the narration" was not true of what is removed — a 0.6 s
  narration cut can drop a 5 s sentence or none (the Reviewer's NIT 1).
- **The nudge keys work by physical key or by what it typed** — `BracketLeft` /
  `BracketRight`, or `[` `]` `{` `}` — with AltGr allowed, the one exception to the Alt
  bail-out, so a QWERTZ layout (where `[` is AltGr+8) nudges (the Reviewer's NIT 5). `{` / `}`
  are the quarter-second step, as Shift is.
- **The `past_end` copy comes in two variants** (the Reviewer's MINOR 3): with an offset,
  "pinned at or past the end of the picture by its offset … pull the offset back — drag the
  block earlier, nudge it with [, or type it in the List view"; without one, "the cut picture
  now ends before it is spoken — unlock Narration and cut it too, or drag it earlier". A tail
  cut with Narration locked makes the second kind, and the List's box cannot help it.
- **The hatch is drawn on the lane's pieces layer**, above the thumbnails and the waveform (a
  background on the film lane was hidden under its `<img>` children, and an SVG takes no
  pseudo-element — the Reviewer's NIT 6), so a locked Video lane is visibly hatched in both
  themes.
- **Snapping also to 0 and the picture's end**; the drag is clamped so no member leaves
  `[0, total]`; ghost outlines are drawn at every moved block's origin for the drag's duration,
  so "the ghost stays at the spoken moment" holds for a never-nudged block too (the Reviewer's
  NIT 10); and a release while a key has locked the gesture mid-drag (`]`, Ctrl+Z) commits
  nothing — the blocks return to the plan (NIT 4).
- **Not drawn:** §11.4's muted eye on the headers; the marquee is one-dimensional (time), so it
  selects by the stretch it crosses; no auto-scroll while dragging.

**The Reviewer's one MAJOR, and what closed it.** As first built, `beginDrag` took pointer
capture on the strip body on every pointer-down, and Chromium (Pointer Events Level 3)
dispatches the `click` that follows a captured pointer-down to the capturing element — so no
block ever received its own click: a plain click landed on the body's `onStripClick` and
seeked to the pointer's x, not the sentence (an E2 regression: 0:27.961, the block's centre,
instead of the pin at 25.150), Ctrl+click did nothing, Shift+click seeked, and the head's
`dblclick` never cleared the selection (E2's own bug, same cause — MINOR 1). The Reviewer
proved the mechanism in a fixture and on the real component, and the fix is the first note
above: capture only once a drag has moved, so an unmoved release lets the click through to its
target. The two other MINORs (the drawn-offset rule; the `past_end` copy) and eight of the ten
NITs are closed as listed above; NITs 3 (a selection is clamped to the picture) and 8 (the
numeric-string offset) are recorded as limits. **Proven by the Reviewer** (harnesses in the
scratchpad, none committed): 20,000 random per-track edits — each track null, whole,
split-only or a real cut, 5 % of them version-1 records — over random transcripts, `apply()`
against an independent oracle on `cut`, `projected`, both durations, both lists and every
projected `(index, start, end, offset)`, **0 mismatches**, the untouched path returning the
very transcript object every time; 400 on-disk projects through `set_edit` — every sentence's
pin, `duration`, `edit` and `past_end` (6,354 checks) equal to the oracle, **0 mismatches**;
the offsets route — one save under the held lock, and **800 race runs** against the single
PATCH, the whole-list Save, `set_edit` and a second batch, **0 lost, 0 torn**; **36,000
`nextEditForCut`** and **24,000 `nextEditForSplit`** against a 1 ms brute-force model, 0
mismatches, locked tracks reference-identical in every case, every split leaving the output
unchanged, and all **88,636** resulting lists accepted unchanged by `validate_keep`; **40,000
`dragOffsets`** results accepted unchanged by `narration._offset`, never a `-0`, never a zero
sent as a number. **Verified live** by the supervisor (Vite against the dev backend, the 341 s
corpus): Narration selected, cut → `video: null`, the narration cut, 61 blocks, sentence 12 →
49.13 s, the picture unchanged, the join on the Narration lane; Video selected, cut → 62
blocks kept, sentence 12 at 54.13 s, the picture 336.008 s, the join on the picture lanes, the
playhead remapped; `S` with Video selected → the video list split only, pieces 2 / 2 / 1; a
plain block click chose the sentence and seeked to 25.150 (its pin, not the pointer),
Shift+click selected the run, Ctrl+click toggled, the head's double-click cleared; a drag of
three selected blocks → one PATCH, the offset 19.76 (snapped), `]` → 19.81, Reset → null; the
locked lane hatched, its click seeking; a tail cut with Narration locked dropped nine
sentences with the no-offset copy. The packaged app is not in that record.

Honest limits, each said in the UI where it bites or recorded here: the marquee is
one-dimensional; no auto-scroll while dragging; a selection is clamped to the picture
(`normalize` → the picture's duration), so a narration piece past a cut picture's end can be
neither selected nor cut from the UI — those sentences are `past_end` and unrendered anyway,
and the notice says how to bring them back; the muted eye is not drawn; a block on a locked
Narration lane can still be dragged (decision 9); the service's numeric-string offset;
snapping is in pixels, so at fit zoom the 8 px threshold is seconds on a long source; a `-0` or
a sub-millisecond offset is stored as absent. **Deliberately left:** **E4** below; **E5** below
(trim handles on the pieces this phase makes, markers, `J`/`K`/`L`, snapping of cuts); and
per-range input seeking (E1's second note), still.

**E5 — the rest of Camtasia's editing set, in this order:** trim handles on a piece's edges
(E3's split makes the pieces); markers; keyboard `J`/`K`/`L`; snapping of cuts to sentence
pins (E3 brings snapping to the *drag*).

**E4 — the music lane.** Asked for by the owner on 2026-09-16 — *"I should be able to add
music tracks over the top — another channel like Camtasia"* — and moved here out of §9's
v2 list (§9 keeps mixing and ducking the *original* audio). **Designed in §12 (2026-09-21) and
BUILT, in both halves: E4a — the library, the model and the render pass — on 2026-09-21, and
E4b — the lane, the gestures, the inspector and the audition — on 2026-09-22; the two blocks
after this sketch record them. Both are unreleased: 0.9.0 ships them together.** E4 is therefore
complete except **E4c**, the one question the build raised and did not answer — whether the
server should keep an already-stored clip whose file has gone, so that only the render refuses
and the lane stays editable (§12.5's *As built*; it is the owner's call). The sketch below stands
as the outline of the whole phase — a second lane on the same timeline, which fits the per-track
shape E3 gave the edit.

- **The model.** `edit.version` 2 gains `"music": [{file, at, in, out, gain, fade_in,
  fade_out}]` — `file` a name in the music **library**, `at` where the clip starts in
  **timeline seconds** (the output's: music is placed on the cut, not on the source, so a
  ripple delete before it moves it with the picture, which is what a Camtasia lane does),
  `in` / `out` the slice of the file used, `gain` the clip's level, `fade_in` / `fade_out`
  in seconds. `keep` is unchanged. The bump means `stored_keep`, which refuses any version
  but its own, must read a version-1 record as "no music" rather than refuse it; and the
  route payload and the plan's `edit` block carry the lane, so E2's drawing has it from the
  same fetch as the ranges. *Since E3:* version 2 exists and is the per-track shape of §11.1
  — the two tracks' lists in place of the one `keep` —, `stored_tracks` already reads
  version 1, and `set_edit` merges, so `music` joins with no bump and survives a cut.
- **The library.** Upload / list / delete under `assets/music` — porting vertical 2b
  (`docs/porting/generation-options.md` §4), surveyed 2026-09-14 and never built: today
  `assets/music/` does not exist and nothing serves it. The library API (filenames
  validated like `_PID_RE`, audited) is the prerequisite, and it is the same work 2b needs
  for the generate path, so it is built once, for both. *Built in E4a* (`services/music.py`,
  `api/routers/music.py`, five studio-wide routes, §12.3): the directory exists, it is
  served, and 2b can use it as it stands.
- **The render** generalises the one-file pydub overlay in `replace_video_audio`
  (`core/video_creator.py:327-342` at `4f39ae1`; the function gained the `video_duration`
  lines above it in E1) — one file, looped to the narration's length, at a flat volume
  from Studio settings; the only music support there is — to clips placed at `at`, sliced
  `in`–`out`, faded, and summed under the master before the mux. `AudioMixer` is still not
  the starting point (§1.3). *As designed in §12 and built in E4a:* it does not generalise
  that overlay, it leaves it alone — it is the generate path's and is not on this one — and
  lays the clips in a **second ffmpeg pass after the mux**, which is what keeps
  `_revoice_video` untouched and the standalone narration track voice-only (§12.4).
- **The audition** schedules a music `AudioBuffer` at `at` exactly as `schedule()` places
  the sentence clips, on the same Web Audio clock, with the clip's gain and fades applied
  there; the library file is fetched and decoded once, like a sentence clip. *Built in E4b*
  exactly so (§12.5): one `GainNode` per clip following `fadePoints`' linear ramps, one decode
  per FILE cached by name beside the sentence buffers, and the eye a mute on the bus they all
  share — never anything a payload carries.
- **Two rules the owner set earlier bind it, and neither is a design question:** **no
  ducking** — the music sits at one static lower volume under the voice for the whole clip
  (dynamic ducking was rejected for the choppy playback it produced) — and **music may
  fade in and out while the voice never does** (the narration starts and stops clean, as
  `assemble_master` leaves it).

Sequenced **after E3**, at the owner's word on 2026-09-17 (per-track editing first, because
it changes the edit's model — §11 bumps `version` to 2 — and the lane should be built on the
final shape: `music` joins version 2 *additively*, a missing key meaning no music, so E4 needs
no bump of its own). It needs E2's timeline-second drawing and transport to be placed and heard
at all. E3 is built (2026-09-18), and its `set_edit` merges into the stored edit, so a `music`
key survives a cut (§11.1's *As built*).

**E4a — the music library, the model and the render pass, no UI.** **DONE**, 2026-09-21,
built over `24f9cd3` on top of 0.8.0 and **not released**: the version stays 0.8.0, and 0.9.0
ships E4a and E4b together (§12.8, decision 8). Designed in **§12** and built to it, with each
place the build differed corrected in place there under *As built*. **The library**
(`services/music.py` and `api/routers/music.py`, both new): `assets/music/` beside
`assets/finished`, an `index.json` read and written under one module lock, and five routes
that are **studio-wide** and carry no `{pid}` — `GET /api/music` (the index, sorted by name);
`POST /api/music` (multipart; the six extensions of §12.3, a 100 MB cap read one byte past so
an oversize body is never buffered whole, the file decoded once to prove it is audio and to
measure it, its 125 ms peaks cached as `<name>.peaks.json`; 400 not audio or a name the
library cannot store, 409 the name exists, 413 too big); `GET /api/music/{name}` (the file,
its media type from the extension, `Cache-Control: private, max-age=86400` — the name is
immutable); `GET /api/music/{name}/peaks`; and `DELETE /api/music/{name}` (409 naming the
projects while any project's edit has a clip on the file, 404 when there is nothing of that
name). Names are unique **case-insensitively** and looked up **exactly**; the file, its peaks
and the index are each written to a `.part` and published with `replace_with_retry` (trap 10);
`MUSIC_UPLOAD` / `MUSIC_DELETE` in `api/audit.py` carry the name and the size and nothing
else, and `ENTITIES` gains `"music"` because `tests/test_audit.py` walks every `audit(...)`
call site and checks both. **The model** (`services/edit.py`, `api/schemas.py`,
`api/routers/edit.py`): `edit.music`, a list of at most `MAX_CLIPS` = 200 objects with exactly
`CLIP_KEYS`, validated by `validate_music` against the library's `{name: duration}` with every
refusal naming the clip by index and id and the field; `stored_music`, which reads a clip
whose file has left the library back as `missing: true` rather than as an error (trap 25) and
a list that does not validate in SHAPE as an UNREADABLE-style `ValueError`; `Applied.music`;
`payload(video, narration, source_duration, music)` carrying each clip with its
`file_duration` and `missing` onto `GET` / `PUT` / `DELETE /{pid}/edit` and into the plan's
`edit` block; the audit summary gaining "; music: 2 clips"; `MusicClipIn` and `EditIn.music`
in the schema (`extra="forbid"`, `in` an alias, strict numbers); and `PUT /edit` reading **one
rule for all three keys** (§12.6's *As built*). **The render** (`core/video_creator.py`,
`services/revoice.py`): `music_filtergraph`, `music_timeout`, `mix_music`, and
`_run_until_done` — the Popen/poll/kill loop lifted out of `cut_picture` and shared rather
than copied. The clips are laid under the muxed output in one more ffmpeg pass with the
picture copied, the voice up-mixed at **unity** (`UPMIX_STEREO`), `normalize=0` on both mixes
and `duration=first`; a clip whose file is missing refuses the job before it does any work;
`music_rendered` goes on the record for the page, and the stamps describe the file that is on
disk. **Shared, not copied:** the strip's 125 ms bucket arithmetic is now
`waveform.peaks_from_samples` / `peaks_payload`, which `compute_peaks` itself calls, so the
library's peaks and the strip's cannot drift. **`frontend/` is untouched** — the lane is E4b —
and `frontend/dist` stays fingerprint-consistent.

Seven things the build changed about the design in §12, each corrected in place there under
*As built*: the up-mix is `pan=stereo|FL=FL+FC|FR=FR+FC`, not §12.4's `aformat`; `PUT /edit`
has one rule for every key in place of §12.6's asymmetry, which **reverses trap 32**; the
library is case-insensitively unique and exactly looked up; the mux's stamps go on **before**
the music pass, not after it; the decode seam runs `FFMPEG_PATH` itself rather than pydub,
which would have run ffprobe; `_run_until_done` is shared with the picture cut; and the bucket
arithmetic moved into `services/waveform.py`. Traps **33** and **34** are what the round
earned.

**The Reviewer's two MAJORs, and what closed them.** Its verdict was CHANGES REQUIRED, and
neither MAJOR was findable by reading. **(1) A delete with a differently-cased name defeated
the in-use guard.** Windows matches filenames without regard to case and the index did not,
so `DELETE /api/music/BED.mp3` against a library holding `bed.mp3` passed the "no row, but the
file is there" escape hatch (`Path.is_file()` is true for either spelling), compared
`"BED.mp3"` against clips that all say `bed.mp3`, found no reference, unlinked the file and
its peaks **by path**, and kept the index row — a 204, a referenced file deleted, and a
phantom row behind it: `GET /api/music` went on listing `bed.mp3` while
`GET /api/music/bed.mp3` answered 404, the plan and `GET /edit` went on reporting the clip as
healthy while the render refused it as missing, `validate_music` went on accepting new clips
against the dead name, and the name itself was stuck — a re-upload a 409 and a delete an
in-use 409. Closed at both ends: an upload now collides under
`str.casefold` with an indexed name, an on-disk name or one mid-upload, so the index can never
hold two names Windows cannot tell apart (on Linux too, so the platforms behave alike); and
every lookup — `get_path`, `peaks`, `remove`, `references` — requires the exact on-disk
spelling, read from `os.listdir(MUSIC_DIR)` rather than inferred from `Path.is_file()`. The
escape hatch stays for the interrupted-upload case and fires only on an exactly-spelled
on-disk name. **(2) The music pass took a mono narration down 3.01 dB** — the design's own
defect, since §12.4 named the up-mix that does it; §12.4's *As built* records the measurements
and the fix.

**Proven by the Reviewer** (harnesses in the scratchpad, none committed): the filtergraph over
**4,000** random clip lists plus eight hand-built edge cases — chain count, every `[mK]`
label, `adelay` in whole milliseconds and equal to `round(at × 1000)`, the input index equal
to the position in the DISTINCT-file list, `normalize=0` on every `amix`, exactly one
`duration=first`, no `afade` with `d=0`, every number at 3 dp, no `dB` anywhere — **0
problems**; **nine real renders** on the real binary (one clip at 0; fades; fades filling the
clip; three clips over two files; a clip running past the voice's end; the 0.1 s minimum;
gain 0; **20 clips over 2 files**; a sub-millisecond `at`), every one 2 channels, the output
the voice's length, **60 video frames in = 60 out** by framemd5, and each clip audible only in
its own window; `normalize=0` shown to be load-bearing (stripped, the voice drops to ×0.354);
the 200-clip worst case measured at **15.9 s and 132 MB** peak ffmpeg RSS on a 340 s output,
against its own 740 s deadline; **4,000** fuzzed clip dicts giving **3,818** refusals, every
one a `ValueError` naming the clip; `_run_until_done` reconstructed from `24f9cd3` and run
beside both callers over **eight** terminal cases — exit 0, a cancel while running, the
deadline, a non-zero exit, exit 0 with no output, a cancel landing between ffmpeg's exit and
the publish, a cancel before it starts, and `Popen` raising — with return value, kill, poll
count, destination bytes and leftovers identical in every column; the traversal set (`..`,
`%5C`, `%00`, a trailing dot) refused and the decoy outside the directory never served;
`references` over **300** projects in **163 ms**; and **13 planted mutants**, of which 9 died
— the four survivors are closed by the fix round's tests (the deadline sized by the OUTPUT,
the index's module lock under ten concurrent uploads, the index's own `.part` publish) or
recorded as harmless (a redundant `missing` branch).

**Measured by the supervisor.** Before the build: the bundled ffmpeg 7.1 essentials lists all
seven filters §12.4 names, and §12.4's graph over the corpus re-voiced output (336 s, 71 MB)
with two 300 s stereo MP3s cost **8.25 s** on it and 8.0 s on the dev 8.0.1, exit 0, the
output still 5:35.96 and the picture copied — which is where `music_timeout = 60 + 2 ×
output` comes from, generous by forty times. After the fix round: the up-mix on both binaries
(the table in §12.4's *As built*), and live, on a real render of the mono project
`c5c8a50e6c3c`, the voice within **0.03 dB per channel** of the standalone narration in a
music-free window with the music **19.4 dB** above the floor in its own. The packaged app is
not in that record, and neither is a browser — there is nothing to look at until E4b.

**Tests:** `tests/test_music_library.py` (new, **40**), `tests/test_music_render.py` (new,
**23**), `tests/test_edit.py` (**143**, from 90), `tests/test_waveform.py` (+1: the two peak
paths agree on the same samples, mono and stereo), `tests/test_project_ownership.py` (the five
routes in a new `STUDIO_WIDE_ROUTES` *exclusion* list, itself guarded so a route that ever
grew a `{pid}` fails the sweep), `tests/test_no_state_tracked.py` (`assets/music/` stays
git-ignored) — the full backend suite **906** (from **786**; **891** at the first build,
before the fix round), ruff clean; nothing under `frontend/` changed, so vitest is untouched.
**Deliberately left — E4b, which is the rest of E4:** the Music lane and the per-lane CSS a
fourth lane needs, the add / move / trim / remove gestures, `cutMusic` and the ripple through
the clips, the Library modal, the inspector, the audition's music buffers and the eye that
mutes them, the render summary's "mixes 2 music clips", and the release of both halves as
0.9.0. Per-range input seeking (E1's second note) is still left, too. *(All of that is the
block below, built the next day; the release is the only item still open.)*

**E4b — the Music lane: the gestures, the inspector and the audition.** **DONE**, 2026-09-22,
built over `131f8c2` on top of E4a and **not released**: the version stays 0.8.0, and 0.9.0
ships E4a and E4b together (§12.8, decision 8). It is **`frontend/` only** — not a line of
`services/`, `api/`, `core/` or `tests/` changed, and the backend suite is **909** either side
of it (E4a's 906 plus the three the stereo refusal added at `131f8c2`). Designed in **§12.5**
and built to it, with each place the build differed corrected in place there under *As built*.

**The lane and its CSS** (`theme.css`; `NarrationTimeline.tsx`, 2319 → 3209 lines): a fourth
lane, Music, under Narration — `.os-tl-headers` on `repeat(4, …)`, `.os-tl-music`, and a clip
as a violet block (`--tl-music`, so it is never mistaken for a narration block or the original
audio) carrying the file's name, **the file's waveform inside it** (`clipPeaks` is
`projectPeaks` over `[[in, out]]` of the cached peaks — the audio lane's own bucket rule, never
a second one), a `clip-path` triangle at each end for the fades, and a `--bad` hatch with
"— missing" after the name when the library has lost the file. The header carries the channel
name, the library button, the eye and the lock; the **selection band became one row per lane**
(§12.5's *As built*), and `.os-tl-join.picture` now stops above the two bottom lanes.
**The library modal** (`MusicLibrary.tsx`, new, 248 lines): the index as a table — name,
length, size, mono/stereo, who added it, when — **Upload** (a hidden file input and a status
line, no percentage: `fetch` cannot report request-body progress), **Delete** behind the repo's
`ConfirmDialog` naming the file, because the library is studio-wide and there is no undo, and
**Add at playhead**, which hands the row back to the timeline for `newMusicClip` and one `PUT`.
Every refusal is shown in the server's own sentence: the 409 that names the file, the 409 that
names the projects, the 400 for a file that will not decode or is wider than stereo.
**The gestures**: a clip drag moves `at` (`moveClip`, snapped by `snapClip` over
`clipSnapTargets` — the playhead, the picture's joins, 0, the picture's end and the other
clips' edges, Ctrl to drag freely — and clamped to `total − length`, `total` being the
**audition's** length, `auditionLength(duration, timing.end)`, not the picture's; a clip with no
room left at all stays where it is rather than moving); an edge drag trims
(`trimClip`: the left edge moves `at` and `in` together so the audio stays under the pointer,
the right moves `out`; never past the file, never under 0.1 s, the fades re-fitted);
Delete/Backspace removes the selected clip and Escape clears the clip selection first. Pointer
capture is taken **lazily**, on the first movement (E3's MAJOR), the drag is painted through
refs (`paintClip` writes `transform` for a move, `left`/`width` for a trim — never a `setState`
per frame), and the commit is one `PUT` **on release** (trap 8) under E3's `awaiting` lock.
**The commit**: `editBody(op, committed, sourceDuration)` in `lib/edit.ts` builds every body —
**always a `PUT`, never a `DELETE`** (E3 sent `DELETE /edit` when both tracks went back to
whole, and since E4a a DELETE clears the music too, so that call would have wiped the lane on
the undo of the last cut), and `music` only when the operation changes it, compared in the
body's own terms by `sameMusic` so the read-back's `file_duration` and `missing` never look
like a change. `editRefusal` writes what a refused commit says.
**The inspector**: the row under the strip — the file, where the clip sits, a **linear** level
slider with the studio's `music_volume` marked on it through a `<datalist>`, the two fades as
number boxes, and Remove — each committing on a slider release, a blur or Enter, and each
disabled while a commit is in flight or the lane is locked.
**The audition**: every clip scheduled in `scheduleFrom` on the sentences' own clock
(`clipPlayback` → `start(ctxStart + delay, offset, length)`) through its own `GainNode`
following `fadePoints`' **linear** ramps into one music bus; one decode per FILE, lazily on the
first Play and cached by name beside the sentence buffers (trap 31); a missing clip skipped;
and the eye a mute on the bus — the audition only, never a payload.
**Undo**: `EditState = TrackEdit & { music: MusicClip[] }`, so an add, a move, a trim, a level,
a fade, a removal and a rippled cut are each one operation, fifty deep, and the undo of a
ripple restores the stored BEFORE list rather than recomputing a lossy one.
**The locks and the render line**: `Lane` / `LANES` / `laneLocks` (a 0.8.0 two-key stored value
reads Music **unlocked**), `canCut`, and `renderSummary(video, narration, source_duration,
music)` counting the clips the second pass will mix and saying that the render will refuse
while a file is missing; `ProjectDetail.tsx` labels the button **Render** whenever there is
music, because the job really does the extra pass.

Five things the build changed about the design in §12.5, and six smaller ones, each corrected
in place there under *As built*: the ripple applies only when the PICTURE's list changed and
the scissors is disabled when Music is the only unlocked lane (the owner's ruling); a clip
click does not seek (the owner's ruling); a missing file froze the whole lane, and the way out
is a banner that removes every stuck clip in one commit (the deeper fix is **E4c**); the cut's
bounds are rounded **once**, so a rippled clip lands on the join to the millisecond; and the
selection band is one row per lane rather than one rectangle with offsets. The six smaller ones
are `editBody`, the drag's ceiling, the third-of-a-clip edge zones, the 200-clip refusal on a
cut or a split, the anchored fade envelope, and the confirmation before a library delete.

**The Reviewer's verdict, and what the fix round changed.** CHANGES REQUIRED — one MAJOR, five
MINORs, six NITs — with the arithmetic the strongest part of the change: `cutMusic`,
`trimClip`, `newMusicClip`, `clipPeaks`, `clipPlayback` and `fadePoints` survived every sweep
against independently-written references, and eleven of thirteen planted mutants died. **The
MAJOR was not in the arithmetic**: the UI printed a remedy for a missing file that could not be
carried out, and one missing clip froze every other gesture on the lane behind a 400 naming a
clip the user had never touched (§12.5's *As built*, and trap 35). The five MINORs are all
closed — the band per lane; the ripple keyed on the picture, which the owner then ruled on; the
drag's ceiling; two surviving mutants killed by lifting the body-building into `editBody` and
by two `?raw` assertions (no `exponentialRampToValueAtTime`, which throws on a target of 0 and
would kill the audition outright, and the `awaiting` lock over clip commits) together with real
tests for `snapClip`; and the confirmation before a studio-wide delete — as are five of the six
NITs (the third-of-a-clip edge zones, the 200-clip cap on a cut, the scissors' tooltip now
saying what Ctrl+X does with a clip selected, `togglePlay` waiting when every clip is missing,
and a comment on the `LATE_LEAD` a mid-flight re-schedule costs). The sixth NIT — the clip
click that seeked — went to the owner and came back as a ruling.

**Proven by the Reviewer** (harnesses in the scratchpad, none committed): **6,000** random
`cutMusic` cases against a reference written the other way round, plus **600**
split-idempotence cases — 0 problems — and, on ~**700,000** sampled moments, what is *heard*
after a cut equal to what was heard before it, shifted by the cut; **942** clip lists drawn
from every gesture's bounds fed through the real `services.edit.validate_music`, **0 refused**;
**20,000** audition cases (13,542 scheduled, 6,458 correctly skipped, **2,229 beginning
mid-fade**), 0 problems, with the Web Audio automation timeline reconstructed from `fadePoints`
and compared to `fadeLevel` at twelve instants each; **3,000** peak slices over **17,772** drawn
columns, 0 problems; **nine** payload tests against the real FastAPI app proving the clips
survive every path that used to send a `DELETE`; and an **SSR render of the real component**
(10 tests) confirming five header rows in the lanes' order, the missing clip hatched and named,
and a plan carrying no `music` key at all still drawing.

**Verified live by the supervisor** (Vite against the dev backend, project `c5c8a50e6c3c`, two
real library files): the geometry exact at 12.554 px/s, the fades drawn at exactly 1.0 s and
2.0 s, move / trim / delete / add each committing once on release, a picture cut surviving
every clip gesture, the ripple arithmetically right (a clip's `in` advanced by exactly the
overlap the cut ate, the later clip moved by the cut's length, no invented fades), undo
restoring a lossy ripple exactly and redo reapplying it, the library modal and its refusals,
and the missing-file treatment end to end. Four findings, all fixed: the rippled clip a
millisecond short of the join (§12.5's *As built*, re-proven after the fix with a zero gap);
the summary reading "and … and"; a refusal shown in the server's internal wording alone; and
the level slider wearing the zoom slider's class. **The packaged app is not in that record, and
neither is the owner's own check** — UI behaviour is verified in the browser by the owner, not
by the agents (`CLAUDE.md`).

**A second fix round, from using the banner rather than reading it** (§12.5's *As built* has each
in full). **One:** a change to the library now invalidates the **plan** and the **edit** as well as
the library, through one shared list of keys (`lib/timeline.ts::libraryChangeKeys`) that the
modal's upload and its delete both go through — before it, putting the missing file back under its
name left the banner and its destructive button armed over clips whose file had already returned,
and pressing it deleted them; `removeMissingClips` also guards itself now. That is **trap 37**.
**Two:** a drag leaves a clip with no room left *exactly where it is*, instead of slamming it to 0
and committing that. **Three:** the frozen-lane sentence a refusal leads with is gated on the
server's detail really being about the library, so an unrelated 400 is no longer blamed on the
missing clips. **Four:** the header's three small buttons wear `os-tl-tool` rather than borrowing
`os-tl-lock` — the same lesson as the level slider the live check caught, one file away. The two
rules the fix round made *named* rather than inline — `musicAfterCut` and `clipsAfterDelete` — are
the reason the counts below moved: both are tables now, where each was a line the tests could only
reach by a regex over the component.

**Tests:** `frontend/src/lib/music.test.ts` (new, **99**) — `cutMusic` against the brute-force
reference, the move, the trim and the add at their bounds, the hit test's edge zones,
`snapClip`, the peaks slice, the audition's placement and its envelope, the body a commit
sends, the refusal copy, `musicAfterCut` as a table over all eight lock combinations and
`clipsAfterDelete` as a table of its own, the keys a library change invalidates, and the Render
line's music clauses, plus the `?raw` assertions on the component's own source for the decisions
no pure helper can hold — **24** of them now rather than three, in the one `describe` that holds
them, with two more beside them on the library modal's source. Several of those 24 have been
demoted on purpose: where a rule now has a name, the assertion pins only that the component
*calls* it, and the rule itself is answered by a table.
`frontend/src/components/project/NarrationTimeline.render.test.tsx` (new, **38**, 283 lines) is
the other half: a server-side render of the real component through `renderToStaticMarkup`, which
needs no DOM and no new dependency (`react-dom/server` ships with `react-dom`) and which is what
pins the selection band across all eight lock combinations, the banner's presence and a button's
disabled state — none of which a helper test can see. `frontend/vite.config.ts`'s `include` was
widened to `.tsx` for it; the environment stays `node`. `edit.test.ts` is **byte-identical** to
`131f8c2` at 76, which is what let the Reviewer prove no E3 assertion had been weakened.
**321 vitest across 10 files** (from 184; 237 at the first build, 258 after the first fix round),
`tsc --noEmit` and eslint clean but for the three `react-refresh`
warnings that predate the phase, and the backend **909**, untouched. The Reviewer re-ran every
gate itself rather than taking the Developer's word: 237 vitest, 909 backend in 451.90 s, and
`tests/test_frontend_dist.py` + `test_version.py` green — the committed `dist` matching the
committed source, down to `os-tl-music` really being inside the built chunk. `frontend/dist` is
rebuilt again for the fix round's source.

**Deliberately left:** **E4c**, the one question this build raised and could not answer inside
its own scope — letting `validate_music` keep an already-stored clip whose file has gone, so
the lane stays editable and only the render refuses (§12.5's *As built*; the owner's call); all
of **E5** (trim handles on the pieces E3's split makes, markers, `J`/`K`/`L`, snapping of
cuts); per-range input seeking (E1's second note), still; and **an interactive** component harness
(`jsdom` + `@testing-library/react`), which the Reviewer raised as its own task. Half of what it
raised has landed: the SSR harness above renders the real component and reads the **markup**, which
is what caught the per-lane band and now guards it. The other half is untouched and the Reviewer's
reason for it still stands — nothing short of a DOM reaches the **gestures and the effects**, which
is where both of this project's shipped frontend defects have lived, and the SSR harness says so
about itself.

Budget E1 as phase-3a-sized and E2 as larger than E1: the drawing change touches every
lane and the transport, and it is where the first-open-at-zero-scale class of bug lives.
Every phase passes Developer → Reviewer → Documentation, and E2 is **rendered in the
packaged app before it is believed** — three gates missed 3a's zero-scale strip because
nobody looked.

---

## 8. Traps

1. **Never move the transcript.** If an implementation finds itself rewriting `start`/`end`
   on disk after a cut, it has re-created the dropped-adjustments bug (`services/projects.py:303-316`).
   Project; do not mutate.
2. **No ffprobe, anywhere, ever.** `_FFPROBE_PATH` cannot be queried from outside
   `utils/config.py`; the packaged app has none. Source length = WAV header; output length =
   `output_duration(keep)`; pass it into the mux. (E1: the argument to pass it exists on
   `replace_video_audio` and is unwired — §7, third note — so an edited mux still probes;
   harmless under `-shortest`.)
3. **`FFMPEG_PATH`, not `"ffmpeg"`.** The eleven bare-name sites work by accident of
   `PATH` mutation. The new step is the first code that runs *only* in an edited project
   and must not inherit the accident.
4. **Stream copy cannot cross a cut.** Measured: P-frames at the head, 2 s GOP. Re-encode.
   If someone later proposes "copy the middle ranges and re-encode only the boundary GOPs",
   the answer is the benchmark: 15.8 s for the whole file is not worth the complexity.
5. **No `-r` at all.** `trim` + `setpts` + `concat` carry the source's own cadence through
   (measured: 30 fps in, 30 fps out), and `cut_picture` passes no rate — its test forbids
   one. What this trap guards is that it is never `fps_for_transition`: a screen recording
   rendered at 2 fps is the bug that would follow. (This read "`-r` is the source frame
   rate" until E1, while §4 already said no `-r`; the build is the latter.)
6. **`extra="forbid"` on the edit schema.** Phase 1's lesson, and the whole-list PATCH still
   pays for it (`tests/test_narration_overrides.py:332-341`).
7. **Take `store.project_lock`; never `slides.project_lock`; never none.** The two wrong
   writers in `ai_slides.py` are a smell, not a precedent.
8. **Commit on release, never per frame.** The record is rewritten whole and served whole.
9. **The new picture step must check cancellation** and scale its timeout; the existing
   render checks nothing and has a fixed 600 s. Done in E1 — the flag polled while ffmpeg
   runs and the process killed; the timeout scaled to the **decode reach**, not the output
   (§7, second and eighth notes).
10. **Write to `.part`, publish with `replace_with_retry`.** Windows sharing refusals are
    measured at 2 in 60 (`utils/helpers.py:21-55`), and a render holds several files open
    across seconds.
11. **A sentence can still be pinned past the output's end — by its offset.** The
    projection drops sentences in removed ranges and clamps the rest's `start` to the
    output, but the offset is applied after the projection (§3), so an offset can carry a
    pin past the end under an edit exactly as it can without one. The `past_end` machinery
    (`services/processing.py:1181-1194`, `frontend/src/lib/timeline.ts:38-39`) stays
    load-bearing in both cases. (This read "impossible by construction" until E1; §7,
    fourth note.)
12. **The `revoice_sync_mode == "free"` branch is ignored.** Nothing writes it
    (`core/project_manager.py:66`; no route sets it); an edit is meaningless without pins.
13. **`edit_rendered_at` busts the cache** for the same reason `revoiced_at` does — same
    filename every run (`services/revoice.py:143-151`, `MUTABLE_MEDIA_HEADERS`). Shipped in
    E1, and cleared by a whole-source run.
14. **The stale comment at `api/routers/projects.py:258-260`** said a preview and a full
    render can run at once; `require_idle` refuses the second. Fixed in E1. The re-read is
    still right, but **not** because "an edit can land mid-job", as this trap first said:
    `PUT` / `DELETE /edit` take `require_idle`, so no edit lands on a project a job holds.
    It is right because `record_images_source` writes the record earlier in the same job,
    and a write that passed `require_idle` an instant before the job was registered can
    land on it too (§7, fifth note).
15. **`data/cache` grows** exactly as before (trap 19 of the narration spec). Unchanged, and
    still not to be swept.
16. **Dead code that looks like capability**: `AudioMixer`, `extract_keyframes`,
    `get_video_chapters`, `get_video_duration`. None is on a path; none is a starting point.
17. **Two different ffmpegs.** Development resolves a WinGet 8.0.1 full build; customers run
    the bundled 7.1 essentials. Every claim here was measured on both. A filter added later
    must be checked against 7.1's `-filters` before it is relied on.

---

## 9. What NOT to build

- **Multi-source clips.** Twelve places assume one source (§1.1); the filmstrip and the
  transport drive one `<video>`; the waveform is one WAV. Ranges of one source deliver
  cutting, trimming and ripple delete — which is what the owner cuts *for*. A second source
  is v2, with its own `version` of the edit and its own survey.
- **Keeping or mixing the original audio, ducking.** v2. The narration replacing the
  original is today's product and the reason people use it. Music over the top is **no
  longer on this list**: it is E4 in §7, at the owner's request (2026-09-16).
- **Transitions, callouts, cursor effects, animations, green screen, multi-camera,
  quizzing, screen recording.** Camtasia's, not ours. None serves a narrated demo.
- **Editing while a render runs.** Camtasia does; the one-job rule forbids it; the render
  is ~16 s. Accepted for v1, recorded as the thing to revisit if renders grow long.
- **A server-side undo.** The list is small; snapshots on the client are enough until they
  are not.
- **A stream-copy fast path.** See trap 4 and the benchmark.

---

## 10. Decisions — taken by the owner 2026-09-16 ("go with your proposals")

1. **A sentence that straddles a cut is kept iff its `start` is kept** (§3). Simpler to
   explain, and it matches how mute already works. The alternative — kept if more than half
   survives — was considered and not chosen; it is a one-line change in
   `project_transcript` if it is ever wanted, and it must stay one line.
2. **The join marker is in.** Small, with the removed length in its tooltip (E2).
3. **E1 is committed unversioned**, like phase 3a was. E2 is what gets a number. *(It did:
   0.7.0, 2026-09-17, carrying E1, E2 and 3a together.)*

The owner's standing instruction for E2 and E3 is **"copy Camtasia"** — the gestures, the
look and the feel of its timeline, within the scope §9 draws.

### Critical files

| File | Role |
|---|---|
| `services/edit.py` (new; per track since E3) | validation, `to_timeline`/`to_source`, `project_transcript`, `output_duration`; E3: `TRACKS`, `stored_tracks` (version 1 read as cut together), `Applied` with `video` / `narration` / `projected` / `narration_duration` (`keep` an alias of `video`), the per-track `payload`, the merging `set_edit(pid, video=, narration=)` |
| `services/revoice.py:79-118` | apply the projection before building the engine state (E3: the picture cut with `applied.video`, the engine handed `applied.sentences`, `edit_rendered_at` stamped when either track removes anything) |
| `services/narration.py::plan` | return projected sentences and the edit (E3: `listed` keyed on `projected`, `duration` the picture's when cut); `update_offsets` and `MAX_OFFSET_BATCH` (E3: the batch write, one read-modify-write under the lock) |
| `core/video_creator.py` | `cut_picture`, `cut_filtergraph`, `cut_timeout` (E1); `video_duration` on `replace_video_audio` (E1, unwired) |
| `api/routers/edit.py` (new) | the three routes (E3: the version-2 body and the version-1 body, `keep` beside a track refused, the per-track audit summary) |
| `api/routers/narration.py` (E3) | `PATCH /{pid}/narration/offsets`, audited once as `PROJECT_TRANSCRIPT_TIMING` by indices |
| `api/audit.py` | `PROJECT_EDIT` |
| `api/deps.py`, `api/schemas.py` | `readable_project` / `writable_project`; `EditIn` (E1; per track since E3, `Ranges`), `OffsetIn` / `OffsetsIn` (E3) |
| `frontend/src/lib/edit.ts` (new, E2) | the client copy of the projection (`outputDuration`, `toTimeline`, `toSource`, `wholeSource`, `wholeKeep`), `joins`, `removeRange`, `positionAfterEdit`, `projectPeaks`, the ruler (`ticks`, `tickStep`, `rulerLabel`), the zoom maths, `stepFrame`, `renderEstimate` / `renderSummary`; E3: `splitAt`, `pieces` / `pieceAt`, `snap`, `dragOffsets` / `MAX_OFFSET`, `TrackEdit` / `TrackLocks` / `TRACKS`, `trackList` / `trackBody` / `sameEdit`, `nextEditForCut` / `nextEditForSplit`, `clickSelectsPiece`, `unmovedRelease` / `releaseSuppressesClick`, `renderSummary` per track |
| `frontend/src/lib/edit.test.ts` (new, E2), `tests/fixtures/edit_projection.json` (new, E2) | 76 vitest cases (45 in E2); the projection's pinned cases and, since E3, the `tracks` section (video-only, narration-only, both, together), read by `edit.test.ts` and by `tests/test_edit.py` so the two copies cannot drift |
| `frontend/src/components/project/NarrationTimeline.tsx` (E2, E3) | the Camtasia-shaped panel: timeline-second drawing, the ruler, the handles and the selection, Cut, undo/redo and the commit lock, clip survival, the transport across joins, zoom, the keys; E3: the locks and the selected channel, the pieces and per-lane joins, split, block selection and the marquee, the drag with snapping and the lazy pointer capture, the nudge keys, Reset timing, operation-based undo/redo |
| `frontend/src/pages/ProjectDetail.tsx` (E2) | the `["edit", id]` query, the Render button and its line (E3: when either track removes anything), the edit query's error |
| `frontend/src/lib/timeline.ts`, `frontend/src/components/project/TranscriptCard.tsx` (E2, E3) | `EditPayload` (version 2 since E3: an `EditTrack` per track) and `edit` on `NarrationPlan`; `jobActive` handed to the timeline; E3: `offsets` and `onOffsetsSaved`, the saved sentences folded into the page's copy |
| `frontend/src/styles/theme.css` (E2, E3) | the `--tl-*` tokens and the `.os-tl-*` block; E3: the track-name and lock buttons, the hatch on a locked lane's pieces layer, the pieces, the marquee and the move label, the `chosen` block, the band skipping locked lanes, the per-lane joins |
| `tests/test_edit.py` (new; E2 adds the fixture case; E3 +9), `tests/test_narration_offsets.py` (new, E3), `tests/test_revoice.py`, `tests/test_generation_options.py`, `tests/test_narration_plan.py`, `tests/test_project_ownership.py`, `tests/test_audit.py` | the guards |

---

## 11. E3 — tracks: lock, cut per track, drag the narration (designed 2026-09-17, built 2026-09-18)

Written against the tree at `cab28bc` (0.7.0); **built** over `f637c1d` on 2026-09-18 and
released as **0.8.0** the same day — §7's E3 block records what shipped, the Reviewer's
findings and the proofs — and where the build
differed from the design below, the section says so in place under *As built*. The owner's
words, on the day E2 shipped:
*"I want to be able to select a channel and edit that channel, so for example select video and
edit just that track. That way I can adjust timings."* Two things are asked for at once, and
they are Camtasia's two ways of re-timing narration against picture: **cut one track while
the others stay put** (its lock icons), and **slide the narration along its track** (drag a
clip). E2 cuts every track together — Camtasia with everything unlocked — so both are
missing today, and both can be added without touching the rule that made E1 and E2 safe: the
transcript never moves, and the edit is a projection.

### 11.1 The model — one kept list per track

```json
"edit": { "version": 2,
          "video":     { "keep": [[0.0, 47.3], [52.3, 341.008]] },
          "narration": { "keep": [[0.0, 341.008]] } }
```

- **Two lists, same shape as E1's one**, both in SOURCE seconds, both validated by
  `validate_keep` against the WAV header's length (they are ranges of the one source; nothing
  else changes about what a range is). A track key **absent** means that track keeps
  everything; both absent means no edit and the record reads as one that never had one.
  Nothing else is stored — no lock state, no positions.
- **A version-1 record is read as "cut together"**: `{"version": 1, "keep": K}` means
  `video = narration = K`. `stored_keep`'s rule that a foreign version is refused stays for
  versions this code does not know; version 1 it *knows*, and E1's own §2 said whoever bumps
  the version must read the older shape rather than refuse it. Written back as version 2 on
  the next write. No migration. *As built:* `stored_tracks` does exactly this; a version-2
  record that also carries a top-level `keep` is refused ("mixes two shapes") rather than read
  as either, and `stored_keep` stays as E1's name for the picture's list.
- **The output's length is the picture's**: `output_duration(video.keep)`. The narration
  track has its own length, `output_duration(narration.keep)`, and a sentence pinned at or
  past the picture's end is `past_end` — the machinery E1 kept load-bearing (trap 11) — and
  is dropped by `-shortest` exactly as today.
- **E4's `music` joins this version additively** (a missing key is no music), so E4 bumps
  nothing. *As built:* it holds on write as well as on read — `set_edit` **merges** into the
  stored edit (the version and the two tracks are its to write; a `None` track is removed, a
  version-1 `keep` popped; every other key rides through), so a `music` key survives the first
  cut after it lands. The Reviewer's NIT 7 found the first build rewriting the key from
  scratch; `test_edit.py` pins the merge.

### 11.2 The projection, per track — what changes in `services/edit.py`

`apply(record, transcript, source_duration)` returns both lists and applies **the narration
list** to the transcript: `sentences = project_transcript(transcript, narration.keep)` when
that list removes anything, else the transcript itself, untouched — the byte-identical path
for a project whose narration is whole, which is every project made before this and every
video-only edit. `cut` (whether the picture step runs) is the **video** list's
`whole_source`. Everything downstream keeps its one call:

- `services/revoice.py` cuts the picture with `applied.video` and hands the engine
  `applied.sentences` — no other line changes, and `_revoice_video` is still untouched.
- `services/narration.py::plan` reports `sentences` in the **output's** seconds (the
  narration list's timeline starts at the same zero as the picture's), `duration` = the
  picture's output length, and `edit` in the version-2 shape.
- `to_source` / `to_timeline` for the playhead, the filmstrip and the waveform use the
  **video** list: the picture and its original audio are one file.

*As built:* `Applied` carries `video`, `narration`, `cut`, `projected`, `sentences`,
`output_duration` (the picture's) and `narration_duration`, with `keep` an alias of `video` so
E1's callers stand; the plan's `listed` is keyed on `projected`, never on `cut` (a video-only
edit projects nothing, a narration-only edit everything); `edit_rendered_at` is stamped when
**either** track removes anything, since the output differs from an unedited render either
way; the audit summary is per track ("video: 2 ranges kept, 5.0 s removed; narration: whole");
and `set_edit(pid, video=None, narration=None)` validates both lists before the lock, stores a
`None` track as absent and refuses both `None`. The route keeps the version-1 body `{"keep":
[…]}` meaning both tracks, refuses `keep` beside a track and an empty body with a 400, and a
per-track refusal names the track ("narration: Range 2 […] overlaps …").

What this means on the timeline, in one sentence each — and these are the semantics the
owner is choosing (decision 1 of §11.7):

- **Cut with both tracks unlocked** (today's behaviour, unchanged): the output interval
  `[a, b]` is removed from both lists; sentences whose spoken moment was in it go with the
  picture, later ones close up with it.
- **Cut with Narration locked**: only `video.keep` changes. The picture closes up; the
  narration's pins do not move — so a sentence that was spoken over the removed picture now
  plays over the picture that follows, and every later sentence is *earlier against the
  picture* by the length removed. That is the timing adjustment the owner asked for, and it
  is exactly what Camtasia does to a locked track.
- **Cut with Video locked**: only `narration.keep` changes. Sentences whose start lies in
  the removed narration interval are dropped (E1's rule, applied to this track's list), later
  ones close up; the picture is untouched. This is "take those sentences out and bring the
  rest forward" without touching a single offset.
- **Audio · original follows Video** — it is the picture's own audio, reference only, never
  rendered; it has no lock and the video lock is its lock.

The one-list arithmetic of `lib/edit.ts` and `services/edit.py` is reused as it is: a cut
on a track is `removeRange(thatTrack.keep, a, b)` with the same `[a, b]` — both tracks are
laid out from 0 on the output axis, so the interval means the same instant on each. The
join markers become per lane: the video's joins on the Video and Audio lanes, the
narration's on the Narration lane. The shared fixture gains the per-track cases.

**Split at the playhead.** A split is a **boundary** in a track's list — `[s, e]` becomes
`[s, x], [x, e]` at `x = toSource(playhead, track.keep)` — which E1's `validate_keep`
already accepts (touching ranges), `whole_source` already reads as removing nothing (so the
render of a project that is only split is byte-identical to one with no edit, and the card
keeps saying "Re-voice", not "Render"), and E2's marker already draws as "Split — nothing
removed". `splitAt(keep, t)` is the client helper (E2's `removeRange(keep, t, t)` is the same
list; the name says what the gesture means), a no-op on an existing boundary or at either end
of a range, and the split is committed like a cut — one `PUT` of the track's list. Its value is
what it creates: **pieces**, the stretches of a lane between boundaries and joins, which are the
things a Camtasia user clicks. Split twice, click the piece, Cut — the third way to cut,
beside the handles and Ctrl+drag, and the one that needs no dragging at all. *As built:*
`splitAt` is `removeRange(keep, t, t) ?? keep`; `nextEditForSplit` applies it to the unlocked
tracks (or to all with `Ctrl+Shift+S`), and a split that changes no list commits nothing
(`sameEdit`). The pieces (`pieces` / `pieceAt`) are drawn on all three lanes, and **a plain
click selects a piece only once its lane has more than one** (`clickSelectsPiece`): a
single-piece lane is the whole picture, and a click there seeks as it did in E2 — selecting
the entire picture with one click would only ever arm a Cut that must refuse.

### 11.3 Dragging the narration — the narration spec's phase 3b, delivered here

A sentence block dragged along the Narration lane sets its **`offset`** — nothing else, and
never `start`/`end` (trap 1): `offset = round3(new pinned position − toTimeline(start,
narration.keep))`, clamped to ±`MAX_OFFSET_SECONDS` (300 s) and to the audition's length,
`0` stored as `null` (the List's own rule, `numberChange`). The List view's Offset box
becomes the readout of the drag; the number stays the contract, as the narration spec said.
*As built:* the input to that formula is the pin as **drawn** — `dragOffsets` is given
`pinned_start − start` from the plan, never the page's stored offset, which can be a
plan-refetch behind after a List-view edit (the Reviewer's MINOR 2); the stored value serves
undo's "before" only. And the lock on the Narration track does **not** stop a drag: the lock
scopes cuts and splits — the lists — and a drag commits an offset, not a list (decision 9,
§11.7).

- **Selecting blocks**: click selects one (as now, and seeks); **Ctrl+click** adds or
  removes one; **Shift+click** extends to a range of sentences; a **marquee** dragged on
  empty Narration-lane space selects the blocks it crosses (Camtasia's rubber band). A drag
  that begins on a selected block moves **every selected block by the same amount**; on an
  unselected one, that block alone. Ctrl+drag stays the timeline range selection (E2), on
  every lane, block or not. *As built:* a plain click chooses the sentence (the List's row
  follows) and seeks to where it **lands**; the pointer is captured **lazily**, only once a
  drag has moved past the 3 px slop — capturing on pointer-down retargeted every block click
  to the strip and killed all three (the Reviewer's MAJOR; §7) — and `unmovedRelease` /
  `releaseSuppressesClick` in `lib/edit.ts` say what an unmoved release means per drag kind.
  The marquee is one-dimensional: it selects by the stretch of time it crosses.
- **Snapping** (Camtasia snaps to clip edges, the playhead and markers; Ctrl held while
  dragging disables it): the dragged block's pin snaps, within 8 px at the current zoom, to
  the playhead, to the video's joins, to the other sentences' pins and landed ends, and to
  the block's **own spoken moment** (the ghost) — so "back to where it was said" is a snap,
  not a hunt. Nudge keys for precision: `[` / `]` move the selected blocks ∓/± 0.05 s,
  `Shift+[` / `Shift+]` 0.25 s (the narration spec's nudges; Camtasia has no timeline nudge
  key and `Ctrl+[`/`]` are its marker keys, untouched). *As built:* the candidates also
  include 0 and the picture's end, and the drag is clamped so no member leaves the audition;
  the threshold is 8 px at the current zoom, so at fit zoom on a long source it is seconds.
  The nudge keys work **by physical key or by what it typed** (`BracketLeft` /
  `BracketRight`, or `[` `]` `{` `}`, AltGr allowed), so a QWERTZ layout nudges too; `{` / `}`
  are the quarter-second step, as Shift is.
- **What the drag shows**: the block follows the pointer with its new time in a label beside
  it (as the in/out handles show theirs), the ghost stays at the spoken moment, and after
  the commit the plan says where the sentence **lands** — a pin is a floor, so a block
  dragged into the previous sentence's clip is drawn pushed, with the overrun marker, exactly
  as a typed offset is today. Blocks are never re-sorted (the narration spec's rule). *As
  built:* a ghost outline is drawn at every moved block's origin for the drag's duration (a
  never-nudged block has no plan ghost until the plan lands), and the moved blocks hold their
  transforms until the plan that draws them where they landed is in — cleared at once on a
  refusal, a cancelled pointer, or a release while a key has locked the gesture mid-drag.
- **Commit on release, one request** for however many blocks moved: a new
  `PATCH /api/projects/{pid}/narration/offsets` with
  `{"offsets": [{"index": 3, "offset": 0.4}, {"index": 4, "offset": null}, …]}` —
  `extra="forbid"`, indices in range, offsets validated by the existing rule in
  `services/narration.py` — applied under `store.project_lock` in **one** read-modify-write
  (a drag of twelve sentences must not be twelve interleaving writes), refused with a 409
  while a job holds the project, audited **once** as `PROJECT_TRANSCRIPT_TIMING` with the
  indices and the field name only ("segments 3, 4, 5: offset"), returning the updated
  sentences. The single-sentence `PATCH …/transcript/{index}` stays as it is. The baseline
  memo is **not** dropped: an offset changes where a sentence is pinned, not the texts the
  rate is measured over. *As built:* the answer is `{"sentences": […]}`, each with its
  `index` in the stored transcript; `OffsetIn` / `OffsetsIn` in `api/schemas.py`
  (`extra="forbid"`, a `StrictInt` index, the offset bounded as `SegmentOverride`'s, 1 to
  `MAX_OFFSET_BATCH` = 5000 entries); `narration.update_offsets` resolves every index before
  it applies any, so the twelfth entry's refusal leaves the first eleven unwritten; and the
  service also accepts a numeric-string offset, as `update_segment` always has, where the
  route's strict float refuses it (recorded so nobody tightens one without the other).
- **Reset timing**: a toolbar button (and the block's context) sends `offset: null` for the
  selected sentences through the same route.
- **The audition after a drag**: pins and windows change, texts do not, so the clips whose
  effective speed changed are re-fetched and the rest survive — the E2 rule, already in
  place; the playhead stays where it is.
- **The List view stays in step**: the timeline hands the committed offsets to
  `TranscriptCard` (as `adjust`'s `onSuccess` folds a saved sentence back in) so the boxes
  read the new values without a refetch that would drop half-typed words.

### 11.4 Locks, the selected channel, and pieces — the UI of "select a channel"

Camtasia's lock icon on each track header. A locked lane is drawn dimmed with a hatch, the
selection band skips it, and a Cut or a split applies to the unlocked tracks only (both
unlocked is today's cut; both locked disables Cut and the toolbar says why). Audio · original
shows "follows Video" in place of a lock. **Clicking a track's name selects that channel**,
which is the owner's phrase made literal: it locks the *other* track and highlights this one,
so "select video, and edit just that track" is one click; clicking the selected name again
unlocks both; the lock icons still toggle one track at a time. Lock state is **the client's,
per visit and per project** (remembered in `localStorage` so it survives a reload, never on
the record — it is a gesture modifier, and the durable thing is the edit it produces). The
header column gets the Camtasia look the owner's screenshot shows: the lock, and a muted
eye that is not a control in this phase. *As built:* the lock and the name; the muted eye is
not drawn. A locked lane's hatch is drawn on its pieces layer, above the thumbnails and the
waveform (a background on the film lane was hidden under its images — the Reviewer's NIT 6).

**Pieces.** Each lane draws its stretches between boundaries (splits and joins) as Camtasia
draws clips — a faint edge at every boundary, a hover highlight over the piece under the
pointer — and **clicking a piece selects it**: the in and out handles jump to its ends, the
band spans it (on the unlocked lanes), and Cut removes it. A click on the Narration lane
that lands on a sentence block still selects the sentence (the block is above the piece);
a click beside the blocks selects the piece. Keys, TechSmith's own: **`S`** splits the
selected / unlocked tracks at the playhead, **`Ctrl+Shift+S`** splits every track regardless
of locks. Selecting a piece and pressing Cut is Camtasia's "select the clip, ripple delete".
*As built:* a piece is clickable once its lane has more than one (§11.2's note); a **locked**
lane's piece seeks on click and never arms a cut of the other track (the Reviewer's NIT 2 —
Camtasia does not let a locked track's clips be selected either); the pieces layer is
`.os-tl-pieces.<lane>`; and a selection is clamped to the picture, so a narration piece lying
past a cut picture's end can be neither selected nor cut from the UI (those sentences are
`past_end` and unrendered anyway).

### 11.5 Undo, in the presence of two kinds of change

The client stack becomes a list of **operations**, each with what to send to undo it and to
redo it: a cut stores the whole edit (both lists) before and after; a drag or a Reset stores
the moved sentences' offsets before and after. Undo re-sends the "before"; redo the "after";
fifty deep; pushed only on success; lost on reload — exactly E2's rules, generalised. The
commit lock E2's Reviewer forced (nothing edits while the plan the last commit produced is
still being read) covers offset commits too, since they invalidate the plan as well. *As
built:* an entry is `{undo, redo}`, each an operation holding an edit's two lists or the
offsets before and after; `UNDO_DEPTH` = 50; the tooltips say "a cut, split, drag, nudge or
reset"; a release while a key has locked the gesture mid-drag commits nothing. The `past_end`
copy comes in two variants — with an offset, pull it back; without one (a tail cut with
Narration locked), "the cut picture now ends before it is spoken — unlock Narration and cut
it too, or drag it earlier" (the Reviewer's MINOR 3) — and the Render line is
`renderSummary(video, narration, sourceDuration)`: E2's line when the lists are equal, else
"Cuts 2 ranges of the picture (12.4 s removed) and shortens the narration's timeline by 0.6 s
(sentences spoken in the removed stretch are left out) and re-voices — …", a narration-only
edit saying the picture is not cut.

### 11.6 Traps this phase adds

18. **Two timelines, one axis.** The narration list and the video list each close their
    own holes from the same zero; a sentence's output position is
    `toTimeline(start, narration.keep) + offset`, its picture is `toSource(t, video.keep)`.
    Anything that projects the narration through the *video* list has re-created E2's
    "cut together" — fine when the lists are equal, wrong the moment they are not, and the
    per-track fixture cases exist to catch it.
19. **A locked track is untouched, not re-derived.** Cutting the picture with Narration
    locked drops no sentence and moves no pin, even sentences spoken over the removed
    picture — the render plays them over what follows. That is the point, and the audition
    shows it before any render.
20. **The drag commits `offset`; nothing commits `start`/`end`.** Trap 1, restated for the
    one gesture that looks like it moves a sentence.
21. **One write per gesture.** A marquee of twelve blocks dragged together is one PATCH under
    the lock, never twelve; the whole-list Save and the single-sentence PATCH keep their own
    concerns.
22. **Version 1 is read, not refused.** The only foreign version is one this code has never
    seen.
23. **A split changes no output.** `whole_source` is true for a list that only splits, so the
    picture step is skipped, the render is byte-identical, the card says "Re-voice" and the
    audition is unchanged. The marker and the pieces are the only evidence, and that is
    correct: Camtasia's split changes nothing either until a piece is moved or removed.

### 11.7 Decisions for the owner before the build

1. **Locked-track semantics as §11.2 states them** — a video-only cut leaves every pin where
   it is (Camtasia's rule). The alternative — still dropping the sentences whose spoken moment
   was cut — was considered and not proposed: "locked" would then not mean untouched.
2. **Lock state is per visit, not stored** (like undo). The alternative — on the record — is
   a one-key change if a stored lock is ever wanted.
3. **The drag commits `offset` only**; the List's box becomes its readout.
4. **Snapping on by default; Ctrl held while dragging disables it** (Camtasia's rule).
5. **Nudge keys `[` `]` (0.05 s) and `Shift+[` `Shift+]` (0.25 s).**
6. **Release**: E3 and E4 together as **0.8.0**, or E3 alone first — the owner's call when
   E3 is built. *(Taken 2026-09-18, the day E3 was built — "cut a build and release 0.8.0":
   **E3 alone, as 0.8.0**, ahead of E4, together with the narration transcript download, T1.)*
7. **"Select a channel" = click its name: the other track locks.** One concept underneath
   (locks), one click on top. The alternative — a separate "active track" state beside the
   locks — was not proposed: two ways to say which track an edit touches would disagree.
8. **Split (`S`) applies to the selected / unlocked tracks; `Ctrl+Shift+S` to all**, and a
   piece is selected by clicking it. Trim handles on a piece's edges stay E5.
9. *(Taken by the build, 2026-09-18, for the owner to overturn:)* **the Narration lock scopes
   cuts and splits, not drags.** A block on a locked Narration lane can still be dragged: the
   lock protects the track's list, and a drag commits an offset, never a list. The
   alternative — refuse the drag while the lane is locked — is a one-line check in
   `onBlockPointerDown`.

Build order inside the phase: the model and routes (version 2 read/write, `apply` per track,
the offsets route — verifiable by `curl`, the E1 way), then the timeline (locks, per-lane
joins, marquee, drag with snapping, the operation stack), each through Developer → Reviewer →
Documentation, rendered in the packaged app before it is believed. *As built:* in that order,
both halves through all three gates; the live check was the supervisor's, on Vite against the
dev backend — the packaged app is not in the record (§7).

---

## 12. E4 — the music lane (designed 2026-09-21; E4a built 2026-09-21, E4b 2026-09-22)

Written against the tree at `dd818d0` (0.8.0 plus two clock fixes); **E4a — the library, the
model and the render pass — was built the same day**, over `24f9cd3`, and **E4b — the lane,
the gestures, the inspector and the audition — the day after**, over `131f8c2`. Each went
through all three gates and a fix round after the Reviewer's findings (§7's two blocks record
what shipped and the proofs), and both are **unreleased**: the version stays 0.8.0 until 0.9.0
ships them together. Where a build differed from the design below, the section says so in place
under *As built*; the ones that matter most are §12.4's up-mix, §12.6's one rule for every key,
and §12.5's two owner rulings and the lane a missing file froze.

The owner's words, 2026-09-16, mid-E1: *"I should be able to add music tracks over the top —
another channel like Camtasia"*; and their own Camtasia project, shown on the 17th, has
exactly that: Track 3 holds *"Amarent — The Man from Hyde Park — Ambient Mix"* laid twice
along the timeline under the picture and the narration. Scoped 2026-09-21 at *"scope E4"*.
This is the third track, and it is different in kind from the two E3 gave locks to: the
picture and the narration are **material with an edit**, ranges of one source; music is
**clips placed on the output** — Camtasia clips, with a file, a position, an in and an out.
The one-list shape does not hold it, and §7's sketch already said so: version 2 gains a
`music` list, additively.

### 12.1 What exists, and what does not

- **The re-voice has never mixed music.** `replace_video_audio` (`core/video_creator.py:300-357`)
  can overlay ONE file looped to the narration's length at a "rough dB" level
  (`20 × volume − 20`), but `_revoice_video` hands it `background_music_paths[0]`, and
  `services/revoice.py` constructs the processor with no music at all — so `bg_music` is
  `None` on every re-voice a video project has ever had. The generate path mixes a playlist
  through moviepy (`_apply_background_music`, `:1010-1070`: concatenated, looped or trimmed,
  a linear volume factor, one fade in and out). Neither is a lane, neither is placed, and
  E4 replaces neither: the re-voice gains its own music step (§12.4) and the generate path
  keeps the playlist it has.
- **There is no library.** `services/styles.py:15` names `MUSIC_DIR = assets/music`; nothing
  creates it, nothing serves it, and `api/app.py:79`'s `/assets` mount is the FRONTEND's
  hashed chunks, not the repo's `assets/` — a music file must be served by a route. Vertical 2b
  (`docs/porting/generation-options.md` §4) surveyed this and was never built; E4 builds it,
  once, for both paths.
- **The edit is ready for it.** `stored_tracks` reads version 2 and lets a key it does not
  know ride through; `set_edit` merges rather than rewrites (E3's Reviewer, NIT 7), so a cut
  made after a music clip lands does not drop the clip. `TRACKS = ("video", "narration")` is
  the picture's and the narration's business only.
- **The timeline draws three lanes** with the lane count baked into the CSS
  (`.os-tl-headers` rows `repeat(3, …)`, `.os-tl-selection.no-video` / `.no-narration`,
  `.os-tl-join.picture`, the `.os-tl-pieces.*` tops — `theme.css:424-517`). A fourth lane
  means those become per-lane rules, not a fourth copy of each.
- **The audition schedules one-shot buffers on one clock** (`scheduleFrom`,
  `NarrationTimeline.tsx:715-728`): a `Map<index, AudioBuffer>` of decoded sentence clips
  and `node.start(when, offset)`. A music clip is the same node with three more things —
  a `duration`, a `GainNode`, and ramps for the fades.
- **ffmpeg, not ffprobe** (traps 2, 3): a music file's length must come from decoding it
  once at upload (pydub finds the bundled ffmpeg through the PATH prepend,
  `utils/config.py:70-71`), never from a probe. *As built:* right about the decode and wrong
  about pydub, which runs **ffprobe** for anything not named `.wav` — the seam runs
  `FFMPEG_PATH` itself, for the reason set out in §12.3's *As built*.

### 12.2 The model — clips on the output axis

```json
"edit": { "version": 2,
          "video":     { "keep": [[0.0, 47.3], [52.3, 341.008]] },
          "narration": { "keep": [[0.0, 341.008]] },
          "music": [ { "id": "m3f9a1", "file": "Amarent - The Man from Hyde Park.mp3",
                       "at": 12.5, "in": 0.0, "out": 95.25,
                       "gain": 0.15, "fade_in": 1.0, "fade_out": 2.0 } ] }
```

- `music` is a list of clips, ordered by `at`; absent or empty means no music. Version stays
  **2** — additive, as §7 promised; `stored_tracks` is unchanged and a new `stored_music`
  reads the list beside it.
- `id`: client-minted (`^[a-z0-9_-]{1,32}$`, unique in the list) — the selection, the undo
  stack and the inspector need a handle that survives re-ordering; the server validates
  uniqueness and never renumbers.
- `file`: a name in the library (§12.3), sanitised exactly as the library sanitises on
  upload, and **checked to exist at write time**. At read time a file that has since gone
  is reported as `missing: true` on the clip — the plan's `edit` block carries it, the lane
  draws the clip hatched with a warning, the audition skips it, and the render **refuses**
  ("music file X is missing — remove the clip or upload the file again"), never renders
  silence in its place (the E1 rule for an unreadable edit, applied here).
- `at` ≥ 0 in **OUTPUT seconds** — the picture's axis, the one the ruler shows. That is
  what makes the lane behave as Camtasia's does under a cut (§12.5), and it is also what
  decides *when* a cut ripples at all: because the axis is the picture's, the clips move
  only when the **picture's** own kept list changed — Video unlocked — and the Music lane's
  own lock then decides whether its clips come along. With both unlocked a ripple delete
  before a clip moves it earlier with the picture and one through a clip trims it; with
  Music locked, or with **Video** locked (where the output's length does not change at all),
  the clip stays at its time. That is the owner's ruling of 2026-09-21, which narrowed the
  rule this bullet first stated — §12.5's *As built* on `musicAfterCut` has it, and trap 24.
- `in` / `out` in the FILE's seconds: `0 ≤ in < out ≤ file duration` (the library's
  recorded length), `out − in ≥ 0.1`. The clip's length on the timeline is `out − in`;
  there is no stretching, no looping (a second lap is a second clip — Camtasia's model, and
  the owner's own Track 3 shows two copies).
- `gain` in **[0, 1], a linear factor** applied by the render's `volume=` filter and the
  audition's `GainNode` alike — never the old pydub "rough dB" formula (trap 26). The
  default for a new clip is the studio's `music_volume` (`services/studio_settings.py:73`,
  0.15 today: −16.5 dB), which is exactly the "one static lower level under the voice" the
  owner ruled on ([[feedback_audio_ducking]]). **No ducking**: the level is constant across
  the clip whether the narrator speaks or not.
- `fade_in` / `fade_out` ≥ 0 seconds, together at most the clip's length; **linear** ramps
  (ffmpeg's `afade` default curve `tri` and the browser's `linearRampToValueAtTime` are the
  same shape, so the audition and the render agree). Music may fade; **the voice never
  does** ([[feedback_video_audio]]) — nothing here touches the narration master.
- At most 200 clips; overlapping clips are allowed and sum (two beds cross-fading by hand
  is the ordinary use). Validation names the clip by index and field, as `validate_keep`
  names a range.

### 12.3 The library — vertical 2b, built once

`assets/music/` (`MUSIC_DIR`; inside the install tree beside `assets/finished`, where it
survives reinstall as the projects do) plus an index the routes read instead of decoding:

- `POST /api/music` — multipart upload (the `UploadFile` pattern of `import_upload`,
  `api/routers/projects.py`); accepted by extension `mp3 wav m4a aac ogg flac`, then **decoded
  once** with pydub (`AudioSegment.from_file`) to prove it is audio and to measure it — a file
  that will not decode is a 400, not a stored surprise; written to `.part` and published with
  `replace_with_retry` (trap 10); size cap 100 MB; the name sanitised with
  `utils.helpers.sanitize_filename` and **unique — an existing name is a 409** ("rename the
  file or delete the old one"), because a clip refers to a file by name and a silent
  replacement would change every project that uses it. On upload the server also computes
  and caches the file's **peaks** (`<name>.peaks.json`, the waveform module's 125 ms buckets
  from the decoded samples — pull the bucket arithmetic out of `services/waveform.py` into a
  helper both callers use, never a second copy) and appends to `assets/music/index.json`:
  `{name, size, duration, sample_rate, channels, uploaded_at, uploaded_by}`. Audited as
  `MUSIC_UPLOAD` (name and size, no more). *As built:* everything above holds except the
  decoder. **It is not pydub.** `AudioSegment.from_file` takes its `_from_safe_wav` shortcut
  only when the *filename ends in `.wav`*; for anything else it calls `mediainfo_json`, which
  runs **ffprobe** — and `get_prober_name` merely warns and returns the bare `"ffprobe"` when
  the binary is absent, so the call raises. The decisive detail is that the upload decodes
  `<name>.part`, whose extension is never `.wav`, so pydub would have taken the ffprobe path
  *even for a WAV upload*; its `AudioSegment.converter` is the bare `"ffmpeg"` as well (trap
  3). So `services/music.py::decode_audio` is the seam and it runs the resolved `FFMPEG_PATH`
  itself — one process, `-vn` for cover art and `-map_metadata -1`, into a scratch WAV under
  `TEMP_DIR` that the stdlib `wave` module reads and that is always removed — with a 600 s
  timeout and the three-attempt retry on "permission denied" that
  `core.audio_mixer._load_audio_with_retry` exists for. It answers `duration`, `sample_rate`,
  `channels` and one int16 sample per frame, mixed down by the strip's own rule (per frame,
  the louder channel, sign kept), so a stereo file's peaks here are the ones `compute_peaks`
  would draw for it. The peaks helper the bullet asks for is two functions, not one —
  `waveform.peaks_from_samples(samples, sample_rate)` and `waveform.peaks_payload(peaks,
  duration, sample_rate)`, with `compute_peaks` refactored onto both — because the library
  needs the response shape as well as the numbers. And a refusal names **the upload**, not the
  scratch file: `decode_audio` takes a `display_name`, and the ffmpeg tail is reduced to its
  first path-free line with the `[png @ 0x…]` prefix stripped, so the 400 reads `'fake.mp3' is
  not an audio file this app can decode (chunk too big)` and carries no server path.
- `GET /api/music` — the index, sorted by name.
- `GET /api/music/{name}` — the file, `FileResponse`, served for the audition; the name is
  immutable so `Cache-Control: private, max-age=86400` is right (unlike the rewritten
  outputs). `GET /api/music/{name}/peaks` — the cached peaks.
- `DELETE /api/music/{name}` — **refused with a 409 naming the projects** while any project's
  edit refers to the file (a scan of the store's records; a few files, milliseconds); else
  removes the file, its peaks and its index row; audited `MUSIC_DELETE`.
- The library is **studio-wide** — shared by every account, like the voices and the studio
  settings; every signed-in user may list, upload and delete (the audit row says who).
  Routes live in a new `api/routers/music.py`; they are not project-scoped, so they join the
  ownership sweep's *exclusion* list with a comment, not its table.
- **Where the packaged app puts it**: `<install>\app\assets\music`, next to `finished` —
  kept across reinstalls, deleted with nothing (a project's delete never touches the
  library). The data-dir move (§ NEXT in memory) carries it along when it happens.

***As built* — a name is unique CASE-INSENSITIVELY and looked up EXACTLY.** The section says
"unique" and means it on a file system that is case-sensitive; Windows' is not, and the index
was. `DELETE /api/music/BED.mp3` against a library holding `bed.mp3` therefore walked straight
through the guard this section exists to be: `"BED.mp3" not in files` was true, `path.is_file()`
was true (it *is* `bed.mp3`), so the rowless-file escape hatch passed; `references("BED.mp3")`
compared a string no clip stores and found nothing; the file and its peaks were unlinked **by
path**; and the row survived because the name it is keyed by is the other spelling — 204, a
referenced file deleted, and a phantom row (the Reviewer's MAJOR 1, §7). So: an upload collides
under `str.casefold` with an indexed name, an on-disk name **or** one mid-upload (its `.part`),
on every platform, so the index can never hold two names Windows cannot tell apart and the two
platforms behave alike; and `get_path`, `peaks`, `remove`, `references`, `library` and
`duration_of` are all **exact** — the indexed name is THE name, a request matching only by case
is a `MusicNotFound` (404), and the on-disk check is membership of `os.listdir(MUSIC_DIR)`,
which reports the spelling a file was created with, never `Path.is_file()`, which answers a
different question. The escape hatch stays — a file on disk without a row, from an interrupted
upload or an index that could not be read, must be removable so a name never gets stuck — but
it fires only on an exactly-spelled on-disk name. **And `_path_for` creates the directory
before it resolves.** Two `Path.resolve()` calls around a directory that is springing into
existence disagree on Windows — the second answers in the `\\?\` extended-length form — so
`path.parent != base` for a perfectly ordinary name: 108 of 150 concurrent first uploads into
an `assets/music` that did not exist yet, each a **500** from `POST /api/music`, which caught
`TooLarge`, `MusicExists`, `BadName` and `NotAudio` but not `MusicNotFound` (the Reviewer's
MINOR 1). The directory is created first, the path resolved once, and the "directly inside"
check compares `os.path.normcase(os.path.abspath(...))`; the upload route catches
`MusicNotFound` as a 400 for belt; and a ten-thread test uploading ten names into a library
directory that does not exist yet pins both that and the module lock (without the lock, 8 of
10 uploads fail and the index keeps 2 rows).

### 12.4 The render — one more pass, and `_revoice_video` still untouched

E1 put the picture cut BEFORE the re-voice; E4 puts the music AFTER it, as a second ffmpeg
pass over the muxed output — the one shape that leaves `_revoice_video` and the mux exactly
as they are and keeps the standalone narration track voice-only (it is downloaded to be laid
into other editors, and music baked into it would be a regression of that promise):

1. `services/revoice.py` reads the clips (`edit.stored_music`), refuses a missing file
   (§12.2), and after `_revoice_video` has written `<stem>_revoiced.mp4` calls
2. **New `core/video_creator.py::mix_music(video_in, clips, video_out, cancel_check)`** —
   one ffmpeg run, `FFMPEG_PATH`:
   ```
   -i revoiced.mp4  -i fileA.mp3  -i fileB.mp3 …
   -filter_complex "
     [1:a]atrim=start=IN:end=OUT,asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo,
          volume=GAIN,afade=t=in:st=0:d=FI,afade=t=out:st=LEN-FO:d=FO,adelay=AT|AT[m1];
     … one chain per clip, the same input reused for every clip of the same file …
     [m1][m2]…amix=inputs=N:normalize=0:dropout_transition=0[bed];
     [0:a]aformat=channel_layouts=stereo[v]; [v][bed]amix=inputs=2:duration=first:normalize=0[a]"
   -map 0:v -map "[a]" -c:v copy -c:a aac -b:a 192k -y out.part.mp4
   ```
   `normalize=0` on both mixes (trap 29 — `amix`'s default divides by the input count and
   would halve the voice); `duration=first` ends the bed at the picture; `aformat` to stereo
   before any mix (the TTS master is mono, most music is stereo); **`-c:v copy`** — the
   picture is not touched (a few seconds, not a re-encode); `.part` + `replace_with_retry`;
   the cancel flag polled and the process killed as `cut_picture` does; timeout
   `60 + 2 × output length` (the work is decoding the music, bound by the output's length).
   Every filter named here is in ffmpeg 7.1 essentials' core set (trap 17 says: check
   `-filters` on the bundled binary before relying on it — the Developer does, in a test that
   reads the real binary's filter list when it is present).
3. `edit_rendered_at` is stamped when the clips exist (the output differs from an unedited
   render), as it is for a cut; the record gains `music_rendered: N` for the page.
4. The legacy one-file overlay in `replace_video_audio` is left alone — it is the generate
   path's and it is not on this path.

Cost: decoding two five-minute tracks and re-muxing a 341 s output, measured before the
build (§12.8 asks for the number); expected a handful of seconds. The Render line says
"… and mixes 2 music clips under the narration".

***As built* — the up-mix above is wrong, and it is `pan`, not `aformat`.** Step 2 names
`aformat=channel_layouts=stereo` for the clips and for the voice. ffmpeg's default mono→stereo
rematrix is **power-preserving** — each output channel gets M/√2 — so a **mono** narration came
out **3.01 dB quieter** with music under it than without, from the up-mix alone and not from
`amix` (`normalize=0` was doing its job: stripped, the voice drops to ×0.354 instead), and this
section's own claim that "the narration's level is untouched" was false for it. That is not a
hypothetical configuration: the local Kokoro generator writes mono at 24 kHz
(`core/kokoro_tts_generator.py:439`), the mux sets no `-ac` and the TTS master forces no
channel count, and `data/projects/c5c8a50e6c3c/welcome_preview_revoiced.mp4` on this machine is
one — so it is the **free/local iteration path**, the one the owner uses before an ElevenLabs
final, that was hit (the Reviewer's MAJOR 2). The graph now uses one constant,
`UPMIX_STEREO = "pan=stereo|FL=FL+FC|FR=FR+FC"`, for the voice **and** for every clip: `pan`
copies the channels it names and silently drops the ones the input does not have, so one string
serves a mono and a stereo input alike with nothing probed (traps 2 and 30), which is what the
alternatives could not do — `pan=stereo|c0=c0|c1=c0` collapses a stereo R onto L, and
`aresample=rematrix_volume=1.414214` or a `volume=1.414214` after the `aformat` fixes mono by
making stereo **+3 dB**. Measured per channel against the source on the dev ffmpeg 8.0.1 **and**
on the bundled 7.1 essentials:

| voice graph | mono in | stereo in | then `amix` with a stereo bed |
|---|---|---|---|
| `aformat=channel_layouts=stereo` (as designed) | −3.01 dB | 0.00 | — |
| `pan=stereo\|FL=FL+FC\|FR=FR+FC` (as built) | 0.00 | 0.00 | 0.00 |

Proven live on a real render of the mono project: in a window with no music under it the voice
is within **0.03 dB per channel** of the standalone narration, and in a window that has music
the music sits **19.4 dB** above the floor. It matters for E4b as much as here — Web Audio's
mono→stereo up-mix is **unity** (L = R = M), so with `pan` the audition and the render agree
exactly, which is what decision 4 promises and what `aformat` would have broken by 3 dB on
every mono project.

***As built* — the stamps go on BEFORE the pass, not after it.** Step 3 stamps once the mix has
run. As built the mux's stamps — `revoiced_at`, `edit_rendered_at` when either track removed
anything, and `music_rendered` **popped** — are written as soon as `_revoice_video` has produced
the file and before `mix_music` is called, and the success path stamps again: a new
`revoiced_at` and `edit_rendered_at` (the bytes changed once more, and that timestamp is the
page's cache token) and `music_rendered = N`. The pass rewrites `<stem>_revoiced.mp4` **in
place** and can be cancelled or fail, which was not possible before E4a — the only cancel point,
the picture cut, ran before `_revoice_video` touched that file — so stamping only afterwards
left a record claiming the *previous* render's `music_rendered` over bytes that no longer
carried it, with the cache token unchanged although the file had changed (the Reviewer's
MINOR 2, proven for the cancelled path and the failed one). A cancelled or failed mix now leaves
a record that truthfully describes the voice-only render on disk.

***As built* — the rest of the pass.** `mix_music` and `cut_picture` share one loop:
`_run_until_done(cmd, log, timeout, cancelled)`, the `Popen` / poll / kill body lifted out of
the cut, stderr to a file rather than a pipe nobody reads, answering `"finished"` /
`"cancelled"` / `"timeout"` and the process. The Reviewer reconstructed `24f9cd3`'s loop
verbatim and ran both callers against the same scripted fake `Popen` over eight terminal cases,
with return value, kill, poll count, destination bytes and leftovers identical in every column,
so the extraction is behaviour-preserving (§7). `music_timeout(output_seconds) = 60 + 2 ×
output_seconds` as written, sized by the OUTPUT and handed `applied.output_duration` — the cut
picture's length, not the source's; when the caller does not know it (the source's length is
unknown) the same rule is applied to the clips' furthest end. The unit test pins
`music_timeout(3600) == 7260` and the deadline test separates the two candidate rules far enough
to tell them apart, because a one-hour output with a single 15 s sting is 7260 s against 90 s
and the wrong rule would kill a legitimate render. `music_filtergraph([], …)` **raises
`ValueError`** rather than emitting a graph that names an `[m1]` which does not exist; the fade-in
start prints at 3 dp like every other number; the pass writes `<stem>.music.part.mp4` beside the
output (`with_suffix` replaces the extension) with its log beside that, and publishes with
`replace_with_retry`; and the job reports **0.95**, not 0.9 — the mux has already reported up to
0.925, and a progress bar must not step backwards.

### 12.5 The lane, the gestures, the audition

- **A fourth lane, Music**, under Narration, with E3's lock and channel-name button (the
  channel selection locks the other two). The headers grid, the selection band, the join
  markers and the pieces layers become per-lane rules (§12.1's last bullet). Clips are
  Camtasia's: dark rounded blocks with the file's name, the file's **waveform inside** from
  the cached peaks sliced `in…out` (pooled as the audio lane's is), the fades drawn as
  triangles at the ends, a hatch and a warning when the file is missing.
- **Add music**: a button on the Music header opens a **Library** modal (the `Modal`
  primitive): the index as a table (name, length, size, who, when), **Upload** (a file input,
  progress, the 409 on a duplicate shown by name), **Delete** (the 409 naming projects
  shown), and **Add at playhead** — which places a clip at the playhead, `in = 0`,
  `out = min(file length, output − at)`, gain = the studio's `music_volume`, the default
  fades of decision 2, and commits it (one `PUT /edit` with the new `music` list; the
  drawing, as always, is the refetched plan).
- **Move**: drag a clip along the lane — `at` changes, snapping to the playhead, the
  picture's joins, 0, the picture's end and other clips' ends (the E3 machinery,
  `snap`). **Trim**: drag a clip's left or right edge (an 8 px zone) — `in` or `out`
  changes, the clip's length with it; never past the file's length, never below 0.1 s.
  **Remove**: select a clip and press Delete / Backspace (Camtasia's "delete selected
  media"; with a clip selected these keys act on the clip, not on the range selection —
  the clip selection wins while it exists, Escape clears it first). Every commit is one
  `PUT`, on release (trap 8), with the whole `music` list.
- **A cut with the Music lane unlocked** applies E3's ripple to the clips: `cutMusic(clips,
  a, b)` — a clip wholly after `[a, b]` moves earlier by `b − a`; one that spans the cut
  is **split into two** (the part before keeps its `at`, the part after starts at `a` with
  `in` advanced by the cut's overlap) — the two pieces are what Camtasia leaves too; one
  wholly inside is removed. Locked, the clips are untouched (trap 19). The same helper
  serves `S` (a split of the Music lane at the playhead makes two clips).
- **Inspector**: with a clip selected, a row under the strip — the file, `at`, `in`/`out`,
  a **gain** slider 0–1 (default marked), **fade in** / **fade out** seconds — each a
  commit on change (blur/Enter or slider release), like the List's boxes.
- **The audition** fetches each distinct file once (`GET /api/music/{name}` → `api.blob` →
  `decodeAudioData`, cached in a `Map<name, AudioBuffer>` beside the sentence buffers) and
  schedules every clip in `scheduleFrom` on the same clock: `source.start(ctxStart +
  max(0, at − from), in + max(0, from − at), out − in − max(0, from − at))` through a
  `GainNode` whose value follows the clip's fades with linear ramps (decision 4). A clip
  whose file is missing is skipped. **The eye on the Music header gets its job**: it mutes
  the music in the audition only ("hear the voice alone"), never in the render. Decoded
  music is PCM — a five-minute stereo file is about 100 MB in memory — so the audition
  decodes a file once and the copy line says a long library is expensive to audition.
- **Undo**: the operation stack's edit entries hold `video`, `narration` AND `music`; a
  clip's move, trim, gain, fades, add and remove are edit operations, and E3's lock covers
  them.
- **Render summary**: "Cuts 1 range of the picture (5.0 s removed), mixes 2 music clips under
  the narration and re-voices — about 30 s …".

***As built* — the owner's first ruling: THE MUSIC RIDES THE PICTURE.** The bullets above key the
ripple on one thing, the Music lane's own lock — unlocked, a cut moves, trims and splits the
clips. That reading is coherent (each unlocked lane ripples on its own axis) and it is what makes
"click Music, select a range, cut" remove a stretch of music alone; but it disagrees with §12.2 in
exactly one combination, and the Reviewer isolated it rather than judging it — it did not call the
code wrong, it said the code had not chosen (MINOR 2). With **Video locked** and Music unlocked the
picture's output length does not change — `duration` is the picture's — and yet every clip moved
earlier by `b − a`, sliding the bed out from under the frames it was placed against. The
combination is reachable only through the individual lock icons, never through the channel names.
`at` is on the picture's axis (§12.2), so the owner's
answer, 2026-09-21, is that a cut which does not shorten the picture must not move a clip at all:
the ripple applies **only when the picture's own kept list changed**, which is exactly when Video
is unlocked. The ruling now lives as **one named rule in one place** —
`lib/edit.ts::musicAfterCut(clips, locks, a, b)`, whose whole body is
`return locks.music || locks.video ? clips : cutMusic(clips, a, b)`, called by `cutSelection` in
`NarrationTimeline.tsx`. It was first written as a line *inside* that handler, and the Reviewer
showed what that cost: the owner's ruling was pinned by nothing but a regex over a 3,200-line
component, which a rename or a reformat walks straight through, and no test could ask the ruling
itself a question. As a function it is a **table over all eight lock combinations** in
`lib/music.test.ts`, asserting for each whether the cut ripples, and the component's `?raw`
assertion is demoted to what it should always have been — proof that the cut is the *caller*, not
proof of the rule. Cite the symbol, not the line: this one's line number was in these notes, and
the round that moved the rule out of the component made it point at nothing.

The ruling creates a consequence the lane has to answer for, and it is not allowed to answer it by
inventing a different rule. Clicking the **Music** channel's name locks the other two (§11.4's
rule, generalised to three lanes), so a range cut with Music as the only unlocked lane now moves
nothing: the scissors would appear to work and do nothing. It is therefore **disabled** in that one
combination — `canCut(locks)` in `lib/edit.ts`, which refuses "every lane locked" and "Music alone"
and nothing else — with a tooltip naming the two ways to change the music by itself: unlock Video
and cut both, or use the clip's own gestures (drag its ends to trim it, or select it and press
Delete). The keys refuse the same way rather than sending a `PUT` that changes no clip. **`S` is
unaffected**: it still splits a clip at the playhead, on the unlocked lanes or (Ctrl+Shift+S)
regardless of the locks, because a split changes no clip's `at` and so cannot put the music out of
step with the frames.

***As built* — the owner's second ruling: A CLIP CLICK DOES NOT SEEK.** As first built, a click on
a clip called `seek(clip.at)`, matching the sentence block's click; `seek` halts playback and
starts it again at the target, so reaching for the inspector to nudge a bed's level *while
listening* threw the playhead back to the clip's start every time (the Reviewer's NIT 2, raised as
the owner's call). The owner's answer, 2026-09-21: **selecting a clip only selects it** — the
playhead, the transport and any running playback are left exactly as they are
(`NarrationTimeline.tsx::onClipClick`, which sets the selection and nothing else; `onBlockClick`
beside it is the one that seeks). That is what makes an inspector under the strip worth having,
and it is what Camtasia does. A **sentence block's click still seeks**, deliberately (E3): the two
gestures are meant to differ, and the difference is not to be "harmonised" away by a later phase.

***As built* — a missing file froze the EDITOR, not just the render; the way out is a banner.**
This section and trap 25 both assume a lost library file costs the audition a clip and the render a
refusal. It cost more than that. **Every music commit sends the whole list** — that is what a lane
of clips is, and the bullets above say so — while `services/edit.py::_check_music:394-395` refuses
to store any list naming a file the library does not have. So with §12.2's own ordinary case, **the
same bed laid twice** (there is no looping, so one lost file makes two missing clips), removing
either half leaves the other in the body and the call is refused; and with one live clip beside one
missing one, moving the live clip is refused by the *missing* one's name — "music clip 2 (sting):
file 'musicB.mp3' is not in the library" — naming a clip the user never touched and numbering it
by a position they cannot see. The Reviewer proved all of it against the real API (MAJOR 1):
`PUT {music: []}` is the only body the server will take, and **no gesture in the UI sent it**; the
undo stack is per visit, so a reload lost even that. The client's answer, built here: the banner
that names the stuck clips carries a **button which commits the list minus EVERY missing clip in
one `PUT`** (`removeMissingClips`), and **Delete on a missing clip does the same** (`removeClip`).
Both are the same gesture and, as of the second fix round, literally the same rule —
`lib/edit.ts::clipsAfterDelete(clips, id)`: the named clip goes, unless it is missing, in which case
every missing clip goes with it, because that is the only body the server will store; an id the
list does not hold leaves the list alone. It was two filters inlined in two handlers before that,
which is the same defect as the ripple's — a decision with no name, pinned only by a regex over the
component — and it is now a table of cases in `lib/music.test.ts` (the ordinary delete, one missing
of two, all of them, the id that is not there — a stale selection must not clear the lane — and
that the clips it keeps are **the same objects**, never re-ordered or rewritten). The banner's
copy says plainly that nothing on the
lane can be moved, trimmed, added or removed until they are gone. `editRefusal` puts that sentence
in front of the server's own, which is kept whole rather than replaced — a refusal this client does
not recognise must still reach the user.

That is a client-side remedy for a server-side rule: the right size for E4b, not the right fix.
**The deeper one is E4c, it is open, and it is the owner's call**: let `validate_music` accept a
clip whose file is missing *when that exact clip is already stored* — "you may keep what you have,
you may not add what is not there" — so the lane stays editable and only the render refuses, which
is all trap 25 ever claimed. Until that is taken, `missing` is a modelled state the editor can
leave only by deleting.

***As built* — the ripple rounds the cut's bounds ONCE, so a clip lands ON the join.** `cutMusic`
first rounded its intermediate subtractions as well as its results, and a millisecond fell out of
the gap: on a real Ctrl+drag cut of the output at `[9.9856, 13.3144]` — a gesture whose bounds
carry three decimals, unlike a hand-typed one — the picture's join landed at 9.986 and the clip
that should have started there at **9.985**, while a clip at 20 s landed at 16.671 where the
picture had moved by 3.328 (the supervisor's live check, 2026-09-21). The helper now rounds `lo`
and `hi` once, at the top, and computes everything after that from those two numbers, rounding only
when a value becomes a clip's own — which is precisely what `removeRange` does to the picture's
list, so the two axes cannot disagree. Re-proven live after the fix: the join and the clip at the
same millisecond, a zero gap.

***As built* — the selection band is one row per lane, not one rectangle with offsets.** E3 drew
the "this cut applies here" band as a single rectangle whose `top` and `bottom` were pushed in by
`.no-video` / `.no-narration`; the fourth lane brought that rule back as an E3 regression, because
**a rectangle can only skip lanes at the ENDS**. Computed over all eight lock combinations against
the body's geometry (ruler 28 px + 4 × 68 px), two were wrong — Narration locked with Music
unlocked, alone or with Video locked as well — and both painted the band over a lane the cut does
not touch (the Reviewer's MINOR 1). The band is now a flex column of **one row per lane** plus the
ruler's, in the lanes' own order, and a locked lane's row is simply not drawn (the five
`.os-tl-selection-row` spans inside `.os-tl-selection` in `NarrationTimeline.tsx`, and that class
in `theme.css`); the container is what
the painter moves, so there is still one `left` and one `width` for all of them. It also deletes
the `calc(n * var(--tl-lane-h))` arithmetic a fifth lane would have broken again: a fifth lane is
a fifth row. The general form is trap 36.

***As built* — six smaller ones, each with its reason.**

- **`editBody(op, committed, sourceDuration)` is a pure payload builder** (`lib/edit.ts`), not
  four lines inside the mutation. The two rules it holds are the highest-risk decisions in the
  phase — always a `PUT`, never a `DELETE`; `music` only when the operation changes it — and as
  source text they were pinned only by `?raw` assertions, which a rename, a reformat or a DELETE
  built from a concatenated path would walk through. Pinned by behaviour now (the Reviewer's
  MINOR 4, its "better" option).
- **A clip drag is clamped to the end of the AUDITION** (`moveClip(clip, at, limit)`, the component
  passing `totalRef.current` = `auditionLength(duration, timing.end)`), as E3's block drag already
  was — the longer of the picture and the new narration, not the picture's own `duration`, which
  are different numbers the moment the narration overruns. The server's only rule is `at >= 0`, and
  the render mixes under `amix=…:duration=first`, so a clip parked past the end was accepted, drawn
  past the ruler, counted by the Render line and then silently absent from the output (MINOR 3;
  `moveClip(base, 1e6)` passed `validate_music` unchanged). What the clamp does when the clip is
  longer than what is left changed again in the last round — see the note below.
- **The edge zones are capped at a THIRD of a clip each**, not a half, in `clipAt` and in the CSS
  overlay alike (`min(8px, 33%)`). At a half the two zones met in the middle of any clip drawn
  16 px wide or narrower, leaving no body to grab: it could be trimmed and never moved, at any
  zoom that drew it that small (NIT 1).
- **A cut or a split is refused when the ripple would pass the 200-clip cap.** `cutMusic` can
  grow the list — 200 clips with a cut through one gives 201, which the server refuses — so the
  cut path checks the result and says which gesture would have made how many, where only the add
  path had a guard before (NIT 3).
- **The audition's envelope is anchored**, so playback that begins mid-fade enters at the level
  the fade had **reached**, not at the fade's start: `fadePoints` always opens with a
  `setValueAtTime` (a linear ramp is only linear *from* the previous event) and anchors the
  plateau before a fade-out, or the bed would slide down across its whole length. The Reviewer
  reconstructed the automation timeline and compared it to `fadeLevel` at twelve instants over
  2,229 mid-fade cases.
- **Deleting a library file goes behind a confirmation** naming it (`MusicLibrary.tsx`'s row
  button, through the repo's `useConfirm` / `ConfirmDialog`, titled "Delete this track?").
  The library is studio-wide, the delete is irreversible and there is
  no undo, and an unreferenced file — somebody else's upload — went on one click from a button
  sharing its cell with "Add at playhead" (MINOR 5).

***As built* — the second fix round: four things the first build got wrong once the banner was
used.** Each is a decision, not a tidy-up, and each is recorded because a later reader will
otherwise put it back.

- **A change to the LIBRARY invalidates the PLAN and the EDIT, through one list.** `missing` is the
  *server's* answer, computed against the library, and it reaches the strip only through the
  narration plan's `edit` block. The upload path invalidated the library query alone — so the
  banner's *other* way out, "put the file back under the same name", left the app insisting the
  lane was frozen, every one of the banner's four claims false, and its red "Remove the N stuck
  clips" button still armed over clips whose file had come back. Nothing else would have refetched:
  `refetchOnWindowFocus` is off and `staleTime` is 15 s, so closing the modal is not a refetch. The
  keys are now `lib/timeline.ts::libraryChangeKeys(projectId)` — the library, the plan (a key
  *prefix*, so every provider/voice/speed variant goes) and the page's own `["edit", pid]` — and
  the modal's upload and delete both reach them through the single `libraryChanged` handler, so the
  two mutations cannot drift apart. The same two keys are what a successful commit invalidates.
  `removeMissingClips` carries the belt: it re-reads the clips and returns early when none is
  missing any more, so the button cannot remove live clips in the window before a refetch lands.
  That is **trap 37**.
- **A drag leaves an over-long clip exactly where it is.** `moveClip`'s ceiling is `limit − length`,
  and a picture cut made with the Music lane locked can leave a clip longer than the whole audition,
  making that negative. Clamping the ceiling at 0 — the obvious reading — meant the first drag
  *slammed the clip to 0* and committed it, and every drag after that did nothing. A drag must never
  move a clip somewhere the user did not drag it to, so a negative ceiling now returns the clip
  unchanged and the gesture commits nothing at all (`sameMusic` sees no change).
- **The frozen-lane sentence is gated on the refusal really being about the library**, not merely on
  a clip's file being gone (`lib/edit.ts::editRefusal`, via `aboutTheLibrary`, which looks for the
  server's own "not in the library"). A missing file is a state a project can sit in for a whole
  session, and while it did, *every* 400 — a job holding the project, a range the server would not
  take — had the frozen-lane paragraph appended and blamed the missing clips for something they had
  nothing to do with. The server's own sentence is still kept whole either way.
- **The header's small buttons no longer wear the lock's class.** The Music header carries three of
  them — the library, the eye, the lock — and while all three were `os-tl-lock`, a rule below had to
  *undo* the locked header's amber on the two that never meant "locked". They are now `os-tl-tool`,
  named for what they are, and `os-tl-lock` carries only what being a lock means. This is the same
  lesson as the level slider one file away (the inspector's level had borrowed `os-tl-zoom`; both
  controls now share `os-tl-range` and keep their own class for their own width): **a class is a
  name, and borrowing one couples two things that are only alike today.**

***As built* — the two standing rules, where this lane rests on them.** Both are the owner's and
neither was a design question, so they are recorded here as things the lane obeys rather than as
things it decided. **No ducking** ([[feedback_audio_ducking]]): a clip's level is one number,
constant across the clip whether the narrator is speaking or not — the inspector's slider sets it,
a new clip takes the studio's `music_volume`, and nothing anywhere reads the voice to move it.
**The voice never fades** ([[feedback_video_audio]]): fading is the music's own, drawn as the
triangles at a clip's ends and applied by `fadePoints` in the audition and `afade` in the render;
nothing in this lane opens the narration master, and the eye on the header is a mute on the music
bus alone — the audition's, never the render's.

### 12.6 API changes

| Route | Change |
|---|---|
| `PUT /{pid}/edit` | body gains `"music": [clip, …] \| null`; **absent means unchanged** (E3's client sends no `music`, and a cut must not clear the music), `null` or `[]` clears. The tracks keep E3's rule (absent = whole). Validated before the lock; a clip naming a file not in the library is a 400 naming the clip. The audit summary gains "music: 2 clips". |
| `GET` / `DELETE /{pid}/edit`, the plan's `edit` block | carry `music` with each clip's `file_duration` and `missing`. `DELETE` clears the music too (it is "back to no edit"). |
| `GET /api/music`, `POST /api/music`, `GET /api/music/{name}`, `GET /api/music/{name}/peaks`, `DELETE /api/music/{name}` | new (§12.3). |
| `POST /{pid}/revoice` | unchanged — the render IS the re-voice job, now with a music pass after the mux. |

***As built* — one rule for every key, not two.** The asymmetry in the first row did not survive
§12.5. As designed the tracks read absent as "whole" while the music read absent as "unchanged",
so a music-only `PUT` **removed both track lists** — and §12.5's own copy says a clip commit is
"one `PUT /edit` with the new `music` list", which means E4b would have discarded the picture's
cut on every add, move, trim, gain change and fade change (the Reviewer's MINOR 4, proven).
Worse in the other direction: `{"video": null, "narration": null}` with music stored was refused
("at least one track"), so the only way back to whole tracks was a `DELETE` — which clears the
music too, and a `DELETE` is exactly what E3's client sends when the last cut is undone
(`NarrationTimeline.tsx:1209-1217`), so undoing a cut would have wiped the lane. So the rule is
now one rule: **absent = unchanged, `null` = cleared, a list = set**, for `video`, `narration`
and `music` alike, with `[]` clearing the music (no clips is no music) while an empty *track*
list stays E1's refusal. `{}` is a **200 no-op** — nothing written, nothing forgotten, no audit
row, the current edit returned, the shape `clear_edit`'s no-op already had — where the design
and E3 both made it a 400. After the merge, when nothing but `version` would remain, the `edit`
key is **removed** from the record, so `{"music": []}` on an unedited project no longer stores
an edit that is no edit (the Reviewer's MINOR 3) and no `project.edit` "cleared" row is written
for a project that was never edited; unknown keys ride through only while a track or the music
is still there. A version-1 `keep` is converted to two tracks **on write**, so "absent" cannot
drop a cut this call never saw; `keep` beside `video`, `narration` or `music` is still a 400;
and `DELETE /edit` is unchanged — everything gone, music included. The signature is
`set_edit(pid, video=UNCHANGED, narration=UNCHANGED, music=UNCHANGED)` — one module sentinel for
all three, because `None` already means something different for each — with the route mapping
each key by `body.model_fields_set`, and it returns `(payload, changed)` so the route leaves a
no-op out of the audit log exactly as `clear_edit` already did. Every list is validated before
the first write: the given lists before the lock, and the **merged edit's read-back** inside it
and before `save_project`, so a stored clip list this version cannot read refuses the call
instead of landing the cut and then raising on the way out with the write already done (the
Reviewer's NIT 1). E3's client is untouched by all of this: it always sends both track keys,
possibly `null`, and never `music`. **Trap 32 below is reversed by this and is rewritten there.**

### 12.7 Traps this phase adds

*(E4a proved traps 24–30 in the parenthetical after each, and E4b what its half of the phase
could reach — including 31, which is entirely its; 32 is reversed by E4a's build. 33 and 34 are
what that round earned, 35 and 36 are what E4b's did, and 37 is what its second fix round earned
once the banner 35 describes was actually used.)*

24. **`at` is output seconds, so the Music lane obeys the locks like a track**: a cut with
    it unlocked must move and trim the clips (`cutMusic`), and the server accepts whatever
    valid list the client sends — it does not re-derive clips from the picture's cut.
    *(The server half is proven: whatever valid list arrives is what is stored, sorted by
    `at` and otherwise untouched. `cutMusic` was built in E4b and proven there — 6,000 random
    cases against a reference written the other way round, 0 mismatches, every result accepted
    unchanged by `validate_music`. **The trap is narrower than it was written**, by the owner's
    ruling of 2026-09-21: the clips ripple only when the PICTURE's kept list changed, not merely
    when the lane is unlocked, and the scissors is disabled where that leaves it nothing to do —
    §12.5's *As built*.)*
25. **A missing library file is a refusal, never silence**: the clip is marked, the audition
    skips it, the render refuses. And a library delete is refused while any project refers
    to the file. *(Proven at three depths — `stored_music` flags it, `services/revoice.py`
    refuses the job **before any work** (no `mix_music` call, no `_revoice_video`, no scratch
    directory, nothing stamped) for a clip flagged `missing` and for a file that left the disk
    after the index was read, and `mix_music` returns False before starting ffmpeg as the
    belt to both. The delete half was the one that failed — the Reviewer's MAJOR 1; see
    §12.3's As-built note. E4b proved the client's three: the clip draws hatched and named
    "— missing", `clipPlayback` returns `null` for it so the audition never schedules it and
    `musicFiles` never fetches its file, and the Render line says the render will refuse. It
    also found what this trap does NOT cover — the editor froze as well; see §12.5's As-built
    note and trap 35.)*
26. **Gain is a linear factor everywhere** — `volume=`, `GainNode.gain`, the slider. The
    pydub "rough dB" formula in `replace_video_audio` is not the model and is not on this
    path. *(Proven: no `dB` anywhere in 4,000 fuzzed graphs, and `replace_video_audio` is
    untouched by the change. E4b's half too: the inspector's slider is a linear 0–1 through
    `clipGain`, and the audition multiplies it into a `GainNode` — one number, three places,
    no curve.)*
27. **No ducking; the voice never fades.** The bed's level is constant; fades are the
    music's own; the narration master is never opened by the music pass. *(The half about
    ducking and fades held — the head and the tail of the voice measure unfaded, nothing in
    the graph touches `[0:a]` but the up-mix, and the standalone narration track is never
    opened. The half about the level FAILED on a mono voice, by 3.01 dB, until trap 33 was
    found: see §12.4's As-built note. E4b holds both in the browser as well: a clip's gain is
    one constant that nothing reads the voice to move, the fades belong to the clip, and the
    eye is a mute on the music bus alone — §12.5's last As-built note.)*
28. **`amix` normalises by default** — `normalize=0` on both mixes, or the voice drops
    by 1/N. Stereo before mixing. *(Proven load-bearing: with `normalize=0` stripped the
    voice drops to ×0.354, and the mutant that removes it from both mixes dies on three
    graph tests.)*
29. **The second pass copies the video** (`-c:v copy`); a re-encode here would be E1's
    16 s again for nothing. *(Proven: 60 video frames in and 60 out, framemd5-counted, on
    each of nine real renders; the mutant that swaps in `libx264` dies on the command test.)*
30. **The upload decodes; the render decodes; nothing probes.** Length, sample rate and
    channels come from the decode at upload and are recorded in the index. *(Proven: exactly
    one decode per upload — of `<name>.part` — the peaks cached so `peaks()` decodes again
    only if the cache has gone, and no `ffprobe` and no bare `"ffmpeg"` anywhere in the
    change. It is also the trap that chose the decoder, since pydub would have run ffprobe:
    see §12.3's As-built note.)*
31. **Decoded music is big in the browser**: one decode per file, cached; never one per
    clip. *(Proven in E4b, which is where the browser is: `musicBuffers` is a
    `Map<file name, AudioBuffer>` beside the sentence buffers — one decode per FILE however
    many clips play it, lazily on the first Play rather than on mount, and never for a missing
    file. The copy under the strip says what it costs, so a long lane is a choice and not a
    surprise: a few seconds and about 100 MB of memory for a five-minute track.)*
32. **Every key of a `PUT /edit` body reads the same way: absent means unchanged.**
    *(Written here as an asymmetry — `music` unchanged, a track key whole — and **reversed by
    the build**, because the asymmetry silently dropped the other half of the edit in both
    directions: see §12.6's As-built note. The trap itself survives and is stronger for being
    symmetrical — a `PUT` never means "and clear whatever I did not name", so a cut may send
    its tracks and keep the clips and a clip commit may send the clips and keep the cut. It is
    written into `EditIn`'s and `set_edit`'s docstrings and pinned by the mutant that reverses
    it, which kills four `test_edit.py` tests.)*
33. **The default stereo up-mix is not unity for mono.** ffmpeg's 1→2 rematrix is
    power-preserving: `aformat=channel_layouts=stereo`, a plain `-ac 2` and `aresample` all
    hand each output channel M/√2 — **−3.01 dB** — with no error, no warning, and nothing a
    test that reads the filtergraph string can see. Only a per-channel measurement on a real
    render finds it. Anything that must not change a level up-mixes with
    `pan=stereo|FL=FL+FC|FR=FR+FC`, which takes exactly the channels it names and drops every
    other one, so one graph serves mono and stereo with nothing probed (traps 2,
    30). *(Re-review, 2026-09-21: `pan` drops the channels the input **does** have and the
    mapping does not name, not only the ones it lacks — a 5.1 clip keeps FL, FR and FC, loses
    LFE, BL and BR, and arrives with its centre at unity where a standard fold takes it to
    0.7071, all measured. One static mapping cannot give mono its unity and a surround layout
    a correct fold, and the choice between them is not close: the app makes mono and stereo,
    a music bed is mono or stereo, and the audition's rule is the browser's. So the library
    refuses an upload wider than stereo — `services.music.MAX_CHANNELS`, the owner's call on
    the day: "this isn't a HiFi app" — and every clip that reaches the graph is mono or
    stereo. The comments that had claimed `pan` only drops absent channels were corrected
    before the commit; a claim about audio that no measurement backs is the same defect as
    the one this trap records.)* The reusable rule: **a format conversion is not a no-op**, and the browser's rule is
    not ffmpeg's — Web Audio up-mixes mono as L = R = M, at unity, so a render that used
    `aformat` and an audition that used Web Audio would disagree by 3 dB on every mono
    project.
34. **A Windows filename check is not a name check.** `Path.is_file()`, `Path.exists()` and
    an `open()` that succeeds all answer "a file the OS resolves to this name is there", not
    "a file spelled this way is there": on Windows `BED.mp3` finds `bed.mp3`. Any guard that
    keys a case-SENSITIVE dict beside a check like that has a hole one keystroke wide — here
    it deleted a file a project's clip still named and left the row behind. Where the name IS
    the key (a library, a cache, an index), make it unique under `str.casefold` on **every**
    platform so the platforms behave alike, and read the on-disk spelling from
    `os.listdir` when an exact answer is what is wanted. (Windows also strips a trailing dot
    and a trailing space from a name; this library never reaches that only because the
    extension check refuses those names first.)
35. **A modelled "missing" state must leave the EDITOR usable, not merely the render honest.**
    Marking a clip whose file has gone, skipping it in the audition and refusing the render is
    only two thirds of an answer. The third is the way out: if every commit sends the whole
    collection and the validator refuses the whole collection over one bad member, the user is
    locked out of the *other* members too — here one lost file froze every gesture on the lane
    behind a refusal that named a clip nobody had touched, and the spec's own ordinary case
    (the same bed laid twice) made the printed remedy, "remove the clip", impossible to carry
    out. Whenever a state is modelled as "broken but stored", ask the question in the same
    breath: *what is the gesture that leaves it, and can the user reach it?* A validator that
    treats "keep what is already stored" and "add something new" alike will answer no. (E4b
    ships the client's way out — one button that removes them all in a single commit — and
    §12.5's As-built note records the server-side fix as **E4c**, open.)
36. **One rectangle cannot express a per-lane truth.** A band that means "this applies here",
    drawn as a single box with `top` and `bottom` offsets, can only ever skip rows at the
    ENDS: the moment the thing it describes is per-row and a middle row opts out, the box
    lies, and it lies quietly — it still looks like a considered layout. Two of E4b's eight
    lock combinations painted a cut's selection over a locked lane for exactly that reason.
    Draw one element per row and show each on its own condition; the arithmetic that made the
    offsets disappears with it, which is the second win, because that arithmetic is what the
    NEXT row would have broken again.
37. **A remedy the UI offers must be re-checked after the user takes it.** Printing a way out of a
    broken state is half the job; the other half is noticing that the way out was taken. The lane's
    banner offered two — remove the stuck clips, or put the file back under its name — and the
    second one left the banner, all four of its claims and its destructive button exactly as they
    were, because the fact it reports is the *server's* (`missing`) and arrives by a query nothing
    in that path invalidated. A stale remedy is worse than none: this one, pressed after the file
    had come back, deleted live clips. So when a fix is offered, ask which query carries the
    evidence for it, invalidate that query on **every** path that can change the evidence (one
    shared list of keys, not a copy per call site — a copy drifts), and guard the remedy's own
    handler so it does nothing when there is nothing left to remedy.

### 12.8 Decisions for the owner before the build

1. **Music clips live on the output axis and the Music lane obeys the locks** — a cut with
   the lane unlocked moves, trims or splits clips as Camtasia does; locked, they stay.
   *(Refined by the owner on 2026-09-21, once E4b made the one ambiguous combination visible:
   the ripple applies only when the **picture's** own list changed as well — "the music rides
   the picture" — and the scissors is disabled where that would leave it nothing to do. §12.5's
   *As built*.)*
2. **Defaults for a new clip**: gain = the studio's `music_volume` (0.15), **fade in 1 s,
   fade out 2 s** (Camtasia adds no fade by default; a bed under narration nearly always
   wants one — the owner's rule allows it). Changeable per clip.
3. **The library is studio-wide**, an upload of an existing name is refused (rename), a
   delete is refused while referenced.
4. **Fades are linear** in both the render and the audition (the shapes agree exactly).
5. **The render is a second ffmpeg pass with the video copied**; `_revoice_video` stays
   untouched; the downloadable narration track stays voice-only.
6. **The eye mutes the music in the audition only.**
7. **No looping**: a bed longer than the file is two clips.
8. **Two builds, each through the three gates**: **E4a** — the library, the model, the render
   pass (verifiable with `curl` and a real render, the E1 way); **E4b** — the lane, the
   gestures, the inspector, the audition. Release **0.9.0** when E4b lands. *(Taken with the
   rest on 2026-09-21 — "go with your proposals, start E4a". **E4a was built that day**,
   through Developer → Reviewer → Documentation with a fix round after the Reviewer's two
   MAJORs, and **E4b on 2026-09-22**, the same way, with its own fix round after the
   Reviewer's MAJOR and the supervisor's four live findings; §7's two blocks record what each
   shipped. Both are unreleased — the version stays 0.8.0 — so **the release of 0.9.0 is the
   only part of this decision still open**.)*
9. Measured before the build, not guessed: the second pass's cost on the 341 s corpus with
   two five-minute clips (the bundled ffmpeg 7.1), and the presence of every filter named in
   §12.4 in that binary's `-filters`. *(Both measured 2026-09-21, before a line was written.
   The bundled 7.1 essentials lists all seven — `adelay afade aformat amix asetpts atrim
   volume` — and §12.4's graph over the corpus re-voiced output (336 s, 71 MB) with two 300 s
   stereo MP3s, clips at 12.5 s and 200 s, gain 0.15, fades 1 s / 2 s, cost **8.25 s** on it
   and 8.0 s on the dev 8.0.1: exit 0, the output's length unchanged at 5:35.96 under
   `duration=first`, the picture copied. `music_timeout = 60 + 2 × output` comes from that
   number and is generous by about forty times. The Reviewer then measured the worst case the
   model permits — 200 clips placed at 330 s on a 340 s output, since `adelay` could have made
   a late clip expensive — at **15.9 s and 132 MB** peak ffmpeg RSS against that rule's own
   740 s deadline, so the 200-clip cap needs no second thought.)*
10. *(Ruled by the owner on 2026-09-21, during E4b, and recorded in §12.5's *As built* with the
    reasoning behind each:)* **the music rides the picture** — a cut ripples the clips only when
    the picture is cut too, and the scissors says so rather than appearing to work — and **a
    clip click does not seek**: selecting a clip leaves the playhead where it is, so a level can
    be set while the audition plays. A sentence block's click still seeks; the two are meant to
    differ, and nothing should harmonise them.

### Critical files (E4)

| File | Role |
|---|---|
| `services/music.py` (new) | the library: index, upload (decode + peaks + publish), delete with the reference check |
| `api/routers/music.py` (new) | the five routes; `api/audit.py` `MUSIC_UPLOAD` / `MUSIC_DELETE` |
| `services/edit.py` | `stored_music`, clip validation, `set_edit(..., music=UNCHANGED)`, `payload` with `music` |
| `services/waveform.py` | the bucket arithmetic pulled into a helper the library's peaks share |
| `core/video_creator.py` | `mix_music`, `music_filtergraph`, `music_timeout` |
| `services/revoice.py` | the music pass after the mux; the missing-file refusal |
| `api/schemas.py` | `MusicClipIn`, `EditIn.music` |
| `frontend/src/lib/edit.ts` | `cutMusic`, `splitMusicAt`, `clipBounds`, the inspector's rules |
| `frontend/src/components/project/NarrationTimeline.tsx`, `MusicLibrary.tsx` (new) | the lane, the gestures, the modal, the audition's music buffers |
| `frontend/src/styles/theme.css` | per-lane rules for four lanes; the clip block |
| `tests/test_music_library.py`, `tests/test_music_render.py` (new), `tests/test_edit.py`, `tests/test_revoice.py`, `tests/test_project_ownership.py` (exclusion), `tests/test_audit.py` | the guards |

*As built (E4a):* every row above `frontend/src/lib/edit.ts` is built; the three `frontend/`
rows are E4b's and nothing under `frontend/` changed. Two of the guards needed no edit:
`tests/test_audit.py` already sweeps every `audit(...)` call site for a registered action and
entity — which is *why* `api/audit.py`'s `ENTITIES` gains `"music"` — and the render pass's
tests went into `tests/test_music_render.py` rather than `tests/test_revoice.py`.
`tests/test_waveform.py` joins the list (+1: the two peak paths agree on the same samples,
mono and stereo) and so does `tests/test_no_state_tracked.py`, which now asserts that
`assets/music/` stays git-ignored. Two files the table did not name are a line each:
`api/routers/edit.py` (the PUT's key mapping and the audit summary's music clause) and
`services/narration.py` (the plan's `edit` block handed `applied.music`).

*As built (E4b):* the three `frontend/` rows are now built too, and **nothing outside
`frontend/` was touched** — the whole phase is the three files the table names plus five it
does not: `frontend/src/lib/timeline.ts` (`EditPayload.music`, optional, because a backend
before E4a sends no such key, and `libraryChangeKeys`), `frontend/src/pages/ProjectDetail.tsx`
(the Render line and its label), `frontend/src/lib/music.test.ts`, which is new — the Music
lane's tests went into a file of their own rather than into `edit.test.ts`, deliberately, so E3's
suite stays byte-identical and can be shown not to have been weakened —
`frontend/src/components/project/NarrationTimeline.render.test.tsx`, also new (the SSR render
harness), and `frontend/vite.config.ts`, whose vitest `include` was widened to `.tsx` so that
harness runs. Two of the helpers the first row
names were never needed: there is no `splitMusicAt` (a split is `cutMusic(clips, t, t)` — a
zero-length interval, tested for idempotence) and no `clipBounds` (`clipLength`, `clipEnd` and
`clipAt` are what the callers wanted). `frontend/dist` is rebuilt and fingerprinted against the
source, as `tests/test_frontend_dist.py` requires.
