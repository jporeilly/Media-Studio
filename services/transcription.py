"""Transcribe a video project with the carried-over Whisper engine.

Runs inside a job (see :mod:`services.jobs`): extract the audio track, run
faster-whisper (GPU with automatic CPU fallback, handled in
``core.video_importer``), and store the timed segments on the project so the UI
can show and edit an editable transcript.

**Transcribing again** (the Transcript card's **Transcribe again**, the same
route and the same Whisper-model rule as the first time) is the same job over
a project that already has a transcript, and what it does to the record is
deliberate:

- **replaced**: the transcript - Whisper's words and windows, so every
  correction made to the words is gone - with the detected language, the
  duration, the model, the device and ``transcribed_at``; and ``audio.wav``,
  extracted again from the source, which is how a lost ``audio.wav`` is
  rebuilt;
- **dropped**: every sentence's adjustments (``services.narration``: offset,
  mute, voice and its provider, speed). They live ON the old sentences, which
  no longer exist, so none of them can land on a new sentence - by
  construction, not by a matching rule: nothing is carried across, even when
  Whisper happens to return the same windows. How many sentences had one is
  counted and reported (``adjustments_dropped``, and the job's closing line);
- **kept**: everything that is not attached to a sentence - the edit's kept
  ranges and markers (SOURCE seconds) and its music clips (OUTPUT seconds),
  so the cuts stay where they were in time and the new sentences are
  projected through them as the old ones were; the last re-voice's files and
  stamps (they describe a render that was made, and the next re-voice
  replaces them); the name, the owner and everything else on the record.

The memoised speaking rate (``narration.forget_baseline``) is dropped with
the sentences it was measured from.
"""

from datetime import datetime, timezone

from services import projects as store


def transcribe_project(pid: str, model_size: str | None, progress) -> dict:
    """Extract audio, transcribe, and persist the transcript on the project.

    ``model_size`` None/"" means the engine's recommended default; the studio's
    Whisper model (Settings › Studio) is resolved by the transcribe route at
    request time (``studio_settings.resolve_whisper_model``), not here.
    ``progress(fraction, message)`` is forwarded from the job runner. Returns a
    small summary ``{segments, language, duration, adjustments_dropped}``; the
    full transcript is saved on the project record (fetch it via
    ``GET /api/projects/{id}``). A project that already had a transcript
    loses its words and every sentence's adjustments and keeps its edit (see
    the module docstring). Raises ValueError for a bad project / kind /
    missing file.
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

    # Fresh dicts of Whisper's three keys and nothing else: no adjustment of
    # the old transcript can ride onto a new sentence (the module docstring).
    transcript = [
        {"start": round(float(s.start), 3), "end": round(float(s.end), 3), "text": s.text}
        for s in segments
    ]
    from services.narration import OVERRIDE_KEYS, forget_baseline

    # Saved onto the record as it is NOW, not the copy read when the job
    # started: transcription takes minutes, and a whole-list text Save that
    # landed while it ran would otherwise be reverted by this write. The new
    # transcript legitimately replaces the old one - and with it any
    # per-sentence adjustments, which belonged to sentences that no longer
    # exist - but nothing ELSE on the record should be rolled back to how it
    # looked before the job. The same re-read the generate and re-voice jobs
    # do, under the lock every other writer of this record takes, so the
    # adjustments counted are the ones really dropped.
    with store.project_lock(pid):
        current = store.get_project(pid) or record
        previous = current.get("transcript")
        dropped = sum(
            1 for seg in (previous if isinstance(previous, list) else [])
            if isinstance(seg, dict) and any(key in seg for key in OVERRIDE_KEYS)
        )
        current["transcript"] = transcript
        current["language"] = language
        current["duration"] = round(float(duration), 2)
        current["transcribed_model"] = model
        current["transcribed_device"] = last_load_device()
        # When THIS transcript was made: the page keys the Transcript card
        # on it, so an undo the Timeline held for the old sentences (by
        # index) can never be replayed onto the new ones.
        current["transcribed_at"] = datetime.now(timezone.utc).isoformat()
        store.save_project(current)
    # The speaking rate was measured from three of the old sentences.
    forget_baseline(pid)

    count = len(transcript)
    message = f"Transcribed {count} segment{'' if count == 1 else 's'}"
    if dropped:
        message += f"; the adjustments on {dropped} sentence{'' if dropped == 1 else 's'} were dropped"
    progress(1.0, message)
    return {
        "segments": len(transcript), "language": language, "duration": current["duration"],
        "adjustments_dropped": dropped,
    }
