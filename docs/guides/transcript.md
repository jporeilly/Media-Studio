# Transcribing and the transcript

The **Transcript** card of a video project transcribes its audio with Whisper, then is where you fix the words, adjust how each sentence is spoken, and download the result.

## What you see

The **Details** card of a video project shows **Type**, **Imported**, and, once transcribed, **Language** (detected), **Duration** and **Transcribed on** (CUDA for the GPU, or CPU).

Before a transcript, the **Transcript** card says "No transcript yet. Transcribe the audio to get an editable transcript." with one button, **Transcribe audio**. During the transcription it shows the job's progress.

Once there is a transcript, the card header offers **Download** as **SRT**, **TXT** or **JSON**, a select for which timings the file carries (**as the re-voice will play it** or **as spoken in the source**), and **Transcribe again**. Two tabs follow: **List** (with the sentence count) and **Timeline**, which is the tab a video opens on.

**The List** is one row per sentence: its timecode (and "aimed at …" when it has been nudged), the text in a box, and four controls, **Offset (s)**, **Speed** (placeholder "auto"), **Voice** and **Mute**, plus **Play**, which reads **Speaking…** while the voice service answers and **Stop** while it plays. At the foot: **Save transcript**. Three short paragraphs above the rows explain the controls.

**The Timeline** is the filmstrip, the waveform and one block per sentence on one time scale, with the edit's lanes and a transport; [The Timeline](timeline.md) is its full guide. Both tabs stay loaded, so switching back to the Timeline keeps the clips it has fetched, and the playhead and its clock show where you left them the moment the tab is back.

## What to do

**Transcribe.** Press **Transcribe audio**. The job extracts the audio, loads the Whisper model chosen in the studio settings and transcribes. A long recording takes minutes on the CPU.

**Transcribe again.** Press **Transcribe again** in the card header, for a better Whisper model or to bring back a lost `audio.wav`. A dialog says what a new transcription loses and keeps; confirm with its own **Transcribe again**. It loses the transcript's words, with every correction made to them, every sentence's offset, mute, voice and speed (the dialog counts the sentences that have some; none carry over, even to a sentence heard at the same moment), and the Timeline's undo history. It keeps the Timeline's cuts, markers and music clips, which are placed by time rather than by sentence, and the last re-voiced video and its tracks until the next re-voice. The button waits while any job holds the project. When the new transcript dropped adjustments, the card then keeps a line that counts them ("Transcribed 42 segments; the adjustments on 3 sentences were dropped"), with a cross to dismiss it.

**Fix the words.** Edit any sentence's text in the List and press **Save transcript**. Saving the words never moves a sentence and keeps every adjustment.

**Nudge a sentence.** Type an **Offset (s)** (positive is later, negative earlier, ±300 at most) and leave the box or press Enter; it saves at once. A sentence can always be pushed later; it can only be pulled earlier as far as the sentence before it finishes speaking. 0 or a blank box clears the nudge.

**Mute a sentence.** Tick **Mute** to leave it out of the new narration. It saves at once.

**Give a sentence its own voice or speed.** Pick a **Voice** from the provider's list (it saves when picked) or type a voice id (it saves when you leave the box). Type a **Speed** between 0.5 and 2: the sentence is then spoken at exactly that rate, and nothing speeds it up to fit the gap after it. An empty speed box means the re-voice's own speed, which the render may raise a little to fit.

**Hear a sentence.** **Play** speaks it as the re-voice will, using the Re-voice card's provider, voice and speed under the sentence's own overrides. The first press waits on the voice service, a second or two, and longer for the first press after the server starts; every press after that is instant. Only one sentence plays at a time.

**Download.** Choose the timings, then **SRT**, **TXT** or **JSON**. "As the re-voice will play it" is each spoken sentence at the moment it is aimed at, cuts applied and muted sentences left out; "as spoken in the source" is every sentence at the moment it was spoken in the recording, muted ones marked `[muted]` in the SRT and TXT files and `"muted": true` in the JSON.

## Under the hood

Transcribing is a job of kind `transcribe`. The audio is extracted with ffmpeg to `data\projects\<id>\audio.wav` (16 kHz mono) and transcribed with faster-whisper; the model is the studio's **Whisper model**, and the recommended default is large-v3-turbo on a GPU or medium on the CPU. The transcript is stored on the project record as `start`, `end` and `text` per sentence, to three decimals, with the detected language, the duration, the model and the device; see [Transcription](../ai/transcription.md) for the GPU and the fallback. **Transcribe again** is the same job with the same Whisper model rule: it extracts `audio.wav` again and replaces the whole transcript, and every adjustment goes with the sentences it belonged to, since those no longer exist; nothing is carried across by position or by timing. The job's closing line counts the sentences whose adjustments were dropped, and the card keeps it when there were any. The edit's ranges and markers are in the source's seconds and its music clips in the output's, so they stay where they were in time and the new sentences are projected through them as the old ones were. The record also stamps when the transcript was made, and the Transcript card starts afresh with each new one, so nothing held for the old sentences by position can land on a new one.

Each adjustment is stored on the sentence itself as up to five keys (`offset`, `muted`, `voice`, `provider`, `speed`) and is one request the moment it is committed. A voice is checked against the provider it belongs to, so an id from the other provider is refused now rather than dropped at render time. **Save transcript** sends the words only; the server carries the adjustments across by position and window, and reports how many sentences lost theirs when the saved list no longer holds them; the List says so rather than losing them quietly. While a job holds the project, an adjustment or a **Save transcript** is refused with "A job is running for this project…" (the button is not disabled; the server answers); Play and Download are reads and work while other jobs run (the download buttons wait only for a transcription, which is about to replace the transcript).

**Play** fetches a clip synthesised from the stored text, keyed on text, voice and speed in the same cache the render uses, so a sentence you have auditioned is not synthesised again by a re-voice that speaks it at the same speed. The render may raise a sentence's speed by up to 30 % to fit its window, and a clip at a new speed is synthesised afresh. The wait is bounded at 30 seconds; a provider that answers nothing is reported, not a broken player. Kokoro's model is never downloaded by a Play press: with the model absent the answer is "Kokoro's model is not downloaded yet…" and a hint to re-voice once.

The downloads are named `<project name>-narration.srt`, `.txt` or `.json`. The timeline view's timings are the audition plan's own: projected through the edit, muted, wordless and dropped sentences left out, each sentence at its pin. A pin is a floor, so a sentence whose clip runs long lands later than the file says, and the TXT file says so in its two-line header, the JSON in its `note` field. The SRT gives a cue at least half a second and skips a sentence with no words.

The transcription, the text saves and the timing changes are recorded in the audit log (`project.transcribe`, `project.transcript_edit`, `project.transcript_timing`) with the job id, the sentence count, the sentence indices and the field names; never the words or the values.

## See also

- [The Timeline](timeline.md): the filmstrip, the audition and the edit
- [Re-voicing a video](re-voice.md): the render that uses these adjustments
- [Transcription](../ai/transcription.md): Whisper, the models, GPU and CPU
