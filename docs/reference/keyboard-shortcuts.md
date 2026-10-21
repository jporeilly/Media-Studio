# Keyboard shortcuts

Every key the Timeline, the slide editor, the annotation editor, the walkthrough builder and player, the screen recorder and the guided capture of a walkthrough's steps answer to.

The Timeline's keys are ignored while you are typing in a box and while the music library is open; the slide editor's arrows are ignored in a field and while a dialog is open; the annotation editor's keys are ignored while you are typing in a text box, except Ctrl+S, which keeps the text and saves; the walkthrough builder's Undo and Redo are ignored while you are typing in a box, and its Ctrl+S saves from anywhere.

## The Timeline

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
| **Alt** (held while dragging) | No snapping for that drag — a handle, a range's end, a piece's edge, a block, a clip or a marker (Ctrl does the same for a block or a clip once the drag has begun; held as you press, Ctrl makes the drag a range selection instead) |
| **Ctrl+Z** / **Ctrl+Y** | Undo / redo (Ctrl+Shift+Z redoes as well) |
| **Escape** | Give up the marker, then the clip, then the blocks, then the range |
| **Ctrl+Shift+D** | Clear the selection |
| **Ctrl+Shift+=** / **Ctrl+Shift+−** | Zoom in or out (Ctrl+wheel does it at the pointer) |
| **Ctrl+Shift+7** / **8** / **9** | Fit the whole edit / fill the strip with the selection / zoom all the way in |

Keys pressed with **Alt** held are left to the browser, except the nudge keys, which answer to the two physical keys right of P and to whatever types `[` and `]` (AltGr+8 and AltGr+9 on a German layout); **{** and **}** are the quarter-second nudge. A frame is a thirtieth of a second.

## The slide editor

| Key | What it does |
| --- | --- |
| **←** / **→** | The previous / next slide (not while the focus is in a box, or a dialog is open) |
| **Ctrl+S** (in the notes box) | Save this slide's notes |
| **Enter** (in the pause box) | Save the pause |

## Boxes on the Timeline and the List

| Key | What it does |
| --- | --- |
| **Enter** / **Escape** (in the marker name box) | Keep the name / put the old one back |
| **Enter** (in the List's Offset, Speed or Voice box, or a clip's fade box) | Commit the value |

## The annotation editor

| Key | What it does |
| --- | --- |
| **V** | Select |
| **A** / **C** / **T** | Arrow / Callout / Text |
| **R** / **E** / **L** | Rectangle / Ellipse / Line |
| **S** / **B** / **H** | Step / Blur / Highlighter |
| **K** / **M** | Crop / Magnify |
| **Delete**, **Backspace** | Remove the selected shape |
| **Escape** | Deselect; give up the shape being drawn; in a text box, put the old text back (a box just placed is removed) |
| **Ctrl+Enter** (in a text box) | Keep the text (clicking away does too) |
| **Ctrl+Z** / **Ctrl+Y** | Undo / redo (Ctrl+Shift+Z redoes as well) |
| **Ctrl+S** | Save (in a text box too: keeps the text, then saves) |
| **+** / **−** / **0** | Zoom in / zoom out / fit the whole still (**=** and **_** work as **+** and **−**) |

## The walkthrough builder

| Key | What it does |
| --- | --- |
| **Ctrl+S** | Save the whole walkthrough (from a text box too) |
| **Ctrl+Z** / **Ctrl+Y** | Undo / redo over the whole walkthrough (Ctrl+Shift+Z redoes as well); not while typing in a box |
| **Escape** (while dragging the hotspot) | Put the hotspot back as it was |
| **Enter** (in the New walkthrough name box, on the list) | Create it |

## The walkthrough player

| Key | What it does |
| --- | --- |
| **→**, **Space**, **Enter** | Go on (on the last step, finish); Enter or Space on a focused button (the hotspot, Next, a chapter) is that button's click |
| **←** | Go back (from the end card, back to the last step) |
| **Home** / **End** | The first / the last step |
| **T** | Open or close the transcript drawer (not while typing) |
| **↑** / **↓** (with the Chapters menu open) | Move within the menu, wrapping round; **Enter** opens the chapter |
| **Escape** | Close the transcript if it is open, else the Chapters menu if it is open, else leave the player: to the builder it was opened from, else to the list |
| A click on the hotspot | Go on |
| A click on the picture outside the hotspot | Make the hotspot pulse once more; it does not go on |

## Screen recording (desktop app)

While a recording runs these work from anywhere, whichever window has the focus, as in Snagit.

| Key | What it does |
| --- | --- |
| **Shift+F9** | Pause, or resume |
| **Shift+F10** | Stop and save; during the 3-2-1 countdown, call the recording off |
| **Esc**, a right click or **Alt+F4** (on the region overlay) | Give up the region |

The keys are taken from the countdown until the recording stops, and not otherwise. If another program holds them the app says "the recording keys are off", and the recorder bar's buttons still pause and stop.

## Capturing a walkthrough's steps (desktop app)

While **Capture steps…** runs in the walkthrough builder, from anywhere, whichever window has the focus.

| Key | What it does |
| --- | --- |
| A **click** - left or right - anywhere but on the app's own windows (with **Capture on click** ticked, the default) | Take the still for the step the capture bar shows at the moment of the press, attach it, and place the hotspot round the point clicked; a right click is a step like a left one, and a click while the previous still is still being taken is a step too (its still is taken at once and attached after the first). Not a step: the second press of a double-click (within 400 ms and 8 px of the first, with the same button), a click outside the region's frame (the bar says "Click inside the frame to capture this step.") or, in Full screen, off the captured monitor ("Click on the captured screen to capture this step." - every click ON it is a step, other applications' included), a third click while two stills are being taken (the bar says "Too fast: wait for the still."), and a click on the capture bar or the walkthrough's window |
| **Shift+F8** | Take the still without a click, attach it, and place the hotspot round the pointer as it was at the moment of the press |
| **Esc**, a right click or **Alt+F4** (on the region overlay, when the kind is Region) | Give up the region; nothing starts |

The key and the click hook are taken from Start until Done, and not otherwise; it is never held together with the recording keys, and a recording never hooks the clicks. If another program holds the key the bar says so - a red line under the step - and the clicks and its **Capture** button still work (a capture from the button places no hotspot: the pointer is on the bar): the click hook is installed whether or not the app has the key. If the clicks cannot be hooked the bar says that instead, the "Click to capture" line goes, and the key still captures; if neither the key nor the clicks could be had, one line says both and points to the bar's **Capture**. Closing the desktop app's window while the bar is up closes the bar, removes the hook and gives the key back with it.

## See also

- [The Timeline](../guides/timeline.md)
- [The slide editor](../guides/slide-editor.md)
- [Markers and chapters](../guides/markers-and-chapters.md)
- [Screen capture](../guides/capture.md): taking and annotating a still
- [Walkthroughs](../guides/walkthroughs.md): the builder and the player
