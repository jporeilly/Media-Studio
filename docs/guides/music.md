# The Music lane

The Timeline's fourth lane holds music from a library the whole studio shares: clips placed, trimmed, levelled and faded under the narration, and mixed in at render.

## What you see

The **Music** lane's header has three controls: **+** ("Open the music library"), an eye ("Hear the voice alone", then "Hear the music again"), and a lock. Clicking the name **Music** selects the channel, locking the other lanes.

**Music library** is a dialog: **Upload a track**, a status line while a file uploads, and a table with **Name**, **Length**, **Size**, **Channels** (mono or stereo), **Added by** and **Added**. Each row has **Add at playhead** and a bin, **Delete *name***. A note under the table says a clip is placed at the playhead and plays from the top of the track, for as long as the track lasts or until the end of the video, whichever comes first.

A clip on the lane is a dark block with the file's name, the waveform of the stretch it plays, and a triangle at each end for its fades. Selecting a clip opens a row under the strip: the file, "at *time* · *in*–*out* of the file · *length* s", a **Level** slider (0 to 100 %, with a notch at the studio's default), **Fade in** and **Fade out** in seconds, and **Remove clip**.

A clip whose file has left the library is hatched, its name suffixed "missing", and a banner above the strip names the files.

## What to do

**Upload.** Press **Upload a track** and choose an mp3, wav, m4a, aac, ogg or flac file, mono or stereo, up to 100 MB. It is decoded once to prove it is audio and to measure it, which takes a moment for a long file. A name already in the library, compared without regard to case, is refused: rename the file, or delete the old one.

**Add a clip.** Move the playhead to where the music should start and press **Add at playhead**. The clip starts there, plays from the top of the file for as long as the file or the output has room for, at the studio's **Music volume**, with a one-second fade in and a two-second fade out cut down to fit a short clip. There is no looping: to hear a bed twice, add it twice. A lane holds up to 200 clips.

**Move.** Drag the clip. It snaps to the playhead, the picture's joins, the ends and the other clips, by either of its edges; hold Alt to place it freely (Ctrl does the same once the drag has begun; held as you press, Ctrl makes the drag a range selection instead), or switch the magnet off. It cannot go before 0 or past the end of the audition.

**Trim.** Drag either end. The left edge moves the clip and its start in the file together, so the audio stays where it is under the pointer; the right edge moves only where the clip stops. Neither can pass the length of the file, a clip is never shorter than a tenth of a second, and the fades are re-fitted to what is left.

**Select, level and fade.** Clicking a clip only selects it; the playhead stays where it is, unlike a click on a sentence block, so you can set a level or type a fade while the audition plays. The level is linear and is exactly what the render applies; the two fades together are never longer than the clip.

**Remove.** **Remove clip**, or **Delete**, **Backspace**, **Ctrl+Delete** or **Ctrl+X** while the clip is selected; **Escape** gives the clip up. Overlapping clips are allowed and simply add.

**Hear the voice alone.** The eye silences the music in the audition only, and takes effect mid-play. The render always mixes the music in.

**Cut with the picture.** The music rides the picture: a cut ripples the clips only when the Video lane is cut too and the Music lane is unlocked. A clip after the cut moves earlier, a clip across it is split in two, a clip inside it goes; a cut that would split, shorten or remove a clip whose file is missing is refused instead (see **A missing file**). With Video locked, or Music locked, the clips stay where they are; with Music the only unlocked lane the scissors is disabled and says why. **S** splits the clips under the playhead wherever the lane is unlocked and moves nothing.

**Delete a file.** The bin in the library removes the file for everyone after a confirmation. It is refused while any project's timeline still has a clip on the file, and the refusal names those projects.

**A missing file.** When a file a project names is no longer in `assets\music\` (removed outside the app, or the project restored or copied without it; the app itself refuses to delete a file a project uses), that project's clips are marked missing: hatched on the lane, silent in the audition, and the render refuses on them rather than quietly producing a video without them. A missing clip can be moved, its level and fades set, and it can be removed. It can also be shrunk, by trimming it shorter from either end or splitting it, but never grown past the slice it had, because the file's length is no longer known. A cut of the picture across one, with the Music lane unlocked, is refused before anything is saved, because it could not be undone while the file is gone: lock the Music lane to cut the picture alone, or remove the missing clip first. Undo is refused when it would lengthen such a clip: the undo of a trim or a split of it, and the undo of its removal. The refused step is taken off the list and the message says so; the steps before it are often refused for the same reason. To undo them, put the file back in the library under the same name first. Redo likewise: a redo the server refuses is taken off the redo list. The banner above the strip names the files and offers two ways out. Its button, **Remove the missing clip** (**Remove the N missing clips** when there are several), takes out every clip whose file is missing in one save, while Delete on one of them takes just that one. Or put the file back in the library under the same name, and the clips play again as they now stand: a trim or a split made meanwhile stays, and their ends can then be dragged out again.

## Under the hood

The library is one directory, `assets\music\`, with an `index.json` beside the files recording each file's size, length, sample rate, channel count, upload time and uploader, and a `<name>.peaks.json` of the waveform the lane draws. It is studio-wide: every signed-in user may list, upload and delete, and the audit log (`music.upload`, `music.delete`) says who did what. A file is decoded once, at upload, through the app's own ffmpeg; nothing probes it again. Names are unique without regard to case and looked up exactly; a file with more than two channels is refused, because the render's up-mix is built for mono and stereo only.

A clip is stored on the project's edit as `{id, file, at, in, out, gain, fade_in, fade_out}`: `at` in the output's seconds (the picture's axis, which is why a cut of the picture moves it), `in` and `out` the slice of the file in its own seconds, `gain` a linear factor from 0 to 1, the fades in seconds. The server checks every clip against the library's recorded lengths: at most 200 clips, at least 0.1 s each, the slice inside the file, the fades no longer than the clip. Every gesture sends the whole clip list in one request on release, and undo covers it like any other edit, except an undo that would lengthen a missing clip (above). A clip whose file has gone is kept (you may keep what you have; you may not add what is not there): it may be moved, levelled, faded, shrunk or split, its slice never grows past what it was, and the edit reports it `missing`.

The render lays the clips under the finished narration in a second ffmpeg pass: each clip's slice, up-mixed to stereo at unity, its level, a linear fade in and out, then its placement, all summed without normalising and mixed with the voice under "duration=first", so the voice's level is untouched and there is no ducking. The audition decodes each file once in the browser (about 100 MB of memory for a five-minute stereo track) on the first Play, never on opening the tab, and skips a missing file.

## See also

- [The Timeline](timeline.md): the lanes, the locks, cutting and undo
- [Re-voicing a video](re-voice.md): the render that mixes the music
- [Studio settings](../admin/studio-settings.md): the Music volume a new clip starts at
- [Limits](../reference/limits.md)
