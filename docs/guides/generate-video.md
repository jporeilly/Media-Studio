# Generate a video

The **Generate video** card on a deck or a PDF project renders the slides and their speaker notes into a narrated MP4, with the transitions, cards and extras you choose.

## What you see

At the top: **Narration provider** (Edge TTS or Kokoro), **Voice** (the provider's list; "Studio default" while the list is empty), **Speed** (0.5 to 2, in steps of 0.05), **Output preset** (YouTube 1080p, YouTube 4K, LinkedIn / Teams, Zoom webinar, Vimeo 1080p, Vimeo 4K, with the chosen preset's description under the row), and two buttons: **Generate video**, which reads **Regenerate** once a video exists, and **Preview 15 s**.

**More options** unfolds six groups; while it is folded, a line beside it lists what differs from a plain render, or "Defaults".

| Group | Fields |
| --- | --- |
| **Transition** | **Effect** (None, Fade to Black, Fade to White, Crossfade / Dissolve, Slide Left, Slide Right, Slide Up, Slide Down, Zoom In), **Duration (s)** 0.1–2, **Pause between slides (s)** 0–5 |
| **Intro card** | **Title**, **Subtitle**, **Duration (s)** 1–10; leave the title empty for no card |
| **Outro card** | **Closing text**, **Duration (s)** 1–10; leave the text empty for none |
| **Watermark** | **Text**, **Position** (Top Left, Top Right, Bottom Left, Bottom Right, Center), **Opacity** 0.1–1; leave the text empty for none |
| **Subtitles** | **Mode**: None; Per slide (one cue per slide from its notes, an SRT); Whisper, word-level (SRT and VTT with word timings, a second pass after the render) |
| **Extra formats** | **WebM**, **GIF (first 30 s)**, **Audio-only MP3** |

Under the fields: "A job is running for this project (*kind*) — it must finish first." while a job holds the project, whoever started it and wherever, the voice list's notice when it has one, and any value out of range as an error.

Once rendered: a preview player ("Preview — the first 15 seconds"), the video player, **Download video**, and a button per sidecar that exists: **Subtitles (SRT)**, **Subtitles (VTT)**, **WebM**, **GIF**, **Audio (MP3)**.

## What to do

1. Write the notes first; see [The slide editor](slide-editor.md). A slide without notes shows for a few seconds in silence.
2. Choose the provider, the voice and the speed. The provider and voice start as the studio defaults.
3. Choose an **Output preset** for where the video is going. [Output presets](../reference/output-presets.md) lists the numbers.
4. Open **More options** when you want a transition, cards, a watermark, different subtitles or an extra format. The transition, pause and watermark start as the studio defaults; the rest as the engine's own (no cards, per-slide subtitles, no extra formats).
5. Press **Preview 15 s** to check the voice and the options on the opening seconds, or **Generate video** for the whole thing. Both are jobs: the card shows the progress and the message.
6. Download the video and the sidecars when the job is done. **Regenerate** renders again with the current notes and options; the file names stay the same.

## Under the hood

The render is a job of kind `generate`. The narration provider, the voice and the studio's defaults are read when the job is requested, so a change to the studio settings while it queues does not alter it; the one exception is the **Kokoro language**, which Kokoro reads each time it speaks a sentence. An unknown provider, or a voice that belongs to the other provider, is refused before the job starts.

The MP4 is written to the project's own directory, `data\projects\<id>\<source stem>.mp4`, and a re-render overwrites it. A preview lands beside it as `<stem>_preview.mp4`; the two never replace each other. The sidecars are written next to the video: `<stem>.srt` for per-slide subtitles; `<stem>.whisper.srt` and `<stem>.whisper.vtt` for the Whisper pass, which transcribes the finished video with the studio's Whisper model and therefore lengthens the job; `<stem>.webm` (VP9 and Opus); `<stem>.gif`, the first 30 seconds at 5 frames a second, 480 pixels wide; `<stem>_audio.mp3`. A full render replaces the sidecar list with what it produced; the preview and the Q&A document stay listed.

