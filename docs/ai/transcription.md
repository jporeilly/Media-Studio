# Transcription

Transcribing a video runs faster-whisper over its audio: on an NVIDIA GPU when the CUDA libraries are installed, on the CPU otherwise, with the model chosen in the studio settings.

## The models

| Model | Download | Description |
| --- | --- | --- |
| `tiny` | ~75 MB | Fastest, lower accuracy |
| `base` | ~145 MB | Fast, good for clear speech |
| `small` | ~484 MB | Balanced speed/accuracy |
| `medium` | ~1.5 GB | High accuracy, slower |
| `large-v3-turbo` | ~1.6 GB | Fast and accurate; best on a GPU |
| `distil-large-v3` | ~1.5 GB | Fast large model, English only |
| `large-v3` | ~3.1 GB | Best accuracy, slowest |

The **Whisper model** in the Studio card is one of these, or the **Recommended default**: `large-v3-turbo` when a GPU is present, `medium` otherwise, decided when the job runs. A model is downloaded on its first use and kept. The same setting drives the Whisper subtitle pass of a deck render.

## GPU and CPU

On a GPU the model runs in float16; on the CPU in int8, roughly nine times slower. The GPU needs the CUDA 12 runtime, cuBLAS and cuDNN 9 as Python wheels, about 1 GB, listed in `requirements-gpu.txt`:

```powershell
& "C:\Media-Studio-Enterprise\python\python.exe" -m pip install -r "C:\Media-Studio-Enterprise\app\requirements-gpu.txt"
```

From a checkout: `venv\Scripts\pip install -r requirements-gpu.txt`. Then **Restart backend** on the Settings page. The desktop installer does not bundle these libraries.

Without them the app still transcribes: a GPU whose runtime is missing fails only when inference starts, so the engine catches that, logs "GPU transcription failed (…) — retrying on CPU", reloads the model on the CPU and does not try the GPU again until the backend restarts. The **Details** card's **Transcribed on** says which device did the work.

## What the Details card shows

After a transcription the project's **Details** card shows **Language** (the code Whisper detected), **Duration** (of the audio), and **Transcribed on**: CUDA (the GPU) or CPU.

## Under the hood

The job extracts the audio with ffmpeg to `data\projects\<id>\audio.wav` (16 kHz, mono, 16-bit) and hands it to faster-whisper with word timestamps and voice-activity filtering (silences of 500 ms or more are skipped). Segments with no words are dropped. The transcript is stored on the project as one entry per segment with `start`, `end` (to three decimals) and `text`, beside the detected language, the duration, the model and the device. **Transcribe again** on the Transcript card runs the same job with the same model rule: it re-extracts `audio.wav`, which is also the file the Timeline's waveform and the edit are measured against, and replaces the whole transcript, dropping every sentence's adjustments, while the edit's cuts, markers and music clips stay where they are in time. The engine adds the CUDA wheels' DLL directories to the process itself, so nothing needs to be on the system PATH.

## See also

- [Transcribing and the transcript](../guides/transcript.md)
- [Studio settings](../admin/studio-settings.md)
- [Troubleshooting](../admin/troubleshooting.md)
