# Data and backup

Everything the app creates lives in `data\` and `assets\` beside the code, renders included; there is no backup feature, so back up by stopping the app and copying them.

## The layout

In a desktop install the app's code is `C:\Media-Studio-Enterprise\app\`, so `data\` is `C:\Media-Studio-Enterprise\app\data\`; from a checkout both folders sit next to `main.py`. When the desktop app fails to start, the splash's failure panel has an **Open data folder** button that opens `data\`.

```
data\
  media_studio.db              accounts, sessions and the audit log (SQLite)
  config.json                  the studio settings, the password policy, the Ollama URL
  projects\<id>\               one directory per project (a twelve-character id)
    <source file>              the deck, PDF or video exactly as uploaded
    project.json               the record: name, kind, owner, transcript, edit, outputs
    audio.wav                  a video's extracted audio (16 kHz mono), from the transcription
    <stem>.mp4                 a deck's or PDF's rendered video; <stem>_preview.mp4 the preview
    <stem>.srt, <stem>.whisper.srt / .vtt, <stem>.webm, <stem>.gif, <stem>_audio.mp3
                               the render's sidecars
    <stem>_revoiced.mp4        a video's re-voice (the Re-voice card's Re-voice or Render); <stem>_revoiced_narration.mp3 its voice track
    <stem>_project\            the slide editor's inner project: notes and history, slide images, per-slide audio
    revoice\                   the re-voice job's working project
    exports\                   <name>-notes.pptx and <name>-qa.txt
  logs\app.log                 the server log (rotating)
  cache\                       every synthesised sentence and slide, keyed by text, voice and speed; never swept
  temp\                        scratch files
assets\
  music\                       the studio's music library: the files, index.json, and a .peaks.json per file
  models\kokoro\               Kokoro's model and voices, downloaded once (about 340 MB)
  temp\                        a render's scratch directory, removed when the job ends
  finished\                    created, but not used by this edition
```

Renders belong to their project: a deck's MP4 and sidecars, a video's re-voice and narration track, all sit in `data\projects\<id>\`, and deleting the project removes them. The Studio card's **Output folder** is not used by this edition.

## Backing up

There is no backup feature in the app.

1. Stop the app: close the desktop window, or stop the server. Jobs live in memory, so a job in flight is lost: let it finish first.
2. Copy `data\` and `assets\music\`. That is the accounts, the settings, every project with its transcript, edit and renders, and the music library. `data\cache\`, `data\temp\` and `assets\temp\` can be left out; `assets\models\kokoro\` downloads again on the first Kokoro narration if it is missing.
3. Start the app.

A single project can be backed up on its own: its directory `data\projects\<id>\` is self-contained, and the music files its clips name are in `assets\music\` by name.

## Restoring

1. Stop the app.
2. Put `data\` (or the one project directory, keeping its twelve-character name) and `assets\music\` back where they were. A directory whose name is not a project id is not listed.
3. Start the app. Accounts, sessions and the audit log come from `media_studio.db`; settings from `config.json`; the projects are listed from disk.

A project restored without its `audio.wav` keeps its transcript, but a new cut or marker cannot be stored and a stored cut cannot be rendered until `audio.wav` is back, because the edit is measured against that file. Restore the file, or press **Transcribe again** on the Transcript card, which extracts it afresh from the video but replaces the transcript and drops every sentence's adjustments (the cuts, markers and music stay); or clear the edit. A music file restored under a different name is a different file: clips refer to files by name.

## Under the hood

Paths are resolved from the app's own folder, which is why the desktop edition installs per-user into a writable location. A project's `project.json` is written atomically (a temp file and a rename) so a reader never sees a torn record, and a project directory is deleted by renaming it out of the id space first. `media_studio.db` is created and migrated on every start, so a database from an older version is brought up to date; a missing database means a fresh `admin` / `admin` account. `config.json` merges over the built-in defaults, so a missing key is not an error.

## See also

- [INSTALL.md](../../INSTALL.md): the desktop install's folders, uninstalling and what is left behind
- [Projects](../guides/projects.md)
- [The Music lane](../guides/music.md)
