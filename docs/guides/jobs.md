# Jobs

Anything that takes more than a moment runs as a job: it starts at once, the page shows its progress, and the project it belongs to is held until it is done.

## What you see

The card that started the job shows its message, a progress bar and the percentage, refreshed every 1.2 seconds. Other cards on the page say "A job is running for this project (*kind*) — it must finish first." and their buttons wait. The Slides card adds "A job is running for this project — the notes can be edited again when it finishes." and, for an AI job, a **Cancel** button. The Timeline's toolbar says "A job holds the project — cuts wait for it." When a job fails, its error stays on the card until another job starts.

The page follows only a job it started itself: after a reload, in another tab, or for a job someone else started, no card shows it and the buttons stay enabled; the server still refuses every write with the message below.

## What to do

**Wait for it.** One job per project at a time. Reads are unaffected: you can look at the slides, play a sentence, audition the timeline and download the transcript or a finished video while a job runs. Writes (a note, an adjustment, a cut, another job) are refused with "A job is running for this project (*kind*). Wait for it to finish, then try again." Deleting the project is not held by a job; on Windows it fails while the job has one of the project's files open.

**Cancel.** Only the Slides card has one, for an AI job. The per-slide AI jobs (notes, enhance, tone, translate, pacing with the model) stop after the slide in progress and keep what they wrote. The QA review stops between passes and saves nothing. The Q&A document cannot be cancelled. A re-voice honours `POST /api/jobs/{id}/cancel` while the picture is cut or the music mixed, but the app has no button for it. A transcription, a video render, the slide previews and an update run to their end.

**After a restart.** Jobs live in the server's memory, so a backend restart forgets them and the work that was running is gone. The page does not notice: the card keeps its last progress and the other cards keep waiting. Reload the page, then start the job again.

## Under the hood

A job is created by a POST that returns its id; the page polls `GET /api/jobs/{id}` until the status is `done` or `error`. Two worker threads serve the whole studio, so at most two jobs run at once and further jobs wait as `queued`. A job that belongs to a project holds it: the kinds `transcribe`, `generate` (a deck or PDF render, previews included), `revoice` (the Re-voice card's **Re-voice** and **Render**), `render-slides`, `ai-notes`, `ai-enhance`, `ai-qa`, `ai-tone`, `ai-translate`, `ai-pacing` and `ai-qa-doc`; `update` belongs to no project. The check and the start are one step under a lock, so two requests cannot both start a job on the same project. A second request for the slide previews while one is in flight joins it instead of being refused.

The reason for one job per project is that every such job holds its own copy of the project's state; a render would write its stale copy of the notes over what an AI job or an editor wrote, so the app refuses the collision rather than losing the edit.

Cancellation is cooperative: `POST /api/jobs/{id}/cancel` raises a flag, and a job that checks it stops at its next check and finishes as `done` with `cancelled` in its result; a job that never looks at the flag runs to its end. Only the user who started a job, or an administrator, may read or cancel it (an update records no starter, so any signed-in user may read it); a cancel is recorded in the audit log as `job.cancel`. A render, a re-voice and a transcription read their narration provider, voice, Whisper model and render defaults when they are requested, so changing a setting while one waits does not change it; the AI assistant and a re-voice's translation read the Ollama model each time they call it.

## See also

- [Projects](projects.md)
- [The AI assistant](ai-assistant.md): what Cancel does per action
- [Updates](../admin/updates.md): the one job with no project
