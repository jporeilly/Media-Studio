# Studio settings

The **Studio** card on the Settings page holds the defaults every job starts from, from the narration voices to the Whisper and Ollama models; administrators change them.

## What you see

Every field with its meaning, its default on a fresh install, and when a change takes effect. A change is saved with **Save settings**, and the card then says "Settings saved — they apply to the next job."

| Field | Meaning | Default | Applies to |
| --- | --- | --- | --- |
| **Narration provider** | Edge TTS ("Microsoft's online voices — free, needs internet.") or Kokoro ("Local, offline voices — the model (about 340 MB) downloads once, on the first Kokoro narration.") | Edge TTS | the provider preselected on every project page; the next job |
| **Whisper model** | Recommended default ("large-v3-turbo on a GPU, medium on the CPU"), or tiny (~75 MB), base (~145 MB), small (~484 MB), medium (~1.5 GB), large-v3-turbo (~1.6 GB), distil-large-v3 (~1.5 GB, English only), large-v3 (~3.1 GB) | Recommended default | the next transcription, and the next Whisper subtitle pass |
| **Default Edge voice** | "Used when a job names no voice." | `en-US-AriaNeural` | the voice preselected on a project page under Edge TTS; a job with no voice |
| **Kokoro voice** | "Used when a Kokoro job names no voice." | `af_heart` | the same, under Kokoro |
| **Kokoro language** | "The language Kokoro reads the notes in.": English (US), English (UK), French, Italian, Japanese, Chinese, Spanish, Hindi, Portuguese (BR) | English (US) | the next Kokoro narration or preview |
| **Ollama model** | The local model that translates a re-voice **and** runs the whole AI assistant (the card's hint names only the translation) | `gemma4:12b` | the next translation or AI action |
| **Output folder** | "Not used by this edition yet — renders are saved with their project." An absolute path on the server, created if missing and checked to be writable | `<app>\assets\finished` | nothing in this edition |
| **Transition pause** | "Seconds of silence between slides", 0 to 5 in steps of 0.1 | 0 | prefilled into a new Generate card's **Pause between slides (s)** |
| **Music volume** | "Background music level", 0 (silent) to 1 (full) in steps of 0.05 | 0.25 | the level a new music clip starts at on the Timeline |
| **Slide transition** | None, Fade to Black, Fade to White, Crossfade / Dissolve, Slide Left, Slide Right, Slide Up, Slide Down, Zoom In | None | prefilled into a new Generate card's **Effect** |
| **Transition duration** | "Seconds the effect lasts", 0.1 to 2 in steps of 0.1 | 0.5 | prefilled into a new Generate card's Transition **Duration (s)** |
| **Watermark text** | "Brand text drawn over every render; leave it empty for none." | empty | prefilled into a new Generate card's **Text** |
| **Watermark position** | Top Left, Top Right, Bottom Left, Bottom Right, Center | Bottom Right | prefilled into the Generate card |
| **Watermark opacity** | 0.1 (faint) to 1 (solid) in steps of 0.05 | 0.5 | prefilled into the Generate card |

Inside the card, under the fields, each voice list can add a line of its own. Edge TTS shows a notice when its list is more than a week old and could not be refreshed (the last fetched list is shown), and an error when there is no list at all (offline with nothing cached). Kokoro shows a notice until its model is on disk (a built-in list is shown).

## What to do

Change any field and press **Save settings**. The card catches a value out of range or an empty Ollama model or output folder before saving: it names the problem and keeps **Save settings** disabled. The server checks every changed value again and refuses the whole save with the reason when one is wrong (a relative output folder, a voice from the other provider). Non-administrators see the values and "Only admins can change these settings."

"Applies to the next job" means exactly that. A render, a re-voice and a transcription read the provider, the voices, the Whisper model and the render defaults when they are requested, so a job already waiting or running is untouched; the AI assistant and a re-voice's translation read the Ollama model each time they call it, and Kokoro reads its language as it speaks. A project page preselects the provider and its default voice, and prefills the Generate card, when it opens; one already open keeps those values until it is opened again.

## Under the hood

The settings are keys in `data\config.json`, the engine's own configuration file, one key per field. A save sends only the fields that changed, and nothing is written unless every one of them is valid. The Whisper model is resolved when a transcription is requested (the recommended default is chosen when the job runs); the narration provider and voice are resolved when a render or re-voice is requested; the transition, pause and watermark are the defaults a generate request may override per job. The Ollama URL has no field here: it is `ollama_url` in the same file; see [Ollama](../ai/ollama.md).

Some keys can be set from the environment when the server starts, which takes precedence over the file's value for that run (and a later save writes the value in force into the file): `MEDIA_STUDIO_EDGE_TTS_VOICE`, `MEDIA_STUDIO_MUSIC_VOLUME`, `MEDIA_STUDIO_TRANSITION_PAUSE`, `MEDIA_STUDIO_PORT`, and the three Ollama variables. A save is recorded in the audit log as `settings.update` with the names of the changed keys, never their values.

## See also

- [Narration engines](../ai/narration-engines.md)
- [Transcription](../ai/transcription.md)
- [Ollama](../ai/ollama.md)
- [Generate a video](../guides/generate-video.md): where the defaults are used
