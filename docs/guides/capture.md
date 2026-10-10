# Screen capture

Record the screen with the microphone and the computer's sound, or take a still of a region, a window or a screen. A recording becomes a video project; a still, a slide.

Screen capture works in the **desktop app only**. It needs the app and the screen on one machine. When Media Studio is opened in a browser on another machine, the Capture button and the Dashboard's Capture tile are not there, and the Projects page says why in one line. Inside the desktop app, the page first asks the shell once whether it is allowed the capture commands; until the answer comes nothing is shown, and if the answer is no, the button and the tile stay hidden there too. The **Captures** list is not part of that rule: stills already taken are listed, annotated, added to decks, copied, downloaded and deleted from any client, the browser edition included; only taking one needs the desktop app.

The server holds the same rule. A request to freeze or read the screen, to take a still, or to start or feed a recording, that does not come from this computer is refused with "Screen capture works only in the desktop app on this computer." and recorded in the audit log as `capture.refused`. Run as a team server, Media Studio therefore never shows its own desktop to anyone. A request through a proxy or a tunnel counts as not from this computer. Looking at, downloading, annotating, adding and deleting stills, and saving a recording, work from anywhere for the user who made them.

## What you see

**Capture** sits in the Projects page's header, beside **Import**. The Dashboard has a **Capture** tile that opens the Projects page with the same dialog already up.

The **Capture** dialog has two tabs, **Record screen** and **Capture still**. Each offers the same three choices:

- **Region**: a rectangle you drag;
- **Window**: one window;
- **Full screen**: one monitor.

**Record screen** offers:

- **Microphone**, with a list of microphones. The names appear once the app has been allowed a microphone; **List microphones** asks for that.
- **System sound**: everything the computer plays.

**Capture still** offers:

- **Cursor**: whether the pointer is in the still.
- **Delay** (None, 3 s, 5 s, 10 s): time to open a menu before the still is taken.
- **Monitor**, for a full-screen still on a machine with more than one.

