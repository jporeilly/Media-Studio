"""Re-voice a transcribed video: regenerate its narration in a new voice
(optionally translated) while keeping the original picture, producing a new MP4.

Runs inside a job (see :mod:`services.jobs`). Reuses the carried-over media
engine: the project's transcript becomes a single re-voiceable section whose
Whisper segment timings drive :meth:`VideoProcessor._revoice_video`, which
synthesises per-sentence TTS and swaps the audio track onto the untouched
original frames (ffmpeg ``-c:v copy`` + ``apad`` to the video length).
"""

from services import projects as store


def revoice_project(pid, voice_id, speed=1.0, language=None, progress=None, provider=None) -> dict:
    """Re-voice ``pid``'s narration in ``voice_id`` (optionally translated).

    Requires a ``video`` project with a non-empty ``transcript`` (raises
    ``ValueError`` otherwise). When ``language`` names a real target language
    (from :func:`core.translator.get_available_languages`, other than English or
    the source), the narration is translated via the local Ollama model first.
    ``provider`` (edge_tts | kokoro) is the TTS provider the narration is
    synthesised with; None means the studio default at the time the job runs.

    Reconstructs a one-section ``ProjectState`` from the transcript, runs the
    carried-over ``_revoice_video`` (keeps the frames, swaps the audio), records
    ``revoiced_video`` on the project, and returns ``{"video", "language"}``.
    Raises ``RuntimeError`` if the re-voice produced no file.
    """
    record = store.get_project(pid)
    if not record:
        raise ValueError("Project not found.")
    if record.get("kind") != "video":
        raise ValueError("Only video projects can be re-voiced.")

    segments = record.get("transcript") or []
    if not segments:
        raise ValueError("Transcribe the video first.")

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
    slide0.original_segments = [
        {"start": float(s["start"]), "end": float(s["end"]), "text": s["text"]}
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

    record["revoiced_video"] = out.name
    if language:
        record["revoiced_language"] = language
    store.save_project(record)
    return {"video": out.name, "language": language or None}
