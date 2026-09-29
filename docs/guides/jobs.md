# Jobs

Anything that takes more than a moment runs as a job: it starts at once, the page shows its progress, and the project it belongs to is held until it is done.

## What you see

The card that runs the job shows its message, a progress bar and the percentage, refreshed every 1.2 seconds. Other cards on the page say "A job is running for this project (*kind*) — it must finish first." and their buttons wait. The Slides card adds "A job is running for this project — the notes can be edited again when it finishes." and, for an AI job, a **Cancel** button; the Re-voice card has a **Cancel** button for a re-voice. The Timeline's toolbar says "A job holds the project — cuts wait for it." When a job fails, its error stays on the card until another job starts.

The page follows the project's job whoever started it: after a reload, in another tab, or when someone else started it, the card that runs it shows its progress and the other cards wait, as for a job started on the page. A job someone else started is shown read-only: **Cancel** is offered only to the user who started it and to an administrator.

## What to do

**Wait for it.** One job per project at a time. Reads are unaffected: you can look at the slides, play a sentence, audition the timeline and download the transcript or a finished video while a job runs. Writes (a note, an adjustment, a cut, another job, deleting the project) are refused with "A job is running for this project (*kind*). Wait for it to finish, then try again."

**Cancel.** The Slides card has one for an AI job and the Re-voice card one for a re-voice. The per-slide AI jobs (notes, enhance, tone, translate, pacing with the model) stop after the slide in progress and keep what they wrote. The QA review stops between passes and saves nothing. The Q&A document cannot be cancelled. A re-voice stops before the next sentence it would synthesise, or while the picture is cut or the music mixed, and the Re-voice card then says what it left; see [Re-voicing a video](re-voice.md). A transcription, a video render, the slide previews and an update run to their end.

**After a restart.** Jobs live in the server's memory, so a backend restart forgets them and the work that was running is gone. The card that was running the job says "The job stopped when the server restarted. Start it again.", the other cards stop waiting and the buttons work again; dismiss the line with its cross, or start the job again. While the server cannot be reached at all, the page tries three times, stops following the job and says so on its card ("Could not reach the job (…), so the page stopped following it."), whoever started the job; the line stays for as long as the server is unreachable. When the server answers again, the page follows the job again if it is still running, or asks after it once more: a job the restart lost then gets the restart line, and one that finished meanwhile is shown as finished. When the server itself refused the job's polls with an error, the line names that error; the page follows the job once more when the project's job answer names it again, and after that leaves the line until the page is reloaded.

## Under the hood

A job is created by a POST that returns its id; the page polls `GET /api/jobs/{id}` until the status is `done` or `error`, and reads a 404 ("Job not found.") as a job the server no longer holds. It learns of a job it did not start from `GET /api/projects/{id}/job`, which answers the project's queued or running job (its id, kind, status and who started it) or nothing; the page asks when it opens and every 5 seconds while no job it follows is in flight. A job the page gave up on is followed again only from an answer that arrived after the give-up, never from the one it already had. A job that has ended is not asked again when the browser reconnects, so a restart never turns a finished job into a lost one. Two worker threads serve the whole studio, so at most two jobs run at once and further jobs wait as `queued`. A job that belongs to a project holds it: the kinds `transcribe`, `generate` (a deck or PDF render, previews included), `revoice` (the Re-voice card's **Re-voice** and **Render**), `render-slides`, `ai-notes`, `ai-enhance`, `ai-qa`, `ai-tone`, `ai-translate`, `ai-pacing` and `ai-qa-doc`; `update` belongs to no project, and only one update runs at a time (a second is refused with "An update is already running. Wait for it to finish."). The check and the start are one step under a lock, so two requests cannot both start a job on the same project. A second request for the slide previews while one is in flight joins it instead of being refused.

The reason for one job per project is that every such job holds its own copy of the project's state; a render would write its stale copy of the notes over what an AI job or an editor wrote, so the app refuses the collision rather than losing the edit.

Cancellation is cooperative: `POST /api/jobs/{id}/cancel` raises a flag, and a job that checks it stops at its next check and finishes as `done` with `cancelled` in its result; a job that never looks at the flag runs to its end. Only the user who started a job, or an administrator, may cancel it; the project's owner may also read a job on their project whoever started it, and nobody else may read it (an update records no starter: any signed-in user may read and follow it, and only an administrator may cancel it); a cancel is recorded in the audit log as `job.cancel`. A render, a re-voice and a transcription read their narration provider, voice, Whisper model and render defaults when they are requested, so changing a setting while one waits does not change it; the AI assistant and a re-voice's translation read the Ollama model each time they call it.

## See also

- [Projects](projects.md)
- [The AI assistant](ai-assistant.md): what Cancel does per action
- [Updates](../admin/updates.md): the one job with no project
