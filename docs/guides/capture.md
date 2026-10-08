# Screen capture

Record the screen with the microphone and the computer's sound, or take a still of a region, a window or a screen. A recording becomes a video project; a still, a slide.

Screen capture works in the **desktop app only**. It needs the app and the screen on one machine. When Media Studio is opened in a browser on another machine, the Capture button and the Dashboard's Capture tile are not there, and the Projects page says why in one line. Inside the desktop app, the page first asks the shell once whether it is allowed the capture commands; until the answer comes nothing is shown, and if the answer is no, the button and the tile stay hidden there too.

The server holds the same rule. A request to freeze or read the screen, to take a still, or to start or feed a recording, that does not come from this computer is refused with "Screen capture works only in the desktop app on this computer." and recorded in the audit log as `capture.refused`. Run as a team server, Media Studio therefore never shows its own desktop to anyone. A request through a proxy or a tunnel counts as not from this computer. Looking at, downloading, adding and deleting stills, and saving a recording, work from anywhere for the user who made them.

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

**Captures**, at the foot of the Projects page, lists the stills, newest first. Each still has **Add to deck…**, **Copy**, **Save as…** and a bin, **Delete still**.

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
- Stills are kept in `data\captures\<id>\` (`still.png` and `capture.json`). A recording not saved yet lives in `data\temp\recordings\<id>\`; back that folder up with the rest until it is saved.

**The shell's capture commands** (the monitor and window lists, the overlay, the bar, the keys) are granted only to the page the backend serves, at the port the desktop app started it on, together with the app's events and four window calls (minimise, restore, focus, drag the bar). Another local web page shown in the app's window gets none of them. The overlay and the bar are always opened on the backend's own address; no page chooses what they show.

**The recorder bar** is excluded from screen capture by Windows. That needs Windows 10 version 2004 or later. On an older Windows the bar is in the picture, and the app says so.

Every capture is recorded in the audit log:

- `recording.start`, `recording.finish` and `recording.discard` for a recording, plus `project.import` for the project it makes (the pieces, the heartbeat and the recorder letting go of a failed recording are not recorded);
- `capture.still`, `capture.add_to_deck` and `capture.delete` for a still;
- `capture.refused` for a request to touch the screen, or to start or feed a recording, from another machine.

A still or a recording belongs to whoever made it, like a project: an editor sees their own, an administrator sees everyone's.

## See also

- [Projects](projects.md): the page the captures live on
- [The Timeline](timeline.md): cutting a recording, and music under it
- [Keyboard shortcuts](../reference/keyboard-shortcuts.md)
- [Limits](../reference/limits.md)
