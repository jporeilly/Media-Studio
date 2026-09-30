# Troubleshooting

Symptoms, causes and fixes for the problems most often met. The server log at `data\logs\app.log` (DEBUG level, rotating) is the first place to look for anything not listed here.

## Starting the desktop app

The splash streams the backend's own log lines. After 45 seconds it says a first launch can take a couple of minutes while the media engine loads; after four minutes, or as soon as the backend process dies, it shows the last log lines, what the shell resolved, and four buttons:

| Button | What it does |
| --- | --- |
| **Try again** | Restarts the backend in place. A port held for a moment, an antivirus scan or a slow disk usually clear on a second attempt. |
| **Copy details** | Puts the startup report on the clipboard. |
| **Save report** | Writes `startup-report.txt` into `data\` and reveals it in Explorer; attach it to a support request. |
| **Open data folder** | Opens `data\`. |

| Symptom | Cause | Fix |
| --- | --- | --- |
| "The backend stopped with an error" with a traceback | Python raised while starting; the last lines name the cause | Run the environment check below; reinstall if a package is missing |
| "The port was already taken" | Another copy of the app is running | Close it, or sign out and back in |
| "The backend produced no output at all" | An incomplete install (`main_py_found` or `vendored_python` false in the report) | Reinstall, or run from a checkout with `python main.py` |

**Check what the machine is missing.** The installer ships a checker that prints one OK / WARN / FAIL row per dependency and, at the end, the fixes for the WARN and FAIL rows that have one:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Media-Studio-Enterprise\provisioning\check-environment.ps1"
```

Only the WebView2 runtime, a usable Python with its core packages, and an unwritable data directory are FAILs, because without them the window does not open. The media stack, ffmpeg, ffprobe, git, disk space under 3 GB and Ollama on port 11434 are warnings that name the feature they affect. Add `-Json` for machine-readable output; the exit code is 1 when anything fails.

**Port 5680.** From a checkout, `python main.py` stops whatever already listens on its port before it starts (pass `--keep-running`, or set `MEDIA_STUDIO_KEEP_RUNNING`, to leave it alone; `--port` chooses another). The desktop app prefers 5680 and quietly takes another free port when it is held; the address in use is in the splash's log lines and, after a failed start, in the startup report.

## Projects and jobs

| Symptom | Cause | Fix |
| --- | --- | --- |
| "A job is running for this project (*kind*). Wait for it to finish, then try again." | One job per project; a render, re-voice, transcription, previews or AI action holds it, perhaps one started in another tab or by someone else (the project page shows it within five seconds) | Wait; for an AI job, **Cancel** on the Slides card, for a re-voice **Cancel** on the Re-voice card, if you started it or are an administrator ([Jobs](../guides/jobs.md)) |
| "A job holds the project — cuts wait for it." on the Timeline | The same | The same; reads (Play, download) still work |
| "The job stopped when the server restarted. Start it again." on a card | The backend restarted while the job ran; jobs live in memory, so the work was lost | Start the job again; the cross dismisses the line |
| "Could not reach the job (…), so the page stopped following it." on a card | Three polls in a row failed: "Failed to fetch" when the server could not be reached, usually because it is restarting; otherwise the error the server answered | For "Failed to fetch", nothing: when the server answers again the page follows the job again if it still runs, or shows the restart line if the restart lost it; if the server stays down, restart it. For a server error, the page tries once more, then leaves the line: reload the page, and look in `app.log` |
| "Could not delete this project: *file*. Something is still using it" | A file in the project's directory is open elsewhere (Windows) | Close the video or the export and try again; nothing was half-removed |
| "Every sentence is muted, so there would be no narration. Unmute at least one." | Every sentence of the transcript is muted | Unmute one |
| "The edit removes every sentence that would be spoken, so there would be no narration. Keep at least one, or clear the edit." | The Timeline's edit cuts away every sentence that is not muted | Keep at least one, or clear the edit |
| "Keep at least one range — that selection would remove the whole *picture*." | A cut or trim would leave a lane empty | Keep something on every unlocked lane |
| "The music rides the picture, so a cut with the picture locked would leave every clip exactly where it is." | Music is the only unlocked lane | Unlock Video to cut both, or trim or delete the clip itself |

## Slides and PowerPoint

| Symptom | Cause | Fix |
| --- | --- | --- |
| Previews show each slide's title on white; "PowerPoint was not available on the server, so these previews show each slide's title only." | No PowerPoint on the server, or the bundled runtime's PowerPoint bridge could not find its DLLs | Install PowerPoint on the machine that runs the app. When it is installed, find "PowerPoint COM failed: … — using Pillow fallback" in `data\logs\app.log` and report that line. The video still renders with the basic slides. |
| The AI's vision checkbox is off: "the slide previews are the title-only fallback…" | The same previews | The same |
| "These previews were rendered before their source was recorded…" and **Render again** | Previews from an older version have no recorded source | **Render again** |
| Rendering the previews takes a minute or two | A deck goes through PowerPoint, one deck at a time on the server | Wait; a PDF's pages take seconds |

## Narration and transcription

