# The Timeline

The Timeline is where a video project is edited: you hear the new narration
against the picture, cut the parts you do not want, re-time the sentences that
land badly, and lay music under the whole thing — all before a render is ever
started. Nothing here runs a job. Play fetches each sentence exactly as the
re-voice will speak it, so what you hear is what Render will make.

## The four lanes

Under the ruler there are four lanes, all on one time scale, so everything
lines up vertically with the moment it happens at.

| Lane | What it shows |
| --- | --- |
| **Video** | The picture, as a filmstrip. Its own kept ranges are the picture's edit. |
| **Audio · original** | The source recording's waveform. Reference only — it follows the Video lane and has no edit of its own. |
| **Narration** | One block per sentence of the transcript, where that sentence is aimed. |
| **Music** | The clips you have placed, each showing the waveform of the stretch of the file it plays. |

A block sits where its sentence is **aimed**, which is not always where it will
land. The pin is a floor, not an appointment: a sentence that runs long is
first sped up a little to fit the gap after it, and is only marked as
overrunning when it really will push the next one late. Select a block and the
line under the strip says where it was spoken, where it is aimed, where it
lands, and whether it was sped up.

The ruler shows the OUTPUT's time — the edit with the cuts closed up — so a
cut shortens the strip, and the picture skips at a join because that is the
edit.

## Playing the audition

**Space** plays and pauses. **,** and **.** step one frame back and forward (a
frame is a thirtieth of a second here: the timeline never probes the source for
its real frame rate, because it is built to answer instantly and from what the
project already knows). **Ctrl+Home**
and **Ctrl+End** jump to the two ends. Dragging the playhead's head scrubs, and
a click on the ruler seeks.

Each sentence is spoken once and kept, so the first play of a long project
takes a while and every play after it is instant. **Prepare all N sentences**
in the transport row does the same work up front. Music is decoded when you
press Play, once per file — a few seconds and about 100 MB of memory for a
five-minute track, so a long library is expensive to audition.

## Selecting a range

A cut acts on a selection, and there are three ways to make one.

- Drag the **green** handle (the start) or the **red** handle (the end) in the
  ruler.
- **Ctrl+drag** anywhere on the strip.
- Click a **piece** of a lane that has been split — see below — and the piece
  becomes the selection.

**Shift+,** and **Shift+.** grow the selection a frame at a time;
**Ctrl+Shift+Home** and **Ctrl+Shift+End** stretch it to the start or the end
of the picture. **Escape** gives up whatever is selected, in that order — the
music clip first, then the sentence blocks, then the range — and a double-click
on the playhead's head clears the range outright. A selection lives on the
picture: nothing past the end of the video can be selected, and so nothing
there can be cut.

## Cutting

The scissors in the toolbar — or **Delete**, **Backspace**, **Ctrl+Delete** or
**Ctrl+X** — removes the selection from the unlocked lanes and closes the gap.
This timeline has no empty space to leave behind, so a plain Delete ripples
like Camtasia's Ctrl+Delete rather than leaving a hole.

The cut is refused, rather than half-done, when it would leave the picture or
the narration with nothing at all: keep at least one range. The Music lane has
no such floor — a cut across every clip removes them all, and Undo brings them
back.

A sentence that is spoken after the end of the cut picture is **dropped** —
the re-voice leaves it out entirely. The strip says so above the ruler and on
the block itself, and names the two ways out: unlock Narration and cut it too,
or drag the sentence earlier.

## Undo and redo

**Ctrl+Z** undoes the last edit — a cut, a split, a drag, a nudge, a reset, or
anything done to the Music lane — and **Ctrl+Y** (or **Ctrl+Shift+Z**) redoes
it. The toolbar has both buttons.

Undo is for **this visit**. The app stores the current cut and the current
timing, not their history, so the stack is emptied when the project is left or
the page is reloaded, and it remembers the last fifty edits while you are
there.

## Locks and channels

