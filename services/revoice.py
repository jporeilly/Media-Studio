"""Re-voice a transcribed video: regenerate its narration in a new voice
(optionally translated) while keeping the original picture, producing a new MP4.

Runs inside a job (see :mod:`services.jobs`). Reuses the carried-over media
engine: the project's transcript becomes a single re-voiceable section whose
Whisper segment timings drive :meth:`VideoProcessor._revoice_video`, which
synthesises per-sentence TTS and swaps the audio track onto the untouched
original frames (ffmpeg ``-c:v copy`` + ``apad`` to the video length).

**The render of an edit is this same job.** When the project carries an edit
(``services.edit``: the ranges of the source that are kept, one list per
track) whose VIDEO list removes anything, the kept ranges are first re-encoded
into a picture-only intermediate (``core.video_creator.cut_picture``); when its
NARRATION list removes anything, the transcript is handed to the engine
PROJECTED into the output's seconds - so the engine sees a transcript in a
shorter recording and does what it always does. One job kind, one code path,
and nothing in ``_revoice_video`` changes. With no edit, or one that keeps
everything, the path is exactly the one above, byte for byte.

**The music lane is a second ffmpeg pass AFTER the mux** (spec §12.4): once
``_revoice_video`` has written ``<stem>_revoiced.mp4``, the edit's clips are
laid under its audio in place (``core.video_creator.mix_music`` - the video
stream copied, and the voice's level untouched, because the pass up-mixes it
to stereo at unity rather than through ffmpeg's power-preserving rematrix,
which took a mono narration down 3.01 dB). That is the one shape that leaves
``_revoice_video`` and the mux exactly as they are and keeps the standalone
narration track voice-only: it is downloaded to be laid into other editors,
and music baked into it would break that promise.

**The record is stamped for the file that is on disk.** The mux's stamps are
written as soon as ``_revoice_video`` has produced the file, before the music
pass runs; a cancel or a failure in the pass then leaves a record that
truthfully describes the voice-only render it left behind, rather than the
previous render's ``music_rendered`` over bytes that no longer carry it. A
mix that succeeds stamps again - the bytes changed once more, and the page's
cache token with them.

**The markers become chapters LAST** (E5b, spec §13.2, decision 3): once the
file the job records is final - after the mux, and after the music pass when
there is one - the drawn markers (``services.edit.chapters_for``: the stored
markers projected through the picture's list as stored NOW, trap 42) are
written into it as chapters by one stream-copy remux
(``core.video_creator.embed_chapters``), so they are on the file the user
downloads. No drawn markers, no remux: the file keeps whatever chapters the
source had, as it always did. A remux that fails logs and leaves the
un-chaptered file; it never fails the job, exactly as the deck path treats
its own chapters.

**A cancel** (``POST /api/jobs/{id}/cancel``) is looked at between the stages
and inside the long ones: after the translation, while the picture is cut,
before every sentence is synthesised (``_revoice_video``'s ``cancel_check``),
before the music pass and while it runs. It never leaves a half-written file
where the record says there is one, and the job finishes ``done`` with
``result = {"cancelled": True, "stage": ...}`` and a closing line saying what
is on disk:

- **before the mux** (``stage`` "translation", "cut" or "synthesis"): nothing
  the record names was written. The record is UNCHANGED, the previous
  re-voice, if there was one, is still the video and the narration track it
  names, and the job's scratch directories are removed. (At the cut and the
  synthesis the engine's working project under ``<pid>/revoice/`` - its own
  ``project.json``, ``audio/`` and ``images/`` - has been rewritten, as every
  run rewrites it; nothing reads it but the next run.) -
  ``CANCELLED_BEFORE_RENDER``;
- **at the music pass** (``stage`` "mix"): the mux had already replaced
  ``<stem>_revoiced.mp4`` with the new narration, so the record DESCRIBES
  that voice-only file (stamped before the pass, as above) and the partial
  mix is removed - ``CANCELLED_VOICE_ONLY``.

The mux and the chapters' remux are short and are not interrupted: a cancel
that arrives once the last check is behind the job - after the mux of a
render with no music, or after the music pass of one with music - is too
late, and the job completes (the file then carries its chapters too).
"""

