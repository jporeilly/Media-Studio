"""Transcribe a video project with the carried-over Whisper engine.

Runs inside a job (see :mod:`services.jobs`): extract the audio track, run
faster-whisper (GPU with automatic CPU fallback, handled in
``core.video_importer``), and store the timed segments on the project so the UI
can show and edit an editable transcript.
"""

from services import projects as store


def transcribe_project(pid: str, model_size: str | None, progress) -> dict:
    """Extract audio, transcribe, and persist the transcript on the project.

    ``model_size`` None/"" means the engine's recommended default; the studio's
    Whisper model (Settings › Studio) is resolved by the transcribe route at
    request time (``studio_settings.resolve_whisper_model``), not here.
    ``progress(fraction, message)`` is forwarded from the job runner. Returns a
    small summary ``{segments, language, duration}``; the full transcript is
    saved on the project record (fetch it via ``GET /api/projects/{id}``).
    Raises ValueError for a bad project / kind / missing file.
    """
    record = store.get_project(pid)
    if not record:
        raise ValueError("Project not found.")
    if record.get("kind") != "video":
        raise ValueError("Only video projects can be transcribed.")

    video_path = store.PROJECTS_DIR / pid / record["source_filename"]
    if not video_path.is_file():
        raise ValueError("The project's source video is missing.")

    # Imported lazily so the media engine and its heavy deps load only when a
    # transcription actually runs, keeping the web process light on startup.
    from core.video_importer import (
        extract_audio,
        last_load_device,
        recommended_default_model,
        transcribe_audio,
    )

    progress(0.02, "Extracting audio…")
    audio_path = extract_audio(video_path, store.PROJECTS_DIR / pid / "audio.wav")

    model = model_size or recommended_default_model()
    segments, language, duration = transcribe_audio(audio_path, model_size=model, on_progress=progress)

    transcript = [
        {"start": round(float(s.start), 3), "end": round(float(s.end), 3), "text": s.text}
        for s in segments
    ]
    # Saved onto the record as it is NOW, not the copy read when the job
    # started: transcription takes minutes, and a whole-list text Save that
    # landed while it ran would otherwise be reverted by this write. The new
    # transcript legitimately replaces the old one - and with it any
    # per-sentence adjustments, which belonged to sentences that no longer
    # exist - but nothing ELSE on the record should be rolled back to how it
    # looked before the job. The same re-read the generate and re-voice jobs do.
    current = store.get_project(pid) or record
    current["transcript"] = transcript
    current["language"] = language
    current["duration"] = round(float(duration), 2)
    current["transcribed_model"] = model
    current["transcribed_device"] = last_load_device()
    store.save_project(current)

    progress(1.0, f"Transcribed {len(transcript)} segments")
    return {"segments": len(transcript), "language": language, "duration": current["duration"]}