Each slide is narrated from its notes in the chosen voice, or the slide's own **Voice override**, and the audio is cached by text, voice and speed, so a re-render with unchanged notes synthesises nothing again. A slide's pause is its own **Pause after slide (s)** when set, else the job's pause between slides. A deck is encoded at 2 frames a second when nothing on it moves, and at 24 when it has a transition effect or an animated slide. An animated slide plays for as long as its narration: the clip repeats when it is shorter and is cut when it is longer, so the slides after it stay with their narration. The rendered MP4 carries a chapter per slide, titled with the first line of the slide's notes (up to 60 characters) or "Slide N". Slide images are exported through PowerPoint (or the title-only fallback) if the previews are not there yet; PowerPoint exports one deck at a time.

**The encode.** A full render is encoded with libx264's `medium` setting as H.264 High in 4:2:0 colour, with AAC audio at the preset's bitrate (48 kHz stereo) and the file's index at the front, so the video starts playing in a browser, or on a site it is uploaded to, before it has all arrived; [Output presets](../reference/output-presets.md) lists what each preset passes. `medium` makes a much smaller file than libx264's fast `ultrafast` setting at the same picture quality: ten slides with 14 minutes of narration came out at 24.0 MB against 32.7 MB. The narration is mixed in at the level it was synthesised at; before this release a deck's narration came out about 3 dB quieter than that, while a re-voiced video already kept its level. **Preview 15 s** keeps `ultrafast`, because it is for checking the voice and the options, not for delivering, so it stays quick; it keeps the profile, the colour format, the audio bitrate and the index at the front, so it plays wherever the full render does.

**How the picture is made.** The app draws each slide's picture, the title cards and the watermark once, as still images, and ffmpeg builds every frame of the video from them while it encodes. A slide that stays on screen costs little more than its encode, so a transition or the watermark adds seconds rather than minutes. Measured on an 8-core desktop at 1080p with the ffmpeg the app ships, on a 20-slide deck with 7½ minutes of narration, a render with no transition takes about 25 s and one with a crossfade and the watermark about 50 s; Zoom In with the watermark, the dearest transition, about 65 s. Before this release they took about 105 s and 42 minutes. About half of a render with no transition is spent preparing the narration track. The video is made in parts, each encoded on its own and joined without being encoded again, so a long deck needs no more memory than a short one: about 1.4 GB at 1080p with a transition, whether the deck has 20 slides or 60. A part holds up to 8 slides and pauses at 1080p, 18 at 720p and 4 at 4K, and more only when that many would last less than 48 frames. The video never ends before its narration: the last frame is held until the narration has.

**The transitions.** **Fade to Black** fades each slide in from black and out to black, and a pause between slides, when you set one, is black. **Crossfade / Dissolve** does the same; it does not blend one slide into the next. **Fade to White** does the same through white, and its pause is white. **Slide Left**, **Slide Right**, **Slide Up** and **Slide Down** bring each slide in over black, moving the way the name says: Slide Left enters from the right edge, Slide Up from the bottom. **Zoom In** brings each slide in from 1.3 times its size while it fades up from black. Each transition takes the **Duration** you set, or a third of the slide if that is shorter; the first slide never opens with one and the last never closes with one. Before this release, Fade to White never faded in and cut to black before it turned white, Slide Left moved like Slide Right, and Slide Up moved like Slide Down.

A generate job runs to its end; the app offers no Cancel for it. It is refused with a 409 while any job holds the project, and every slide write is refused while it runs.

The job is recorded in the audit log as `project.generate` with the job id, the preset (or "preview" for **Preview 15 s**), and the provider and voice.

## See also

- [Output presets](../reference/output-presets.md): the resolution and the encode of each preset
- [Studio settings](../admin/studio-settings.md): the defaults the card starts from
- [Narration engines](../ai/narration-engines.md): Edge TTS and Kokoro
- [Jobs](jobs.md)