Every lane but Audio has a padlock in its header. A locked lane is left
**exactly as it is** by a cut and by a split: cutting the picture with
Narration locked moves no pin, and every later sentence then lands earlier
against the picture by the length that was removed.

Clicking a lane's **name** selects that channel, which is the same thing said
faster: it locks the other lanes. Clicking the selected name again unlocks
everything. The padlocks still toggle one lane at a time, so any combination is
reachable. The selection band is drawn only over the lanes a cut would really
change, and the scissors' tooltip always says what the current combination
means.

The locks are yours, not the project's: they are kept per project in this
browser and survive a reload, and they are never saved on the record.

## Splitting, and what a piece is

**S** puts a boundary in each unlocked lane at the playhead;
**Ctrl+Shift+S** does it on every lane, locks and all. A split removes nothing
— the output is unchanged — and that is the point: what it makes is **pieces**.

A piece is the stretch of a lane between two boundaries. Once a lane has more
than one piece, clicking one selects it, ready for the scissors. Until then a
click on a lane seeks, as a click on the ruler does, and a click on a locked
lane always seeks.

A split on a boundary that is already there changes nothing and is not saved.

## Re-timing a sentence

**Drag** a sentence block along the Narration lane to aim it somewhere else.
It snaps to the playhead, to the picture's joins, to the start and the end, to
the other sentences' pins and landed ends, and to **its own spoken moment** —
so putting it back where it was said is a snap rather than a hunt. Hold
**Ctrl** to drag freely. While a sentence is aimed away from where it was
spoken, a ghost on the lane shows where that was.

**[** and **]** nudge the selected blocks by 0.05 s, or by 0.25 s with Shift
held. **Reset timing** in the toolbar puts them back exactly where they were
spoken.

Clicking a block selects the sentence and moves the playhead to it; Ctrl+click
adds a block to the selection or takes it out again, Shift+click takes a run of them, and a drag across
the empty part of the lane is a marquee. A nudge and a Reset act on the
selected blocks, or on the one chosen sentence when nothing is selected.

## The Music lane

Music is the fourth lane, and it is not a track: it holds **clips** placed on
the output's own axis, not a list of kept ranges.

### The library

The **+** in the Music header opens the library. It is shared by the whole
studio — everyone sees the same files — and a clip refers to a file **by
name**, which is why:

- an upload of a name that is already there is refused rather than replacing
  it, since a replacement would change every project that uses it;
- a file cannot be deleted while any project's timeline still has a clip on it,
  and the refusal names those projects.

mp3, wav, m4a, aac, ogg and flac are accepted, mono or stereo only, up to
100 MB. Each file is decoded once when it is uploaded, to prove it is audio and
to measure it; the server never probes it again. (Your browser decodes it for
the audition, as described above.)

### Adding a clip

**Add at playhead** in the library puts the track on the lane at the playhead,
from the top of the file, running as long as the file or as long as the output
has room for — whichever is shorter. It arrives at the studio's **Music
volume** (Settings) with a one-second fade in and a two-second fade out, cut
down to fit a short clip. There is no looping: to hear a bed twice, add it
twice.

A lane can hold up to 200 clips, and a clip is never shorter than a tenth of a
second.

### Moving, trimming and deleting

Drag a clip along the lane to move it — only its place changes, never the part
of the file it plays. It snaps to the playhead, the picture's joins, the ends
and the other clips, by either of its own edges; Ctrl drags freely. A clip
cannot be dragged before zero or past the end of the audition.

Drag either **end** of a clip to trim it. The left edge moves the clip and its
start in the file together, so the audio stays where it is under the pointer;
the right edge moves only where the clip stops. Neither can go past the length
of the file, and the fades are re-fitted to whatever length is left.

**Delete** (or Backspace, Ctrl+Delete, Ctrl+X) removes the selected clip, and
so does **Remove clip** in the row under the strip. While a clip is selected
those keys belong to it rather than to the cut; **Escape** gives the clip up.

