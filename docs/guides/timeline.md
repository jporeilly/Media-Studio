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

**J**, **K** and **L** shuttle. **L** plays; pressed again while playing it runs
the picture forward at 2×, then 4×, then 8×, and stays there. **J** runs it
backwards at 1×, then 2×, 4×, 8×. **K** stops, and puts the rate back to 1× — so
do Space, a seek, any transport button, and reaching an end. **K** held with
**J** or **L** tapped steps one frame back or forward. Above 1× and backwards the
shuttle is picture only: the audition's audio cannot run faster without changing
its pitch, and there is no such thing as reverse speech, so it is silent, and the
readout beside the clock says the direction and the rate ("◀◀ 4×", "▶▶ 2×").
**L** inside a selection stops at its end, as Play does; **J** stops at the
selection's start when the playhead is inside one, else at the start of the edit.

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

The handles and a Ctrl+drag's ends **snap**: within 8 px of the playhead, a
join, a sentence's start or end, a music clip's edge, a marker, 0 or the end
of the picture, the end you are dragging lands exactly on it and its time
shows ⌖.
Hold **Alt** while dragging to place it freely for that one drag. The
**magnet** on the toolbar, beside Fit, turns snapping off for every drag on the
strip — the handles, a range's ends, a piece's edge, a sentence block, a
music clip and a marker's flag alike — until it is switched on again; it is on by default and
remembered per project in this browser, like the locks. A block and a clip
still take Ctrl as well as Alt for a free drag.

**Shift+,** and **Shift+.** grow the selection a frame at a time;
**Ctrl+Shift+Home** and **Ctrl+Shift+End** stretch it to the start or the end
of the picture. **Escape** gives up whatever is selected, in that order — the
marker first, then the music clip, then the sentence blocks, then the range — and a double-click
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
back — unless one of them names a file that has left the library: a cut of the
picture across such a clip is refused before anything is saved (see *When a
file leaves the library*, below).

A sentence that is spoken after the end of the cut picture is **dropped** —
the re-voice leaves it out entirely. The strip says so above the ruler and on
the block itself, and names the two ways out: unlock Narration and cut it too,
or drag the sentence earlier.

## Undo and redo

**Ctrl+Z** undoes the last edit — a cut, a split, a drag, a nudge, a reset,
anything done to the Music lane, or a marker dropped, moved, renamed or
removed — and **Ctrl+Y** (or **Ctrl+Shift+Z**) redoes
it. The toolbar has both buttons.

Undo is refused when it would lengthen a music clip whose file has left the
library: the undo of a trim or a split of such a clip, and the undo of its
removal. The refused step is taken off the list and the message says so; the
steps before it are often refused for the same reason. To undo them, put the
file back in the library under the same name first. Redo likewise: a redo the
server refuses is taken off the redo list. (A cut of the picture across such a
clip is refused before it is made, so every cut made while a file is missing
can be undone.)

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

## Trimming a piece

A piece's two edges are the cut itself, and they move. Over the outer 8 px of
a piece on the Video or Narration lane the pointer becomes a resize arrow
(never more than a third of a narrow piece, so it can still be clicked). Drag
the **left edge** to the right to remove more from the piece's start, or back
to the left to **restore** what an earlier cut took — as far as the previous
piece's end, or the start of the video. Drag the **right edge** to the left to
shorten the piece, or to the right to restore, as far as the next piece's
start or the end of the video. The very first edge and the very last are the
head and tail trims, and they exist on a lane with one whole piece too — the
commonest edit, which used to take a selection and a Cut.

While you drag, the stretch that will go is hatched red and the stretch that
comes back is tinted green, the new edge is a bright line, and a label says
where the edge now is and how much: "0:06.700 · −1.200 s", or
"0:06.000 · +0.800 s restored". The edge snaps like the handles
(see "Selecting a range"), and Alt turns that off for the drag. Everything
after the edge moves with it when you let go — the output has no gaps, so a
trim ripples exactly as a cut does — and the filmstrip and the waveform redraw
on release.