import shutil
from datetime import datetime, timezone

from services import edit, jobs, narration, waveform
from services import music as music_library
from services import projects as store
from utils.logger import get_logger

logger = get_logger("REVOICE")

MISSING_MUSIC = "music file '{name}' is missing — remove the clip or upload the file again"

# A cancelled re-voice's closing line, which the Re-voice card shows once the
# job is over: what the cancel left behind, in the user's terms.
CANCELLED_BEFORE_RENDER = "Re-voice cancelled before the new video was written; the project is as it was."
CANCELLED_VOICE_ONLY = (
    "Re-voice cancelled while the music was being mixed: the re-voiced video has the new narration "
    "but not the music{chapters}. Re-voice again for the full render."
)


def _cancelled(stage: str, report, message: str = CANCELLED_BEFORE_RENDER) -> dict:
    """End the job as cancelled at ``stage``: its closing line (reported at
    1.0, which ``services.jobs`` keeps as the job's message) says what is on
    disk, and the result names the stage (see the module docstring)."""
    report(1.0, message)
    logger.info("Re-voice cancelled at the %s stage", stage)
    return {"cancelled": True, "stage": stage}


def _music_clips(clips: list) -> list[dict]:
    """The edit's clips as ``mix_music`` takes them - each with its library
    file's PATH - or ``ValueError`` naming the first file that is not there.
    A clip the library reported ``missing`` is refused outright, and every
    other file is resolved now, before the job does any work, so the render
    never finds a file gone halfway through (trap 25: a refusal, never
    silence in the file's place)."""
    resolved = []
    for clip in clips:
        if clip.get("missing"):
            raise ValueError(MISSING_MUSIC.format(name=clip["file"]))
        try:
            path = music_library.get_path(clip["file"])
        except music_library.MusicNotFound:
            raise ValueError(MISSING_MUSIC.format(name=clip["file"])) from None
        resolved.append({
            "path": str(path), "at": clip["at"], "in": clip["in"], "out": clip["out"],
            "gain": clip["gain"], "fade_in": clip["fade_in"], "fade_out": clip["fade_out"],
        })
    return resolved