Clicking a clip **only selects it**. The playhead stays exactly where it is —
deliberately unlike a sentence block, whose click also seeks — so a level can
be set, and a fade typed, while the audition is playing.

### Level and fades

Selecting a clip opens a row under the strip with its file, its place, its
length, a **Level** slider and a **Fade in** and **Fade out** in seconds. The
level is linear and is exactly what the render applies; the slider's notch
marks the studio's default. The fades are linear ramps drawn as triangles on
the clip, and the two together are never longer than the clip — type more than
that and they are cut down to fit.

### The music rides the picture

A cut ripples the clips **only when the picture is cut too** and the Music lane
is unlocked. A clip's place is measured on the output's axis, which is the
picture's axis, so a cut that does not shorten the picture must not move a clip
away from the frames it was placed against.

That gives three cases:

- **Video and Music both unlocked** — a clip after the cut moves earlier with
  the picture, a clip across it is split in two (the new edges carry no fade),
  and a clip inside it goes.
- **Video locked** — the clips stay at their times, even though the Music lane
  is unlocked, because the picture did not move.
- **Music locked** — the clips stay at their times, like any locked lane.

And it is why the scissors is **disabled when Music is the only unlocked lane**
— which is exactly what clicking the Music channel's name produces. The cut
would have nothing to move, and a scissors that appears to work and changes
nothing is worse than one that says why. Unlock Video to cut both, or change
the music on its own with the clip's own gestures: drag its ends to trim it, or
select it and press Delete.

A **split** is not subject to any of this. It changes no clip's place, so it
cannot put the music out of step with the frames: **S** cuts the clips under
the playhead in two wherever the lane is unlocked.

### Hearing the voice alone

The **eye** in the Music header silences the music *here only*, and it takes
effect in the middle of a play. The render always mixes the music in, whatever
the eye says.

### When a file leaves the library

If a file is deleted from the library while a project still names it, that
project's clips are marked **missing**: they are hatched on the lane, they are
silent in the audition, and the render refuses on them rather than quietly
producing a video without them.

While any clip is missing the whole lane is frozen. Every change to the Music
lane sends the entire list of clips, and the server will not store a list that
names a file it does not have — so moving an untouched clip is refused by the
name of one nobody touched. There are two ways out, and the banner above the
strip carries both:

- **Remove the stuck clips** — one button, which takes out every clip whose
  file is missing in a single go, because that is the only list the app can
  store while one of them is there;
- put the file back in the library **under the same name**, which restores the
  clips exactly as they were.

## The keys

The keyboard is ignored while you are typing in a box, and while the library is
open.

| Key | What it does |
| --- | --- |
| **Space** | Play or pause |
| **,** / **.** | One frame back or forward |
| **Ctrl+Home** / **Ctrl+End** | Jump to the start or the end |
| **Shift+,** / **Shift+.** | Grow the selection a frame at its start or its end |
| **Ctrl+Shift+Home** / **Ctrl+Shift+End** | Stretch the selection to the start or the end of the picture |
| **Delete**, **Backspace**, **Ctrl+Delete**, **Ctrl+X** | Cut the selection — or remove the selected music clip |
| **S** | Split the unlocked lanes at the playhead |
| **Ctrl+Shift+S** | Split every lane, locks and all |
| **[** / **]** | Nudge the selected blocks 0.05 s (Shift: 0.25 s) |
| **Ctrl+Z** / **Ctrl+Y** | Undo / redo (Ctrl+Shift+Z redoes as well) |
| **Escape** | Give up the clip, then the blocks, then the range |
| **Ctrl+Shift+D** | Clear the selection |
| **Ctrl+Shift+=** / **Ctrl+Shift+−** | Zoom in or out (Ctrl+wheel does it at the pointer) |
| **Ctrl+Shift+7** / **8** / **9** | Fit the whole edit / fill the strip with the selection / zoom all the way in |
