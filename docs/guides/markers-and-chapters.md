# Markers and chapters

A marker is a named moment of the picture, dropped on the Timeline's ruler; when the video is rendered every marker becomes a chapter in the MP4.

## What you see

Each marker is a flag on the ruler with its name beside it; the tooltip shows the name and the time. A selected flag is highlighted. Dropping a marker, or double-clicking one, opens a **Marker name** box on the flag. A marker that sits in removed picture is not drawn; it is still there, waiting for the frames to come back.

## What to do

**Drop.** Press **M**. A flag appears at the playhead, named "Marker N", with its name box open. Type a name and press Enter (or click elsewhere) to keep it, or press Escape to keep the default. Nothing is saved while the box is open (the marker is stored when the box closes, in one save), so a page reload with the box open loses it. Dropping a marker keeps any range selection you have made on the strip.

**Name.** Double-click a flag to rename it: Enter keeps the new name, Escape puts the old one back. A name is 1 to 80 characters; tabs and newlines in a pasted name are dropped.

**Move.** Drag the flag along the ruler. It snaps to the playhead, the joins, the sentence pins, the clips' edges and the other markers, like everything else on the strip; hold Alt to place it freely, or switch the magnet off. The label shows the moment it will land on. A marker cannot be dragged into removed picture, because the ruler only shows kept frames.

**Select and remove.** Click a flag to select it (this does not move the playhead). **Delete**, **Backspace**, **Ctrl+Delete** or **Ctrl+X** removes the selected marker; a selected marker wins over a selected clip and over the range. **Escape** gives the marker up first.

**Jump.** **Ctrl+[** and **Ctrl+]** move the playhead to the previous and the next marker. Plain **[** and **]** still nudge the selected sentence blocks.

**Limits.** 200 markers per project; names of up to 80 characters with no control characters. Every marker change is one save, and Undo takes it back like any other edit.

**Chapters.** Press **Re-voice** or **Render** on the Re-voice card. Every marker the ruler shows becomes a chapter in the MP4, running from its moment to the next marker's (or to the end) and titled with its name. A marker in removed picture is no chapter. When the first marker is not at 0:00, the render also writes a leading untitled chapter from the start of the video to the first marker, so the whole file is covered. With no markers the render is exactly what it was, and the file keeps whatever chapters the source video had. A deck's rendered video has a chapter per slide instead; see [Generate a video](generate-video.md).

## Under the hood

A marker is stored on the project's edit as `{id, at, name}` with `at` in the source's seconds, like a sentence's spoken moment and unlike a music clip's place on the output. So a cut before a marker moves it with its frame, a cut over it hides it, a restore brings it back, and no cut, split or trim ever rewrites it; the timeline reads each marker back with where it lands in the output, or nothing for one in removed picture. The server checks the list on every save: at most 200 markers, each inside the source, each name trimmed to 1–80 characters with no control character (a newline or a tab in a chapter title is cut by ffmpeg's own reader). The list is sent whole in one request, and a marker commit does not re-send the music clips.

At render time the chapters are computed from the markers as stored then, projected through the picture's list: `(start, end, title)` per drawn marker, each running to the next drawn marker or to the output's end, a chapter of no length left out, and a leading untitled chapter when the first one is not at 0. They are written into the final file, after the mux and after the music pass, by one stream-copy remux with the chapter list mapped explicitly, so a source that carried chapters of its own is replaced rather than kept; the remux keeps the file's index at the front, so it still starts playing in a browser before it has all arrived. A remux that fails is logged and leaves the un-chaptered file; it never fails the job.

A marker commit is recorded in the audit log as `project.edit` with the number of markers, never their names or moments.

## See also

- [The Timeline](timeline.md): the ruler, snapping and the keys
- [Re-voicing a video](re-voice.md): the render that writes the chapters
- [Keyboard shortcuts](../reference/keyboard-shortcuts.md)
