# Narration engines

Two text-to-speech providers narrate everything the app renders and previews: Edge TTS, Microsoft's online voices, and Kokoro, a model that runs on the machine.

The studio picks a default; every project page, slide and sentence can choose otherwise.

## Edge TTS

Microsoft's online voices: free, no key, and an internet connection for every synthesis. Voice ids look like `en-US-AriaNeural`, and the list (several hundred voices across the locales) is fetched from the service and cached for a week; offline, the last fetched list is shown with a notice, and with nothing cached the list is empty and the card says "Could not fetch the Edge TTS voices (offline?)". A synthesis is given up after 120 seconds.

## Kokoro

A local model, run through kokoro-onnx, so it needs no network once its files are on disk. The model and its voices, `kokoro-v1.0.onnx` and `voices-v1.0.bin`, about 340 MB together, download once from the kokoro-onnx release into `assets\models\kokoro\` (or the directory named by `kokoro_model_dir` in `data\config.json`) on the first Kokoro narration: a render or a re-voice. A voice list and the List's **Play** never start that download; until the model is there the list is a built-in one with the notice "Kokoro's model is not downloaded yet…", and Play answers the same. The model speaks at 24 kHz, mono.

Voice ids look like `af_heart`: the first letter is the accent (`a` American English, `b` British English, `e` Spanish, `f` French, `h` Hindi, `i` Italian, `j` Japanese, `p` Portuguese, `z` Chinese) and the second the gender, `f` or `m`. The built-in list holds af_heart, af_bella, af_sarah, af_nicole, af_sky, am_michael, am_adam, am_echo, bf_emma, bf_isabella, bm_george and bm_lewis; once the model is on disk the list is the model's own. The **Kokoro language** setting (English (US), English (UK), French, Italian, Japanese, Chinese, Spanish, Hindi, Portuguese (BR)) is the language Kokoro reads the text in.

## Speed

Every speed box (the Generate card, the Re-voice card, a sentence's own) offers 0.5 to 2, and 1 is the natural rate. The server refuses a speed outside that range, for a deck render, a re-voice and a sentence's own alike. Edge turns it into a rate percentage; Kokoro takes it as a factor. In a re-voice the job's speed is a floor: a sentence is never spoken slower than it, may be raised by up to 30 % to fit its window, and a sentence with its own speed is spoken at exactly that rate.

## Defaults and overrides

The studio settings hold the provider, the **Default Edge voice** (`en-US-AriaNeural` on a fresh install), the **Kokoro voice** (`af_heart`) and the Kokoro language. A project page preselects the provider and that provider's default voice; the Generate and Re-voice cards can change both per job.

A slide can carry a **Voice override** and a **Pause after slide (s)**; a transcript sentence can carry its own voice, speed, offset and mute. A voice is checked against the provider it belongs to when it is saved: an Edge id under Kokoro, or a Kokoro id under Edge, is refused with a message naming the shape expected. A voice stored earlier under the other provider is not an error at render time: it falls back silently to the job's voice, so a switch of provider never fails a render.

## Under the hood

The provider's id is `edge_tts` or `kokoro`; anything else is refused. Every synthesised clip is cached in `data\cache\` under a key of the text, the voice and the speed (Kokoro folds its language into the key), so a sentence heard in the List or the Timeline is the very file the render muxes, and a re-render with unchanged notes synthesises nothing. The cache is never swept. Each provider has an onset profile that trims the silence at the start of a clip and lifts its first few milliseconds to the body's level (Edge's clips open with digital silence and a short ramp, Kokoro's with a quiet attack), so sentences do not sound as if they fade in. A translation pairs its language with a voice by the locale's language part: `fr` matches `fr-FR-DeniseNeural`, and Kokoro's `f` prefix.

## See also

- [Studio settings](../admin/studio-settings.md)
- [Generate a video](../guides/generate-video.md) and [Re-voicing a video](../guides/re-voice.md)
- [Troubleshooting](../admin/troubleshooting.md): offline Edge, the Kokoro download
