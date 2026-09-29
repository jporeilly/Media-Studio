# Re-voicing a video

The **Re-voice** card renders a transcribed video with a new narration, optionally translated, over the original picture, with the Timeline's cuts, music and chapters applied.

## What you see

The card appears under the Transcript card once the video has a transcript. Its fields: **Narration provider**, **Voice**, **Speed** (0.5 to 2), and **Language**: **Keep original language**, or one of Spanish, French, German, Italian, Portuguese, Dutch, Polish, Russian, Japanese, Korean, Chinese (Simplified), Arabic, Hindi and English.

The button reads **Re-voice** the first time and **Re-voice again** after that. When the timeline's edit removes anything, or has music on its lane, it reads **Render**, and a line under it says what the render will do: "Cuts 2 ranges of the picture (12.4 s removed), mixes 1 music clip under the narration and re-voices — about 25 s, plus the music mix and any sentences the audition has not fetched yet." When a music clip's file has left the library the line also says the render will refuse until the clip is removed or the file uploaded again.

While a re-voice runs, the card shows its progress and a **Cancel** button. With a language selected, a notice explains that a translated re-voice translates the whole transcript as one block, so the per-sentence adjustments (offset, mute, voice and speed) do not apply to it. After a re-voice in which sentences failed, a notice counts them. Then the player, **Download re-voiced video**, and **Separate tracks**: **Picture**, **Original audio**, and **New narration** once a re-voice has been made.

## What to do

1. Transcribe the video and, in the List or the Timeline, adjust anything that lands badly; see [Transcribing and the transcript](transcript.md) and [The Timeline](timeline.md).
2. Choose the provider, the voice and the speed. Choose a **Language** only to translate.
3. Press **Re-voice** (or **Render**). The card shows the job's progress: cutting the picture when there is a cut, then re-voicing, then mixing the music, then writing the chapters. **Cancel**, under the progress, stops it; see below for what a cancel leaves.
4. Play the result, download it, or download the three tracks to line them up in another editor; they share the same start.

## Under the hood

The card's **Re-voice** and **Render** are the same job, `revoice`, with the edit applied. It is refused, before any work is done, when the project is not a video, has no transcript, has every sentence muted, has an edit that removes every spoken sentence, or has a music clip whose file is not in the library ("music file '*name*' is missing — remove the clip or upload the file again"). It is refused with a 409 while another job holds the project.

**Translation.** With a language chosen, the whole transcript is joined into one text and translated once through the local Ollama model (the studio's **Ollama model**, at the configured URL), unless the target's language is English or the same as the recording's, in which case nothing is translated and the adjustments still apply. The translated text no longer matches the transcript's sentences, so the engine splits it into sentences and spreads them across the span of the transcript in proportion to their length; that is why offsets, mutes, per-sentence voices and speeds do not apply to a translated re-voice. If Ollama cannot be reached, the translation returns the original text with a warning in `data\logs\app.log` and the re-voice proceeds untranslated.

**Synthesis.** Each sentence is synthesised on its own and pinned to the moment it was spoken (plus its offset): the gap after it becomes silence again, which is how the new narration keeps step with the picture. A sentence is never spoken slower than the job's speed. When the original speaker was faster than the voice, the sentence's speed is raised by up to 30 % to fit the window before the next sentence. A clip that still overruns its window by more than 15 % is tempo-adjusted to fit, up to a factor of 2; beyond that it is let through. A sentence with its own **Speed** is spoken at exactly that rate and never squeezed. A sentence pinned at or past the end of the picture is dropped, and a sentence whose synthesis fails is silent; both are counted and reported on the card. Clips come from the same cache the List's Play and the Timeline's audition fill, so an auditioned sentence is not synthesised twice.

**The picture.** When the edit's video list removes anything, the kept ranges are first re-encoded into a cut picture (libx264, ultrafast, the default preset's bitrate) and the narration is muxed onto that; otherwise the original frames are copied untouched. The narration is padded with silence to the picture's length, so the video's tail is kept.

**The music, then the chapters.** Music clips are laid under the narration by a second ffmpeg pass over the finished file: the picture copied, the audio re-encoded once as 192 kbit/s AAC, the voice's level untouched and never ducked. Then the drawn markers become chapters by one stream-copy remux; a remux that fails is logged and never fails the job. See [The Music lane](music.md) and [Markers and chapters](markers-and-chapters.md).

**The files.** The output is `data\projects\<id>\<source stem>_revoiced.mp4`, overwritten by every run; the narration on its own is `<source stem>_revoiced_narration.mp3`, voice only and ending where the last sentence does. The record stores the file's name, when it was made, the language, the failed-sentence count and how many music clips it carries. **Picture** downloads the source file in its own container, **Original audio** the extracted `audio.wav`.

**Cancel.** The card's **Cancel** is offered to the user who started the re-voice and to an administrator; it reads **Cancelling…** until the job stops. The job looks at it before the translation's model call and after it, while the picture is cut, before every sentence it synthesises, once more before the new narration is laid on the picture (the mux), and before and during the music mix. The mux and the chapters are short and are not interrupted: a cancel that arrives once the mux has started, on a render with no music, or once the music is mixed, is too late, and the render completes with its chapters. What a cancel leaves, in the line the card then shows:

- Stopped before the mux (during the translation, the cut or the synthesis): "Re-voice cancelled before the new video was written; the project is as it was." The previous re-voice, if there was one, is still the video and the narration track the card offers, and the job's scratch files are removed.
- Stopped at the music, once the new narration is on the picture (a cancel that lands during the mux takes effect here): "Re-voice cancelled while the music was being mixed: the re-voiced video has the new narration but not the music. Re-voice again for the full render." ("but not the music or the chapters" when the render would have written chapters). The new voice-only file is on disk, the record describes exactly that, and the partial mix is removed.

The job is recorded in the audit log as `project.revoice` with the job id, the provider, the voice and the language.

## See also

- [The Timeline](timeline.md): the cuts, the music and the markers the render applies
- [Narration engines](../ai/narration-engines.md): Edge TTS, Kokoro and their speed
- [Ollama](../ai/ollama.md): the model that translates