| Symptom | Cause | Fix |
| --- | --- | --- |
| "Could not fetch the Edge TTS voices (offline?)" under a voice list | Edge TTS is Microsoft's online service; the machine is offline and no list was cached | Connect; the last fetched list is shown when there is one |
| "Kokoro's model is not downloaded yet — it downloads once (about 340 MB) on the first Kokoro narration." | Kokoro's model is fetched on the first Kokoro render or re-voice, never by Play or a voice list | Re-voice or generate once with Kokoro (needs internet that once), or preview with Edge TTS |
| Play answers "*provider* returned no audio for this sentence" | The voice service did not answer within 30 s, or the voice id is not one of its voices | Try again, or pick a voice from the list |
| The **Details** card's **Transcribed on** reads CPU on a machine with an NVIDIA GPU; "GPU transcription failed … retrying on CPU" in the log | The CUDA runtime wheels are not installed | Install `requirements-gpu.txt` into the app's Python and **Restart backend** ([Transcription](../ai/transcription.md)) |
| N sentences "could not be synthesised in the last re-voice" | The voice service failed on those sentences, or their offset pins them past the end of the picture | Re-voice again; pull the offset back |
| The re-voice ignored a translation | Ollama was not reachable: a failed translation keeps the original text and logs "Translation failed for a note: …" (or "Translation HTTP *status* for a note") in `app.log` | Start Ollama and re-voice again ([Ollama](../ai/ollama.md)) |
| A translated re-voice ignored the offsets and mutes | A translation is spread across the video by length; per-sentence adjustments do not apply to it | Expected; keep the original language to use them |

## The AI assistant

| Symptom | Cause | Fix |
| --- | --- | --- |
| "Ollama unreachable at http://localhost:11434 — AI actions disabled" | Ollama is not running, or the URL in `data\config.json` is wrong | Start Ollama (`winget install -e --id Ollama.Ollama`); fix `ollama_url` |
| "*model* is not pulled on the Ollama server (ollama pull *model*)" | The configured model is not on the server | `ollama pull <model>`, or choose another in the Studio card |
| "*model* · vision off: *reason*" | Not a vision model, or the previews are not real renders | Choose a vision model (gemma3, llava, moondream, llama3.2-vision, qwen2.5vl, minicpm-v); render the previews |
| An action stopped early: "Ollama is not reachable … Stopped after N of M slides; the notes written so far are saved." | The server went away mid-run | Start Ollama; run the action again; slides already done keep their notes |
| "The model did not answer within 120 s." (180 s for a QA review pass) | A slow model on a slow machine | A smaller model, or a GPU |

## The Timeline and the music

| Symptom | Cause | Fix |
| --- | --- | --- |
| A clip is hatched and named "missing"; the banner says the render will refuse | The file is no longer in `assets\music\`: removed outside the app, or the project was restored or copied without it (the app refuses to delete a file a project uses) | Put the file back under the same name, or press the banner's **Remove the missing clip** (**Remove the N missing clips**) ([The Music lane](../guides/music.md)) |
| The render says "music file '*name*' is missing — remove the clip or upload the file again" | The same, at render time | The same |
| "That undo was refused and has been taken off the undo list … file '*name*' is not in the library" | The undo would lengthen a clip whose file is missing — the undo of a trim or a split of it, or of its removal; a missing clip can only be kept as it is or made shorter | Put the file back under the same name before undoing; a refused step is taken off the undo list ([The Music lane](../guides/music.md)) |
| "That cut (…) would cut across *name*, but its file is no longer in the library" (or "That trim …") | A cut of the picture, or a piece's edge dragged in, would shorten, split or remove a clip whose file is missing, and could not be undone | Lock the Music lane to cut the picture alone, or remove the missing clip first |
| "'*name*' is already in the library" on upload | Names are unique, without regard to case; a clip refers to a file by name | Rename the file, or delete the old one |
| "'*name*' is used by N projects (…); remove its clips from them first." | A project's timeline still has a clip on the file | Remove the clips, then delete |
| "'*name*' has N channels; music must be mono or stereo" | A surround file | Convert it to stereo and upload again |
| "This browser cannot show frames from this video file" | A container the browser cannot decode (`.mkv`, `.avi`) | The waveform and the blocks are still to scale; the render is unaffected |

## Updates

| Symptom | Cause | Fix |
| --- | --- | --- |
| "This install is not a Git checkout, so the app cannot self-update." | The app directory has no `.git` | Update with a newer installer |
| "git is not installed on this machine, so the app cannot self-update." | No `git` on PATH | `winget install -e --id Git.Git` |
| "Could not reach the Git remote" | No credentials for the remote, or no network | Store credentials that can read the repository, or use a newer installer |
| The update fails with "git pull failed: …" | The update touches a file that was edited by hand under the checkout, or the local branch has commits upstream does not (it has diverged) | Revert the hand edit and update again; a diverged branch has to be reconciled with Git by hand |
| "The backend hasn't come back after four minutes." | The restart failed, or a cold start is slower than the wait | Relaunch the app; **Keep waiting** re-arms the wait |

## Where to look

- `data\logs\app.log`: the server's log, rotating: everything the app's own loggers write, from DEBUG up, and every other module's warnings and errors, each line once: the re-voice translation, the Q&A document, Analyze and the updater among them, the server's own (the traceback of a request that failed with a 500), and a failed job's traceback
- The **Audit log** card: who changed what
- `GET /api/system/health`: a liveness check that needs no sign-in
- `pytest` from a source checkout: the installation itself

## See also

- [Updates](updates.md)
- [Data and backup](data-and-backup.md)
- [INSTALL.md](../../INSTALL.md): the installer's own troubleshooting
