# Ollama

Two features need a local Ollama server: the translation of a re-voice, and the whole AI assistant on the Slides card. Both use the same model and the same URL.

## What needs it

- **Re-voice translation.** A re-voice with a **Language** other than the recording's own (or English) translates the transcript through Ollama first.
- **The AI assistant.** Generate notes, Enhance all, QA review and Fix, Tone, Translate, Pacing with the model, Analyze's suggestions and the Q&A document. Without Ollama the bar shows the actions disabled; Analyze still runs its rules, and **Apply the rules** in Pacing needs no model.

## The settings

**Ollama model** is a field of the Studio card, `gemma4:12b` on a fresh install; its hint mentions the translation only, but it is the assistant's model too. An install that has saved its settings keeps the `ollama_model` in `data\config.json` after an upgrade, so an older install stays on its model until it is changed here in Settings › Studio. The server must have the model pulled: the assistant's status line says "*model* is not pulled on the Ollama server (ollama pull *model*)" when it is not. Every request sends `"think": false`, so a model that thinks by default (gemma4, qwen3) answers without thinking first; one rewritten sentence on gemma4 took 16 s with thinking and 1.4 s without. Models that cannot think ignore the flag.

The **URL** has no field: it is `ollama_url` in `data\config.json`, `http://localhost:11434` by default. Edit the file and restart the backend to change it.

Three environment variables override those two values when the server starts. Set them in the environment, or in a `.env` file next to `main.py`, which the app reads at start:

| Variable | Effect |
| --- | --- |
| `MEDIA_STUDIO_OLLAMA_URL` | Overrides `ollama_url` |
| `MEDIA_STUDIO_OLLAMA_MODEL` | Overrides `ollama_model`; the Studio card then shows it |
| `MEDIA_STUDIO_OLLAMA_ENABLED` | `1`, `true` or `yes` sets `ollama_enabled` in the configuration; nothing in this edition reads that flag, so it changes no behaviour |

`OLLAMA_URL` and `OLLAMA_MODEL` in the environment or in `.env` have no effect: the configuration always carries its own values, and those win. A value that came from the environment is written into `config.json` the next time the settings are saved.

`ollama_system_prompt` in `config.json` replaces the system prompt sent with Generate notes, Enhance all, the per-slide AI Enhance and a QA Fix; the tone, translation and pacing prompts carry their own instructions.

## Vision models

The assistant shows the model a slide's image only when the model's family is one that sees images (gemma4, gemma3, llava, moondream, llama3.2-vision, qwen2.5vl, qwen2-vl, minicpm-v, llava-llama3 or bakllava; a tag such as `gemma3:12b-it-qat` counts by its family) and the project's previews are real renders from PowerPoint or a PDF. The status line reads "*model* · vision" when both hold and "*model* · vision off: *reason*" otherwise.

## The status line

| Text | Meaning |
| --- | --- |
| "Checking Ollama…" | The status request is in flight |
| "Ollama unreachable at *url* — AI actions disabled" | The server did not answer `/api/tags` within 3 seconds |
| "*model* is not pulled on the Ollama server (ollama pull *model*) — AI actions will fail until it is" | The server answers but does not list the model |
| "*model* · vision" | Ready, and images will be shown to the model |
| "*model* · vision off: *reason*" | Ready, text only |
| "Could not check Ollama: *error* — AI actions disabled" | The status request itself failed |

## Under the hood

The assistant probes the server (3 seconds) before it creates a job and refuses with a 503 when it is unreachable; a model call that fails is a 502 for a synchronous action and a counted failure inside a job, and a loop stops early only when the server no longer answers a probe. Per-slide calls wait up to 120 seconds, a QA pass up to 180, the Q&A document 60 and Analyze's suggestions 30. Each call is one `/api/generate` request with the prompt, the system prompt and, for vision, the slide's PNG encoded in the body.

The translation of a re-voice is one request per text, given 60 seconds; a failed request (the server down, a timeout) returns the original text and logs a warning in `data\logs\app.log`, so the re-voice proceeds untranslated rather than failing. The assistant's own translation and tone actions report a failure instead.

The environment checker reports Ollama as a warning when nothing listens on port 11434.

## See also

- [The AI assistant](../guides/ai-assistant.md)
- [Re-voicing a video](../guides/re-voice.md)
- [Studio settings](../admin/studio-settings.md)
