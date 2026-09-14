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
    record["transcript"] = transcript
    record["language"] = language
    record["duration"] = round(float(duration), 2)
    record["transcribed_model"] = model
    record["transcribed_device"] = last_load_device()
    store.save_project(record)

    progress(1.0, f"Transcribed {len(transcript)} segments")
    return {"segments": len(transcript), "language": language, "duration": record["duration"]}
