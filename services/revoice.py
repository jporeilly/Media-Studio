"""Re-voice a transcribed video: regenerate its narration in a new voice
(optionally translated) while keeping the original picture, producing a new MP4.

Runs inside a job (see :mod:`services.jobs`). Reuses the carried-over media
engine: the project's transcript becomes a single re-voiceable section whose
Whisper segment timings drive :meth:`VideoProcessor._revoice_video`, which
synthesises per-sentence TTS and swaps the audio track onto the untouched
original frames (ffmpeg ``-c:v copy`` + ``apad`` to the video length).
"""

from datetime import datetime, timezone

from services import narration
from services import projects as store


def revoice_project(pid, voice_id, speed=1.0, language=None, progress=None, provider=None) -> dict:
    """Re-voice ``pid``'s narration in ``voice_id`` (optionally translated).

    Requires a ``video`` project with a non-empty ``transcript`` (raises
    ``ValueError`` otherwise). When ``language`` names a real target language
    (from :func:`core.translator.get_available_languages`, other than English or
    the source), the narration is translated via the local Ollama model first.
    ``provider`` (edge_tts | kokoro) is the TTS provider the narration is
    synthesised with; None means the studio default at the time the job runs.

    Reconstructs a one-section ``ProjectState`` from the transcript - the
    per-sentence adjustments (``services.narration``) travelling with it -, runs
    the carried-over ``_revoice_video`` (keeps the frames, swaps the audio),
    records ``revoiced_video`` on the project, and returns
    ``{"video", "language", "failed_sentences"}``. Raises ``RuntimeError`` if the
    re-voice produced no file, and ``ValueError`` when every sentence is muted -
    which would otherwise fail inside the job as a bare "Re-voice failed".
    """
    record = store.get_project(pid)
    if not record:
        raise ValueError("Project not found.")
    if record.get("kind") != "video":
        raise ValueError("Only video projects can be re-voiced.")

    segments = record.get("transcript") or []
    if not segments:
        raise ValueError("Transcribe the video first.")
    if not narration.count_spoken(segments):
        # Every sentence muted means no audio at all, and _revoice_video answers
        # that with a bare False the caller below turns into "Re-voice failed".
        # Said plainly here as well as at the route, so calling the service
        # directly gets the same answer.
        raise ValueError("Every sentence is muted, so there would be no narration. Unmute at least one.")

    source_video = store.PROJECTS_DIR / pid / record["source_filename"]
    if not source_video.is_file():
        raise ValueError("The project's source video is missing.")

    def _report(fraction: float, message: str = "") -> None:
        if progress:
            progress(fraction, message)

    # The narration text: the joined transcript. When a target language is
    # given, translate the whole thing once through the local Ollama model.
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
            _report(0.05, f"Translating to {language}…")
            notes_text = translator.translate_notes(
                [joined], language, config.ollama_url, ollama_model(),
            )[0]

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
    # the dict this built before.
    slide0.original_segments = [
        {
            "start": float(s["start"]), "end": float(s["end"]), "text": s["text"],
            **{key: s[key] for key in narration.OVERRIDE_KEYS if key in s},
        }
        for s in segments
    ]
    slide0.original_start_time = float(segments[0]["start"])
    slide0.original_end_time = float(segments[-1]["end"])
    pm.state.source_video_path = str(source_video)
    pm.save()

    out = project_dir / f"{source_video.stem}_revoiced.mp4"

    # Imported lazily so the media engine and its heavy deps load only when a
    # re-voice actually runs (and so tests can monkeypatch VideoProcessor).
    from services import processing

    _report(0.1, "Re-voicing…")
    processor = processing.VideoProcessor(voice_id=voice_id, speed=speed, provider=provider or "")
    ok = processor._revoice_video(pm, source_video, out, progress=progress)
    if not ok or not out.exists():
        raise RuntimeError("Re-voice failed")

    # A sentence whose synthesis failed leaves a silent hole. It used to be
    # visible only in the server log; it is counted now, kept on the record so
    # the page can still say so after the job is gone, and reported in the
    # job's result and closing message the way the AI loops report theirs.
    failed = int(getattr(processor, "failed_sentences", 0) or 0)

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
    if failed:
        _report(1.0, f"Re-voiced - {failed} sentence{'' if failed == 1 else 's'} could not be synthesised")
    return {"video": out.name, "language": language or None, "failed_sentences": failed}