While a recording runs, a small **recorder bar** floats beside it. It shows the time, **Pause**/**Resume** and **Stop**; during the 3-2-1 countdown, **Cancel**. The Projects page shows the same controls. The bar is left out of the picture wherever it sits (on Windows 10 version 2004 or later; on an older Windows it is in the picture, and the app says so).

**Captures**, at the foot of the Projects page, lists the stills, newest first, in the browser edition as in the desktop app. Each still has **Annotate**, **Add to deck…**, **Copy**, **Save as…** and a bin, **Delete still**. A still annotated in the editor shows the annotated picture, with a small **annotated** badge.

**Recordings not saved yet** appears above the projects only when a recording was stopped and never saved, for example after a crash, a closed window, a full disk or a save that failed, and not while a save is running. Each one offers **Save as a project** and **Discard**. The recording being made right now is never listed there, so it cannot be saved or thrown away from under the recorder; the server refuses that too.

## What to do

### Record the screen

1. Press **Capture**, keep **Record screen**, and choose **Region**, **Window** or **Full screen**. Leave **Microphone** and **System sound** on or turn them off, then press **Record**.
2. **Region only.** Every monitor freezes under a dimmed sheet with crosshairs, the pointer's position and a magnifier. Drag the rectangle; the live size follows the drag. A click takes the window under the pointer, cut to the monitor you clicked on if it runs past its edge. **Esc**, a right click or closing the sheet with **Alt+F4** gives up.
3. **The app's share dialog opens** (WebView2's own picker).
   - For a **Window**, choose the **Window** tab and pick the window.
   - For a **Full screen** or a **Region**, choose **Entire Screen** and pick the screen. For a region, pick the screen you drew on. A different screen, or a window, is refused with "The screen shared is not the one the region was drawn on", and you start again.
   - To record the computer's sound, turn on **Share with system audio** in that dialog as well. The app's own switch only asks for it.
   - Then press **Share**.
4. The first time, the app asks to use your microphones. Answer **Allow**.
5. The app's window steps aside and the bar counts down 3, 2, 1. Then the recording runs. To call it off during the countdown, press the bar's **Cancel**, the Projects page's **Cancel**, or **Shift+F10**: nothing is recorded or kept.
6. Pause and resume with **Shift+F9** and stop with **Shift+F10**, from anywhere, as in Snagit. The bar's buttons do the same.
7. On **Stop**, the window comes back on the Projects page. A **Saving the recording** card follows the save, and the new video project opens when it is ready. Until it does, closing the window is refused: closing it would stop the save. **Cancel** on that card stops the save; the recording stays under **Recordings not saved yet**, to save again. If the server stops answering during the save, the app says so and lets the window close. If the app is closed anyway (a crash, a power cut), the recording is offered again under **Recordings not saved yet** the next time it starts.

The new project is named after the window you recorded, or "Screen recording" with the date and time. From there it is a video project like any other: transcribe it, re-voice it, cut it on the Timeline, lay music under it.

The **microphone and the system sound are mixed at the same level**. Nothing is ducked and neither is turned down. For background music, leave it out of the recording and add it afterwards on the Timeline's Music lane, where its level is yours to set.

**The pointer is always in a recording.** The share dialog's capture draws it and cannot be told not to, so the Cursor switch belongs to stills.

The share dialog's strip, "… is sharing your screen", sits at the bottom of the screen while you share. It is WebView2's, not Windows'. A whole-screen recording includes it. Press its **Hide**, or keep it out of the part of the screen that matters.

### Save a recording that was cut short

A recording stopped by a crash, a closed window, a full disk or a failed save waits under **Recordings not saved yet**, with what reached the disk.

- **Save as a project** makes it a video project, as a normal Stop would.
- If a piece of it never reached the disk, the button reads **Save the part before the gap**. It asks first, and says how much can be saved and how many pieces after the gap are given up. Those pieces are deleted with the rest once the project exists. Without that confirm nothing is dropped.
- If its very first piece never arrived, nothing can be saved, and **Save as a project** is greyed out. **Discard** deletes it.

A recording whose recorder is still running, here or after a reload, is not listed until the recorder has been silent for 20 seconds.

### Scaling and more than one monitor

Capture was tested on monitors at **100 % scaling** (Windows' Settings > Display > Scale), one or several. On a monitor scaled to 125 % or more, or with monitors at different scales, the region overlay and a region's crop are **not verified yet**. A full-screen or window recording, and a full-screen still, do not go through the region overlay. If a region comes out shifted or the wrong size on a scaled monitor, record the full screen or the window instead and cut it on the Timeline.

### Take a still

1. Press **Capture**, choose **Capture still**, and choose **Region**, **Window** or **Full screen**. Set **Cursor** and **Delay**, then press **Capture** (with a delay set, the button reads **Capture in 3 s**, **Capture in 5 s** or **Capture in 10 s**).
2. For a region, drag a rectangle on the frozen screen. For a window, click it: the outline of the window under the pointer shows what a click will take.
3. The still appears in **Captures**. It is a pixel-exact copy of the screen at its physical size.

There is no share dialog for a still.

### Use a still

- **Add to deck…** opens **Add to deck**: choose a deck project and press **Add as last slide**. The still becomes that deck's **last slide**, with empty notes to write, and the status line says which slide it became. The deck's .pptx gains the slide as a picture slide, so an export from PowerPoint agrees. If the still's shape is not the slide's, it is **letterboxed**, never stretched. The bars take the deck's own background, the colour round the edge of its first slide image, and the image takes that slide's size. A deck with no rendered slide yet gets white at 1920 × 1080. A PDF cannot take a slide, because its pages are fixed. A deck busy with a job refuses until the job ends.
- **Copy** puts the image on the clipboard.
- **Save as…** downloads it as a PNG named after the capture.
- The bin, **Delete still**, asks first, then deletes it.

Once a still has been annotated (below), all three use the annotated picture.

### Annotate a still

Press **Annotate** on a still. The editor opens the original still under a toolbar of twelve tools, each with a key:

- **Select** (V), the default: click a shape to select it, drag it to move it, drag a handle to resize it (a line's ends, a callout's tail), press **Delete** or **Backspace** to remove it. Double-click a Text or a Callout to edit its words.
- **Arrow** (A): drag from the tail to the tip.
- **Callout** (C): click to place a filled, rounded box with a tail, type, click away. Drag the tail's handle to where it should point.
- **Text** (T): click to place a text box, type, click away. The text wraps to the box; drag a handle to widen it. For a Text or a Callout, **Ctrl+Enter** keeps the words as clicking away does; **Esc** puts the old words back, or removes a box just placed; a box left empty is not kept.
- **Rectangle** (R), **Ellipse** (E) and **Line** (L): drag.
- **Step** (S): click to drop a numbered circle. The numbers run 1, 2, 3 … in the order placed; deleting one renumbers the rest; **Restart numbering** (on the style row, enabled while Step is the tool or a step is selected) starts the next step at 1 again, so one still can carry 1-2-3 and 1-2 for two sequences. The circle's size follows the stroke width.
- **Blur** (B): drag over what must not be read, a password or a name. It is a mosaic, not a soft blur (see Under the hood).
- **Highlighter** (H): drag. A translucent yellow that leaves the text under it legible; it has its own colour and ignores the stroke width.
- **Crop** (K): drag the rectangle to keep; the rest is dimmed. One crop per still: the saved picture is the crop's size, and a shape outside the crop is clipped from the picture, never deleted. Drag the crop's handles to adjust it, or drag inside it to move it; a new drag outside it replaces it.
- **Magnify** (M): drag the rectangle to enlarge. A 2× copy (3× from the **2×** / **3×** buttons on the style row, offered while Magnify is the tool or a lens is selected) appears beside it with a border and a thin connector, and can be dragged with Select to where it should sit. The lens shows the original pixels, not the annotations under them.

The toolbar holds one shared style, remembered for the session: a **colour** (eight that read on light and dark screens, a red first, plus black and white; the Highlighter keeps its own, yellow by default), a **stroke width** (Thin, Medium, Thick) and a **text size** (Small, Medium, Large). Changing the style while a shape is selected changes that shape too. **Undo** and **Redo** (Ctrl+Z, Ctrl+Y) go back without limit within the visit; a click that moves nothing is no step. **Esc** deselects, or gives up the shape being drawn. **Fit**, **50 %**, **100 %** and **200 %** zoom; **+** and **−** step through 25, 50, 100, 200 and 400 %, and **0** fits; scroll to pan when the still is larger than the view. A still carries at most 500 shapes; past that the status line says "A still can carry at most 500 shapes."

- **Save** (Ctrl+S) keeps the annotated picture. The status line reads "Unsaved changes" whenever the picture differs from what was saved, and **Save** is greyed out otherwise; Ctrl+S with nothing changed answers "Nothing to save." Once saved, the page stays open and the status line says "Saved". From then on **Add to deck…**, **Copy** and **Save as…** use the annotated picture, the Captures card shows it with an **annotated** badge, and opening the editor again shows the shapes, still editable. Saving with every annotation removed and no crop is a revert: the export is deleted and the audit log says `capture.revert`.
- **Save and add to deck…** saves, then opens the same **Add to deck** dialog the Captures card has.
- **Revert to original** asks first ("Throw away every annotation on *name*? The original still is kept as it is.", with **Revert**), then throws every annotation away. The original still was never changed.
- Leaving the page with unsaved changes through the app's own links, or with Back and Forward, asks first, in the app's own dialog ("Leave without saving?", with **Leave** and **Cancel**). In a browser, a reload or a closed tab asks through the browser's prompt. **Closing the desktop app's window does not ask: save first.**

Taking a still needs the desktop app; annotating one does not. The editor is pure web and touches no screen, so it works in the browser edition too: the Captures card lists the stills there as well, and **Annotate** opens the editor from it. A still of more than 100 million pixels is not opened by the editor, which says so ("This still is *W* × *H* pixels, more than the 100 million the editor can hold. Add it to a deck as it is, or take a smaller still."), and the server refuses a picture of that size at save as well: add such a still to a deck as it is.

## Under the hood

**The recording** is made in the app's own window with the browser's screen capture (`getDisplayMedia`), the only way to the computer's sound without a driver.

- The microphone comes from `getUserMedia`. The two are mixed at unity into one track.
- The sound is asked for without the browser's voice processing. Echo cancellation, noise suppression and automatic gain are on by default for the captured sound, and they flatten music.
- The picture is asked for at 30 frames a second.
- `MediaRecorder` makes a WebM (VP9 and Opus) at 8 Mbit/s of picture and 192 kbit/s of sound, whatever the screen's size. Every **five seconds** a piece of it is sent to the server and dropped from memory once the server has it. A crash or a closed window therefore loses only the last few seconds; what arrived is listed under **Recordings not saved yet**.
- At Stop, any piece that failed to arrive is sent again, and the server refuses to save while one is missing.
- While it records, paused or not, the recorder tells the server every five seconds that it is still there. Until it has been silent for **20 seconds**, the server refuses to save or discard the recording for anyone but the recorder.
- A recording is stopped with a message at **two hours**. It does not start with less than **2 GB** free.
- **The disk.** A piece that would leave less than **1 GB** free is refused. The recording stops with "Less than 1 GB is left on the disk…", and what arrived is kept. One recording may take at most **16 GB** (two hours at the recorder's rate is about 7.4 GB); past that it stops the same way. If the recorder itself fails, what arrived is kept as well; a recording that never got a piece to the server is thrown away.
- Closing the window while a recording is in flight, or while its save runs, is refused, and the app says why. The shell refuses the close; a reload asks first. The refusal never outlives the server: it ends when the save does, when the save cannot be asked about three times running, when you sign out, and when the server has stopped.
- Navigating within the app does not stop it: the recorder lives above the pages. A reload during a recording loses the recorder; the recorder bar and the keys it held are released when the page comes back, and what arrived is offered under **Recordings not saved yet**.

**The save** is a job of kind `recording`. ffmpeg reads the pieces in place, in order (no joined copy is written, so a save needs room for the MP4 only), and converts the WebM with the bundled ffmpeg to the MP4 every file of the app's is:

- H.264 High, 4:2:0, at the source's size and a constant 30 frames a second (the browser sends a frame only when the screen changes);
- AAC 48 kHz stereo at 192 kbit/s;
- the index at the front.

For a region the picture is cut to it at its exact position, its sides made even. A screen that is still at the end sends no frames, so the last frame is held to the end of the sound; a narration over the end is not cut short. The pieces are deleted once the project exists.

The save's time limit comes from the pieces the server holds, five seconds each: five minutes plus one and a half times the recording's length. In testing, a 10-minute 1080p recording converted in about a minute. Over that recording the sound drifted from the picture by 3 ms, against the 33 ms one frame lasts. A save that fails or is cancelled keeps the pieces for another try. A save cut short by a closed app or a crash is offered again at the next start.

**The overlay and the stills.**

- The region overlay is one window per monitor, over a picture of that monitor frozen just before it opened (held for that moment in `data\temp\capture\`). The frozen pictures are deleted once they have served, and any a crash left behind are deleted at the next start. A picture another program holds open is left for the next time; it never stops the app starting, and neither does a recording whose interrupted save cannot be reset.
- A still is taken by the bundled ffmpeg (`gdigrab`). It copies the screen pixel for pixel, at physical pixels across every monitor, and draws the pointer only when asked.
- The window outline and the monitor sizes come from Windows itself.
- Stills are kept in `data\captures\<id>\` (`still.png` and `capture.json`, and `annotated.png` once annotated). A recording not saved yet lives in `data\temp\recordings\<id>\`; back that folder up with the rest until it is saved.

**The annotations.** The editor keeps a document, a list of shapes in the still's own pixel coordinates plus the crop, and draws what it shows with one renderer on a canvas. Save sends both in one request: the document, and the picture that same renderer exported at the crop's size (or the still's) as a PNG. The server checks the document against the still (every number finite and no further than the still's size beyond its edge, which is also as far as the editor lets a shape be dragged or a text box grow; the crop inside it and at least 16 × 16; at most 500 shapes, and 500 characters in a text), refuses a picture of more than 100 million pixels, reads the picture's own header for its size before anything else looks at it, checks that it is a real PNG of exactly that size that decodes, and keeps both: `capture.json` gains `annotations`, `annotated`, `annotated_at` and `output_width` / `output_height`, and `annotated.png` lands beside `still.png`. The server never re-renders: what the page showed is what is saved, pixel for pixel. A document that fails the check, or a picture that is not a PNG of the right size, or one that cannot be decoded, is refused with the reason and nothing is written; a picture over 64 MB is refused as it arrives. Should an export be damaged on disk afterwards, Add to deck says so ("The annotated picture cannot be read: revert the annotations and save them again.") rather than failing.

- **Which picture.** One rule answers it. A still that says it is annotated is served as `annotated.png` by the image, the download and Add to deck alike; otherwise, and always on request (`?original=1`, what the editor draws over), `still.png`. A record that says it is annotated while the export is missing is served as the original, and the log says so.
- **Blur is a mosaic**: square cells one hundredth of the still's shorter side (never under 8 px), each one colour taken from the original pixels under it. A soft (Gaussian) blur can be partly undone by sharpening; a mosaic at that size cannot be read back. The tool is still called Blur, because that is what people look for.
- **Revert** deletes `annotated.png` and the document from the record. `still.png` stays as it was taken.
- Text and callouts are rasterised with the app's own font stack, on the page and in the export alike, so the saved picture never differs from the preview in its lettering.

**The shell's capture commands** (the monitor and window lists, the overlay, the bar, the keys) are granted only to the page the backend serves, at the port the desktop app started it on, together with the app's events and four window calls (minimise, restore, focus, drag the bar). Another local web page shown in the app's window gets none of them. The overlay and the bar are always opened on the backend's own address; no page chooses what they show.

**The recorder bar** is excluded from screen capture by Windows. That needs Windows 10 version 2004 or later. On an older Windows the bar is in the picture, and the app says so.

Every capture is recorded in the audit log:

- `recording.start`, `recording.finish` and `recording.discard` for a recording, plus `project.import` for the project it makes (the pieces, the heartbeat and the recorder letting go of a failed recording are not recorded);
- `capture.still`, `capture.add_to_deck` and `capture.delete` for a still;
- `capture.annotate` (the number of shapes and whether it is cropped, never a callout's words) and `capture.revert` for a still's annotations;
- `capture.refused` for a request to touch the screen, or to start or feed a recording, from another machine.

A still or a recording belongs to whoever made it, like a project: an editor sees their own, an administrator sees everyone's.

## See also

- [Projects](projects.md): the page the captures live on
- [The Timeline](timeline.md): cutting a recording, and music under it
- [Keyboard shortcuts](../reference/keyboard-shortcuts.md)
- [Limits](../reference/limits.md)