A trim never removes a piece: the edge stops one frame short, and the label
says "one frame — use Cut to remove it". Select the piece and Cut instead.

A trim is a cut with a name, so it obeys the locks exactly as a cut does.
Shortening removes the same stretch of the output from every unlocked lane; a
locked lane is left exactly as it is, and shows no edges to grab while it is
locked. Restoring is the inverse, lane by lane:
at that moment on the output, each unlocked lane's own cut opens up again by
as much as it can — as much as was removed there, and nothing at all on a lane
that has no cut at that moment. So a picture cut made with Narration locked,
restored later, brings the frames back and leaves the sentences exactly where
they are: a restore never invents narration that was never cut. The Audio lane
has no edges of its own; the original audio follows the picture.

A cut made mid-sentence leaves that sentence's block straddling the narration
join, and a block sits above the pieces, so the narration's own edges at that
join cannot be grabbed from the Narration lane: with both lanes unlocked the
picture's edge trims both, and a narration-alone trim there needs the block
dragged clear first (Reset timing brings it back).

The music rides the picture here as everywhere. With Video and Music both
unlocked, shortening ripples the clips as a cut does, and restoring moves every
clip at or after the edge later by what the picture got back (a clip lying
across the edge stays where it is, unsplit). With either lane locked the clips
do not move, and a trim of the Narration lane moves them only as far as the
picture itself changed. Undo takes a trim back like any other edit.

## Markers

Press **M** to drop a marker at the playhead: a flag appears on the ruler with
its name box open — type a name and press Enter (or click elsewhere), or
press Escape to keep the default "Marker N". Nothing is saved while the box
is open: the marker is stored when the box closes, in one save, so a page
reload with the box still open loses it. Click a flag to select it, double-click it to rename it,
and drag it along the ruler to move it: it snaps like everything else (Alt
turns that off for the drag) and its label shows the moment it will land on.
Delete or Backspace removes the selected marker. **Ctrl+[** and **Ctrl+]**
move the playhead to the previous and the next marker (plain **[** and **]**
still nudge the selected sentence blocks). The flag's tooltip is the name and
the time.

A marker is a moment of the **picture** — it lives in the source's seconds,
like a sentence, not on the output like a music clip. So a cut before a marker
moves it with its frame, a cut over it hides it (the flag disappears; the
marker is still there, waiting), a restore brings it back, and no cut, split
or trim ever changes where a marker is. A marker cannot be dropped or dragged
into removed picture, because the ruler only shows kept frames.

**Markers become chapters.** When you render, every marker the ruler shows
becomes a chapter in the MP4, running from its moment to the next marker's
(or to the end) and titled with its name; a marker in removed picture is no
chapter. When the first marker is not at 0:00, an untitled chapter runs from
the start of the video to it, so the whole file is covered and the two kinds
of reader agree: the file's own chapter list, which VLC reads, and a player
built on ffmpeg's reader (mpv, ffprobe) both show the first named chapter
starting at the moment you set. With no markers the render is exactly what
it was, and the file keeps whatever chapters the source video had.

Selecting a marker gives up a selected music clip, and selecting a clip gives
up the marker. Delete removes the selected marker first, then a selected clip,
then the range; Escape gives them up in that order. Dropping, naming or moving
a marker leaves a range selection on the strip exactly as it was; removing one,
Undo and Redo clear it, as every other edit does. Every marker change is one
save, and Undo takes it back like any other edit.

## Re-timing a sentence

**Drag** a sentence block along the Narration lane to aim it somewhere else.
It snaps to the playhead, to the picture's joins, to the start and the end, to
the other sentences' pins and landed ends, and to **its own spoken moment** —
so putting it back where it was said is a snap rather than a hunt. Hold
**Ctrl** or **Alt** to drag freely, or switch the magnet off. While a
sentence is aimed away from where it was spoken, a ghost on the lane shows
where that was.

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
and the other clips, by either of its own edges; Ctrl or Alt drags freely, and
the magnet turns the snapping off. A clip cannot be dragged before zero or
past the end of the audition.

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