def revoice_project(pid, voice_id, speed=1.0, language=None, progress=None, provider=None) -> dict:
    """Re-voice ``pid``'s narration in ``voice_id`` (optionally translated).

    Requires a ``video`` project with a non-empty ``transcript`` (raises
    ``ValueError`` otherwise). When ``language`` names a real target language
    (from :func:`core.translator.get_available_languages`, other than English or
    the source), the narration is translated via the local Ollama model first.
    ``provider`` (edge_tts | kokoro) is the TTS provider the narration is
    synthesised with; None means the studio default at the time the job runs.

    Reconstructs a one-section ``ProjectState`` from the transcript - the
    per-sentence adjustments (``services.narration``) travelling with it, and
    the edit (``services.edit``) applied to it as a projection -, cuts the
    picture first when the edit removes anything, runs the carried-over
    ``_revoice_video`` (keeps the frames, swaps the audio), lays the edit's
    music clips under the result when there are any, records
    ``revoiced_video`` on the project, and returns
    ``{"video", "language", "failed_sentences"}`` - or
    ``{"cancelled": True, "stage": ...}`` when the job was cancelled (after
    the translation, while the picture was cut, while the sentences were
    synthesised, or at the music mix; the module docstring says what each
    leaves). Raises ``RuntimeError`` if the re-voice produced no file or the
    music could not be mixed, and ``ValueError`` when every sentence is muted
    (or cut away) - which would otherwise fail inside the job as a bare
    "Re-voice failed" - or when a clip's music file has left the library
    (refused before any work; never rendered as silence).

    **A cancel before the mux changes nothing**: the record is untouched and
    the previous re-voice's files are still the ones it names. **A cancel
    during the music pass leaves the re-voiced file without music on disk** -
    ``_revoice_video`` has already rewritten ``<stem>_revoiced.mp4`` in place
    by then - and the record describes exactly that: the voice-only render,
    stamped with a fresh ``revoiced_at`` (the page's cache token: the bytes
    DID change) and no ``music_rendered``. The same holds for a mix that
    fails. The next render replaces the file; a cancel is not a rollback.
    """
    record = store.get_project(pid)
    if not record:
        raise ValueError("Project not found.")
    if record.get("kind") != "video":
        raise ValueError("Only video projects can be re-voiced.")

    stored = record.get("transcript") or []
    if not stored:
        raise ValueError("Transcribe the video first.")

    # The edit, applied by the same function the audition plan applies it
    # with: ``segments`` is the transcript as the render will speak it - in
    # timeline seconds when the NARRATION list removes anything, the stored
    # transcript itself (the same objects, untouched) when there is no edit,
    # or it keeps everything, or only the picture is cut. The source's length
    # is the WAV header's, as everywhere.
    source_duration = waveform.duration_for(pid)
    applied = edit.apply(record, stored, source_duration)
    segments = applied.sentences
    if not narration.count_spoken(segments):
        # No spoken sentence means no audio at all, and _revoice_video answers
        # that with a bare False the caller below turns into "Re-voice failed".
        # Said plainly here as well as at the route, so calling the service
        # directly gets the same answer.
        if applied.projected:
            raise ValueError(
                "The edit removes every sentence that would be spoken, so there would be no "
                "narration. Keep at least one, or clear the edit."
            )
        raise ValueError("Every sentence is muted, so there would be no narration. Unmute at least one.")

    source_video = store.PROJECTS_DIR / pid / record["source_filename"]
    if not source_video.is_file():
        raise ValueError("The project's source video is missing.")

    # The music, refused BEFORE any work when a clip's file has gone (the E1
    # rule for an unreadable edit, applied here: never silence in its place),
    # and every file resolved to its path now.
    music_clips = _music_clips(applied.music)

    def _report(fraction: float, message: str = "") -> None:
        if progress:
            progress(fraction, message)

    # The narration text: the joined transcript. When a target language is
    # given, translate the whole thing once through the local Ollama model.
    # Joined from the sentences the render will speak: the engine takes the
    # per-sentence path only while the notes equal the joined segment text
    # (``collect_revoice_segments``), so a cut sentence must be absent here too.
    joined = " ".join((s.get("text") or "") for s in segments).strip()
    notes_text = joined

    if language:
        # Imported lazily so a re-voice without translation never touches the
        # translator / Ollama config, and startup stays light.
        from core import translator
        from services.studio_settings import ollama_model
        from utils.config import config

        subtag = (translator.get_available_languages().get(language) or "").lower()
        source_lang = (record.get("language") or "").split("-")[0].lower()
        if subtag and subtag != "en" and subtag != source_lang:
            # A job cancelled while it queued stops before the model call.
            if jobs.cancel_requested_here():
                return _cancelled("translation", _report)
            _report(0.05, f"Translating to {language}…")
            notes_text = translator.translate_notes(
                [joined], language, config.ollama_url, ollama_model(),
            )[0]

    # The translation is one model call and is not interrupted; a cancel that
    # arrived during it (or while the job queued) stops here, with nothing
    # written.
    if jobs.cancel_requested_here():
        return _cancelled("translation", _report)

    # Reconstruct a re-voiceable ProjectState: ONE section spanning the whole
    # video. The transcript's Whisper segments drive the sentence-level timing.
    # When the notes were translated they no longer match the joined segment
    # text, so collect_revoice_segments() spreads the translated sentences across
    # the section window instead — the intended translated path.
    from core.project_manager import ProjectManager

    project_dir = store.PROJECTS_DIR / pid
    # The ProjectManager keeps its own project.json / audio / images. Give it a
    # sub-directory so it never clobbers the project store's own project.json
    # (same filename) — that record holds the transcript and must survive a
    # re-voice, including a failed one.
    pm = ProjectManager(project_dir / "revoice")
    pm.create_project(pptx_path=source_video, slide_notes=[notes_text], voice_id=voice_id)

    slide0 = pm.state.slides[0]
    # The per-sentence adjustments travel with the timings. ``original_segments``
    # is a plain List[dict] and ProjectManager.save() uses asdict, so extra keys
    # inside these dicts survive the JSON round trip: nothing in the engine needs
    # a new dataclass field. A segment that carries none of them produces exactly
    # the dict this built before. Only the vocabulary rides through: the
    # projection's own bookkeeping (its ``index``) does not.
    slide0.original_segments = [
        {
            "start": float(s["start"]), "end": float(s["end"]), "text": s["text"],
            **{key: s[key] for key in narration.OVERRIDE_KEYS if key in s},
        }
        for s in segments
    ]
    # The section this re-voice runs as: the whole transcript. Taken from
    # ``services.narration`` because the timeline's audition plan has to compute
    # the same bound for the LAST sentence's window - the one place the section's
    # end still decides a synthesis speed - and two spellings of "the section is
    # the whole transcript" would be free to drift apart. Over the projected
    # sentences when there is a cut, as the plan's is.
    section = narration.transcript_section(segments)
    slide0.original_start_time = section["start"]
    slide0.original_end_time = section["end"]

    out = project_dir / f"{source_video.stem}_revoiced.mp4"

    # Imported lazily so the media engine and its heavy deps load only when a
    # re-voice actually runs (and so tests can monkeypatch VideoProcessor).
    from services import processing

    # The picture. With no edit, or one whose VIDEO list keeps everything, the
    # engine muxes the narration straight onto the untouched source - the path
    # every project has always taken. With a cut, the kept ranges are first
    # re-encoded into a picture-only intermediate under the job's scratch
    # directory (one ffmpeg run; never a stream copy, which cannot start on
    # the P-frames a cut lands on), and the engine muxes onto THAT: it is told
    # the cut picture is the source, sees a transcript in a shorter recording,
    # and does what it always does. The scratch directory outlives the mux and
    # is removed after it.
    render_source = source_video
    scratch = None
    try:
        if applied.cut:
            from core import video_creator
            from services.output_presets import DEFAULT_PRESET_ID, get_preset

            scratch = processing._job_scratch("edit_")
            render_source = scratch / f"{source_video.stem}_cut.mp4"
            count = len(applied.video)
            _report(0.08, f"Cutting the picture ({count} range{'' if count == 1 else 's'} kept)…")
            # The bitrate is the output preset's ("" = the codec default); in
            # E1 and E2 that is the default preset; choosing one is a later phase.
            cut = video_creator.cut_picture(
                source_video, applied.video, render_source,
                video_bitrate=get_preset(DEFAULT_PRESET_ID)["video_bitrate"],
                cancel_check=jobs.cancel_requested_here,
            )
            if not cut:
                if jobs.cancel_requested_here():
                    # cut_picture left nothing behind; the scratch directory
                    # goes in the ``finally`` below.
                    return _cancelled("cut", _report)
                raise RuntimeError("The picture could not be cut; the server log has the reason.")
        pm.state.source_video_path = str(render_source)
        pm.save()

        # The best length this job can offer the engine WITHOUT measuring the
        # file, and it must be understood for what it is: with a cut it is the
        # edit's own output length (``edit.output_duration``, the sum of the
        # kept ranges - exact, and known before ffmpeg ran), but with no cut it
        # is the length of the extracted AUDIO, not of the picture
        # (``edit.apply`` is handed ``waveform.duration_for(pid)``, the
        # audio.wav header, and passes it straight through; ``record`` carries
        # faster-whisper's measurement of that same WAV). The two differ
        # whenever a recording's sound stops before its frames do, so the
        # engine treats this as a belt and measures the picture itself - see
        # ``core.video_creator.pad_seconds``. It is passed at all because it
        # is exact for a cut and because it still answers when a file cannot
        # be measured.
        told_seconds = applied.output_duration or float(record.get("duration") or 0.0) or None

        _report(0.1, "Re-voicing…")
        processor = processing.VideoProcessor(voice_id=voice_id, speed=speed, provider=provider or "")
        # The synthesis is the long part of a re-voice, one voice-service
        # round trip per sentence: the engine asks the job's flag before
        # each sentence and once more before the mux, and on a cancel it
        # returns False having written nothing outside its own scratch
        # directory (which it removes).
        ok = processor._revoice_video(
            pm, render_source, out, progress=progress, video_duration=told_seconds,
            cancel_check=jobs.cancel_requested_here,
        )
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
    if not ok and getattr(processor, "cancelled", False):
        # The engine saw the cancel itself, before the mux: the record is
        # untouched and ``out`` - the previous re-voice, if there was one -
        # is the file it already names. (A False for any other reason is a
        # failure, cancel or not.)
        return _cancelled("synthesis", _report)
    if not ok or not out.exists():
        raise RuntimeError("Re-voice failed")

    # A sentence whose synthesis failed leaves a silent hole. It used to be
    # visible only in the server log; it is counted now, kept on the record so
    # the page can still say so after the job is gone, and reported in the
    # job's result and closing message the way the AI loops report theirs.
    failed = int(getattr(processor, "failed_sentences", 0) or 0)

    # Stamped HERE, for the file the mux has just written, and BEFORE the
    # music pass: the pass rewrites that file in place and can be cancelled or
    # fail, and a record carrying the previous render's stamps over bytes that
    # no longer match them is the one thing this must not leave behind. The
    # music's own stamps go on below, once the mix has succeeded.
    #
    # Saved onto the record as it is NOW, not the copy read when the job
    # started: the transcript on it carries the user's per-sentence
    # adjustments, and writing back a copy from minutes ago would revert any
    # made since. (``require_idle`` refuses an adjustment while this job holds
    # the project, so this is the belt to that braces - the same re-read the
    # generate route does, and for the same reason.)
    current = store.get_project(pid) or record
    current["revoiced_video"] = out.name
    # Every run overwrites the SAME filename, so the URL serving it never
    # changes and a browser goes on showing the copy it cached the first time -
    # which, for a file that has since been rewritten under it, decodes to a
    # black frame at 0:00 rather than to the previous video. The page appends
    # this timestamp to the media URLs so each re-voice is a new one. (The
    # generated-video player has always done the same with ``rendered_at``;
    # the re-voice side simply had no stamp of its own to use.)
    current["revoiced_at"] = datetime.now(timezone.utc).isoformat()
    # The same filename whether or not the picture was cut, so a render that
    # applied an edit says so beside the stamp - and a whole-source run clears
    # it, as ``narration_audio`` below is cleared, so the record never claims
    # an edit that the file on disk does not carry. Stamped whenever EITHER
    # track removed anything - a narration-only edit leaves the picture whole
    # but the output still differs from an unedited render. The music's clips
    # are the third reason, and they are stamped after the pass has run.
    if applied.cut or applied.projected:
        current["edit_rendered_at"] = current["revoiced_at"]
    else:
        current.pop("edit_rendered_at", None)
    # Popped for the file as it stands: voice only until the pass below says
    # otherwise, so the record never claims music the file does not have.
    current.pop("music_rendered", None)
    # The narration on its own, for editing the video elsewhere: same length as
    # the picture and starting at the same zero, so it drops straight onto a
    # timeline beside the original. Recorded only when it is really there - the
    # copy is best-effort and an older project re-voiced before this existed has
    # none.
    narration_track = processing.narration_path_for(out)
    if narration_track.is_file():
        current["narration_audio"] = narration_track.name
    else:
        current.pop("narration_audio", None)
    if language:
        current["revoiced_language"] = language
    if failed:
        current["revoice_failed_sentences"] = failed
    else:
        # Cleared on a clean run: a stale count would go on claiming sentences
        # are missing from a narration that has every one of them.
        current.pop("revoice_failed_sentences", None)
    store.save_project(current)

    # The chapters this render will carry, worked out now (a pure function of
    # the record read at the start - see below, where they are written) so a
    # cancel at the music pass can say whether the file lacks them too.
    chapters = edit.chapters_for(record, source_duration)

    # The music, as a second pass over the muxed output and in place: the
    # picture copied, the voice's level untouched, the standalone narration
    # track (voice only) not opened. Asked about a cancel before it starts
    # and while it runs - a mix of a long output can run for seconds - and a
    # cancel here leaves the voice-only file the record above describes (the
    # pass writes aside and removes its part file).
    if music_clips:
        from core import video_creator

        voice_only = CANCELLED_VOICE_ONLY.format(chapters=" or the chapters" if chapters else "")
        if jobs.cancel_requested_here():
            return _cancelled("mix", _report, voice_only)
        count = len(music_clips)
        _report(0.95, f"Mixing {count} music clip{'' if count == 1 else 's'}…")
        mixed = video_creator.mix_music(
            out, music_clips, out, cancel_check=jobs.cancel_requested_here,
            output_seconds=applied.output_duration,
        )
        if not mixed:
            if jobs.cancel_requested_here():
                return _cancelled("mix", _report, voice_only)
            raise RuntimeError("The music could not be mixed; the server log has the reason.")
        # The file changed again, so its stamp does too (the page's cache
        # token is that timestamp), and the edit stamp with it - the output
        # differs from an unedited render whether or not a track was cut.
        current["revoiced_at"] = datetime.now(timezone.utc).isoformat()
        current["edit_rendered_at"] = current["revoiced_at"]
        # How many clips this file carries, for the page.
        current["music_rendered"] = len(music_clips)
        store.save_project(current)

    # The chapters, on the FINAL file - after the mux and after the music
    # pass, which rewrote it in place - so they are on the file the user
    # downloads. Computed at render time from the record the job is rendering
    # (trap 42: the stored markers projected through the picture's list AS
    # STORED at render time; the same ``record`` the picture was cut from,
    # so the chapters and the cut cannot disagree about the list - and
    # ``require_idle`` holds every edit off while this job runs). No drawn
    # markers, no remux. A remux that fails logs and leaves the file as it
    # was; it never fails the job, as the deck path never lets its chapters
    # fail a render. (``chapters`` was computed above the music pass.)
    if not chapters and source_duration is None and applied.markers:
        # Reachable only when audio.wav went after the markers were stored
        # (the PUT that stores them needs it): with no length the last
        # chapter has no end, so the render carries none - said once here
        # rather than silently (the Reviewer's NIT 7).
        count = len(applied.markers)
        logger.warning(
            "Project %s has %d marker%s but its extracted audio is missing, so none became chapters",
            pid, count, "" if count == 1 else "s",
        )
    if chapters:
        from core import video_creator

        count = len(chapters)
        _report(0.98, f"Writing {count} chapter{'' if count == 1 else 's'}…")
        if video_creator.embed_chapters(out, chapters):
            # The bytes changed again, so the page's cache token does too,
            # and the edit stamp with it: the output differs from an
            # unedited render whether or not a track was cut.
            current["revoiced_at"] = datetime.now(timezone.utc).isoformat()
            current["edit_rendered_at"] = current["revoiced_at"]
            store.save_project(current)
    if failed:
        _report(1.0, f"Re-voiced - {failed} sentence{'' if failed == 1 else 's'} could not be synthesised")
    return {"video": out.name, "language": language or None, "failed_sentences": failed}