If a file a project names leaves the library — removed outside the app, or the
project restored or copied without it; the app itself refuses to delete a file
a project uses — that project's clips are marked **missing**: they are hatched
on the lane, they are silent in the audition, and the render refuses on them
rather than quietly producing a video without them.

The lane itself stays yours. A missing clip can be **moved**, its **level** and
**fades** set, and it can be **deleted** on its own, exactly like any other:
the app keeps a clip it already holds, it just will not take a clip on a file
the project never held. It can also be **trimmed shorter** from either end and
**split** with **S**, because every piece is a stretch of the file the project
already holds. The one thing a missing clip cannot do is **grow**. Nobody knows
how long the file is any more, so neither of its ends can be dragged back past
the part of the file it has now, and the inspector says so.

A cut of the picture that would shorten, split or remove a missing clip — a
range cut with the Music lane unlocked, or a piece's edge dragged in across it
— is refused before anything is saved, because it could not be undone while
the file is gone. The strip names the gesture and the file and the two ways
out: lock the Music lane to cut the picture alone, or remove the missing clip
first. A cut that only moves a missing clip along goes through as ever.

Undo is refused when it would lengthen a music clip whose file has left the
library: the undo of a trim or a split of such a clip, and the undo of its
removal. The refused step is taken off the list and the message says so; the
steps before it are often refused for the same reason. To undo them, put the
file back in the library under the same name first. Redo likewise: a redo the
server refuses is taken off the redo list.

The render is what refuses, and it refuses until the clips are gone or the
file is back. The banner above the strip names the files and carries both ways
out:

- **Remove the missing clip** (**Remove the N missing clips** when there are
  several) — one button, which takes out every clip whose
  file is missing in a single save (Delete on one of them takes just that one);
- put the file back in the library **under the same name**, and the clips play
  again as they now stand: a trim or a split made meanwhile stays, and their
  ends can then be dragged out again.

## The keys

The keyboard is ignored while you are typing in a box, and while the library is
open.

| Key | What it does |
| --- | --- |
| **Space** | Play or pause |
| **,** / **.** | One frame back or forward |
| **J** / **K** / **L** | Shuttle back / stop / forward; pressed again, 2×, 4×, 8× (picture only above 1× and backwards) |
| **K** held + **J** / **L** | One frame back or forward |
| **Ctrl+Home** / **Ctrl+End** | Jump to the start or the end |
| **Shift+,** / **Shift+.** | Grow the selection a frame at its start or its end |
| **Ctrl+Shift+Home** / **Ctrl+Shift+End** | Stretch the selection to the start or the end of the picture |
| **Delete**, **Backspace**, **Ctrl+Delete**, **Ctrl+X** | Cut the selection — or remove the selected marker, else the selected music clip |
| **S** | Split the unlocked lanes at the playhead |
| **Ctrl+Shift+S** | Split every lane, locks and all |
| **M** | Drop a marker at the playhead, its name box open |
| **Ctrl+[** / **Ctrl+]** | Jump to the previous / next marker |
| **[** / **]** | Nudge the selected blocks 0.05 s (Shift: 0.25 s) |
| **Alt** (held while dragging) | No snapping for that drag — a handle, a range's end, a piece's edge, a block, a clip or a marker (Ctrl does the same for a block or a clip) |
| **Ctrl+Z** / **Ctrl+Y** | Undo / redo (Ctrl+Shift+Z redoes as well) |
| **Escape** | Give up the marker, then the clip, then the blocks, then the range |
| **Ctrl+Shift+D** | Clear the selection |
| **Ctrl+Shift+=** / **Ctrl+Shift+−** | Zoom in or out (Ctrl+wheel does it at the pointer) |
| **Ctrl+Shift+7** / **8** / **9** | Fit the whole edit / fill the strip with the selection / zoom all the way in |
