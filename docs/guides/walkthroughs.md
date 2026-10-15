# Walkthroughs

Turn stills into a click-through walkthrough: each step a picture with a hotspot the viewer clicks, a tooltip or a card, and a chapter; a builder makes one, a player presents it.

A walkthrough is an ordered list of **steps**. Each step is a picture (a still from **Captures**, annotated or not), an optional **hotspot** (a rectangle on the picture the viewer clicks to go on), a **text** shown as a **tooltip** beside the hotspot or as a centred **modal** card, an optional **instruction** - what the learner does at the step, written as it would be spoken ("drag the Text file input onto the canvas") - and an optional **chapter** name that starts a new chapter at that step. A tooltip step with no hotspot has nothing to sit beside, so it is shown as a modal card. Read in order, the instructions are the walkthrough's **transcript**: the narration script of the narrated video to come.

Walkthroughs work in the **browser edition** as in the desktop app: the list, the builder and the player touch no screen. Only **taking a still** needs the desktop app ([Screen capture](capture.md)). There is no still capture inside the builder: take the still from the **Capture** tile, annotate it if you like, then add it as a step.

## What you see

**Walkthroughs** sits in the sidebar after Projects, and the Dashboard has a **Walkthroughs** tile.

The **list** shows a card per walkthrough: the first step's picture, the name, "12 steps · 3 chapters", when it was last saved and whose it is, and **Open** (the builder), **Play** (greyed out until the walkthrough has a step), **Duplicate** (the copy is named "<name> (copy)", and the page says so) and a bin, **Delete walkthrough**, which asks first ("Delete *name* and its 12 steps? The stills it was made from are kept.", with **Delete**). **New walkthrough** asks for a name and opens the builder. When there is none yet, the page says how one is made.

The **builder** has three columns under a toolbar. The toolbar holds the walkthrough's **Name**, **Undo**, **Redo**, **Save**, **Play**, **Transcript** and a status line that reads **Saved** when the walkthrough is as it was last saved or loaded, **Unsaved changes** whenever it differs, and a one-off line ("Nothing to save.", "Reloaded", or a refusal such as "Could not save: …") until the next change; **Saving…** while a save runs. On the left, the **step list**: a thumbnail and a number for each step, the chapter name when the step starts one, arrows to move a step up or down, a bin to delete it (asked first), and **Add step…** at the foot. In the middle, the selected step's picture with its hotspot. On the right, the step's properties: **Shown as** (Tooltip beside the hotspot, or Modal card), the **Text**, the **Instruction**, the **Placement** of the tooltip (Auto, Above, Below, Left, Right), the **Chapter**, **Replace picture…** and **Remove hotspot**. **Transcript** opens a panel under the toolbar with the walkthrough as Markdown, with **Copy** and **Save as…**.

The **player** fills the window. A header carries the walkthrough's name, "Step 3 of 12", a progress bar, a **Chapters** menu when there are chapters, **Transcript**, **Back**, **Next** (reading **Finish** on the last step) and **Close**. Under it, the picture is fitted to the window and centred on a neutral backdrop (a picture smaller than the window is scaled up to fill it); the hotspot is an outlined rectangle that pulses, with the tooltip beside it; a modal step is a centred card over the dimmed picture (a hotspot the step also has is drawn over the dim and still goes on when clicked). After the last step an end card offers **Restart** and **Close**.

## What to do

### Make a walkthrough

1. Take the stills first: **Capture** → **Capture still** in the desktop app, one still per step. Annotate the ones that need an arrow, a callout, a blur or a crop ([Screen capture](capture.md)): a walkthrough takes the annotated picture when there is one.
2. On **Walkthroughs**, press **New walkthrough**, give it a name and press **Create**. The builder opens.
3. Press **Add step…** and pick a still from the picker: your stills, each with a thumbnail, its name, its size and an **annotated** badge once it is annotated; one click picks it. The still's picture, as it shows now, is copied into the walkthrough as a new step after the selected one (at the end when none is selected), and the new step is selected. Add the rest the same way; a walkthrough holds up to 200 steps, and **Add step…** is greyed out at that.
4. **Draw the hotspot**: drag a rectangle on the picture over the thing the viewer should click. Drag inside it to move it, drag a handle to resize it; it is at least 8 × 8 pixels of the picture and stays inside it. **Remove hotspot** takes it away. Press **Esc** during a drag to put the hotspot back as it was.
5. Write the **Text** (up to 1,000 characters; line breaks are kept) and choose **Shown as**: a **Tooltip** sits beside the hotspot, on the **Placement** you pick, or on the side with room when it is Auto; a **Modal** card is centred over the dimmed picture (Placement is greyed out for it). A tooltip step without a hotspot is shown as a modal card, and the properties panel says so.
6. Write the **Instruction** (up to 2,000 characters; line breaks are kept): what the learner does at this step, as you would say it - "drag the Text file input onto the canvas", "create a hop between the two steps". The text is what the viewer reads on the picture; the instruction is what is said. Read in order, the instructions are the walkthrough's transcript and, later, its narration. A step with no instruction is narrated with its text.
7. Give the first step of each chapter a **Chapter** name (up to 80 characters; the name is trimmed). Every step with a name starts a chapter; the steps before the first named one belong to none. The chapter shows on the step in the list, in the player's **Chapters** menu and as a heading in the transcript.
8. **Reorder** with the arrows on a step, or drag a step and drop it where it should go. **Delete** a step with its bin; it asks first ("Delete step 3? Its picture leaves the walkthrough when you save; the still it was made from is kept.", with **Delete**).
9. **Replace picture…** copies the picture of another still (or the same still after annotating it again) onto the selected step. The step's **Shown as**, text, instruction, placement and chapter stay; the hotspot is kept and brought inside the new picture when that is smaller, or dropped when the picture cannot hold one.
10. **Transcript** (beside Play) shows the walkthrough as Markdown: the name as the title, a heading at each step that starts a chapter, and every step as a numbered item carrying its instruction - its text when it has none, and "(no instruction yet)" when it has neither. **Copy** puts the Markdown on the clipboard and says "Copied to the clipboard." (or "Could not copy: the page may not write to the clipboard." where the page is not allowed to); **Save as…** downloads it as `<name>.md`, the name made safe for a file: only letters, digits, `_`, `.` and `-` are kept, spaces become dashes, at most 60 characters, and `walkthrough.md` when nothing is left. This is the narration script for the narrated video to come.
11. **Save** (Ctrl+S). The whole walkthrough is written in one go, with the revision the builder last took (at opening, and from every save, Add step and Replace picture). Until then the status line reads **Unsaved changes**; afterwards, **Saved**; Ctrl+S with nothing changed answers "Nothing to save." **Undo** and **Redo** (Ctrl+Z, Ctrl+Y; Ctrl+Shift+Z redoes as well) step through every edit of the visit, the hotspot, the texts and the instructions included; a text being typed joins the history when you leave its box. After a save, Undo and Redo no longer reach a version that named a picture the save took away (a step added and then undone before the save): such a version could never be saved again.
12. **Play** opens the player at the selected step. With unsaved changes it asks first ("Save before playing?" with **Save and play**): the player shows what was saved.

Leaving the builder with unsaved changes through the app's own links, or with Back and Forward, asks first ("Leave without saving?" - "The changes you made since the last save are lost if you leave now." - with **Leave** and **Cancel**). In a browser a reload or a closed tab asks through the browser's prompt. **Closing the desktop app's window does not ask: save first.**

If somebody else saved the same walkthrough since you opened it, Save is refused with "This walkthrough was changed elsewhere: reload it." and nothing is written; a **Reload** button beside the message loads the saved walkthrough, asking first when you have unsaved changes ("Reload the walkthrough?" - "Your unsaved changes are lost; the walkthrough as it was last saved is loaded." - with **Reload**), and the status line then reads **Reloaded**. If the walkthrough was deleted meanwhile, or is no longer yours, the builder and the player go back to the list with "That walkthrough is no longer there: it was deleted, or it is not yours."

### Play a walkthrough

Press **Play** on the list (from the first step) or in the builder (from the selected step).

- Click the **hotspot**, press **Next**, **→**, **Space** or **Enter** to go on. On the last step Next reads **Finish** and shows the end card: "That is the end", the walkthrough's name and its number of steps, with **Restart** and **Close**.
- A click on the picture **outside** the hotspot does not go on: it makes the hotspot pulse once more, a hint.
- **Back** or **←** goes back (from the end card, to the last step); **Home** and **End** go to the first and the last step.
- **Chapters** lists the chapters; the current one is marked; a chapter opens at its first step. With the menu open, **↑** and **↓** move within it, wrapping round, and **Enter** opens the chapter.
- **Transcript** in the header, or **T**, opens the transcript in a drawer over the picture - the same Markdown the builder shows - with **Copy** and **Save as…**; the keys for stepping still work under it.
- **Esc** closes what is open first - the transcript, then the Chapters menu - and otherwise leaves the player, as **Close** does: back to the builder when it was opened from there, else to the list.
- The end card's **Restart** starts again at the first step.
- A walkthrough with no steps shows "This walkthrough has no steps yet." with **Close**; the Play buttons are greyed out until there is one, so this is reached only by its address.

The hotspot is a button with a visible focus ring, labelled with the step's text (or "Next step" when it has none), and the tooltip and the card are dialogs named "Step 3 of 12" that are announced to a screen reader. The pulse is left out when the system asks for reduced motion.

### The browser edition

Open the same address in a browser: the list, the builder and the player are all there, and the Captures card lists the stills already taken and opens the annotation editor on them, so a walkthrough can be built, its stills annotated, and played from any machine. Taking a still needs the desktop app on the machine whose screen it is.

## What is to come

Sharing a walkthrough (a link, an embed snippet, an export as a self-contained HTML page) and rendering one as a narrated video, with each step's narration - its instruction, else its text - read in a chosen voice while the picture zooms to each hotspot, are the next phases. The player is written so that an export can carry it unchanged.

## Under the hood

**A step's picture is a copy.** Add step copies the picture the still shows at that moment - the same one rule that answers the Captures card, the download and Add to deck (`services.captures.image_path`: the annotated picture once there is one, else the still) - into the walkthrough's own folder. Nothing in a walkthrough reads the still again: deleting the still, or annotating it afterwards, never changes a step. Replace picture copies the still's current picture again. Each step records where its picture came from (`source`: the still's id and whether it was annotated at the time), for information only.

**The files.** A walkthrough lives in `data\walkthroughs\<id>\`: `walkthrough.json` (the record: the id, the name, the owner, when it was made and last saved, the revision, the steps) and one `step-<id>.png` per step beside it. Back the folder up with the rest ([Data and backup](../admin/data-and-backup.md)). A walkthrough belongs to whoever made it, like a project: an editor sees their own, an administrator sees everyone's; Duplicate makes a copy owned by whoever pressed it.

**The save is whole and versioned.** Save sends the whole document - the name and the steps, never a picture - with the revision the builder loaded. The server checks the document against the pictures the folder holds (every step names its own picture, `step-<its id>.png`, there at the size it really is; the hotspot in whole pixels, inside the picture, at least 8 × 8; the texts within their limits; the ids unique), refuses it with the reason and writes nothing when it fails, refuses a revision that is not the record's with the "changed elsewhere" line, and otherwise writes the record with the revision raised by one, then removes the pictures no step names any more. Every write to one walkthrough - a save, an added step, a replaced picture, a copy and a delete - runs under that walkthrough's own lock, so two cannot interleave and a delete waits for a save in flight. Adding a step and replacing a picture are the server's own writes and raise the revision too; the builder takes each answer as its new saved state and keeps your other unsaved edits. Duplicate makes a new record at revision 1 and leaves the original as it is. The builder compares what you have with what was saved the way the server stores it - a trailing space in a name or a chapter is no difference - so "Unsaved changes" clears the moment a save answers.

**The transcript** is written from the document by one rule on each side - the page's `narrationOf` and the server's `narration_of`: a step says its instruction when that is not blank, else its text - into the same Markdown, byte for byte (a test on each side holds the same fixture). `GET /api/walkthroughs/<id>/transcript` answers it as `text/markdown`, and as a download named after the walkthrough with `?download=1`; it is a read, so it leaves no audit row. The player's drawer is part of the player itself, built from the document it already holds, so an export of the player will carry it.

**The player** is one component that takes the document and a function from a step to its picture, and makes no request of its own; the page gives it the API's picture addresses. The picture's displayed rectangle is one calculation (fit inside the window, centred), the hotspot's is the same one applied to its rectangle, and the tooltip's place is another: the side asked, or for Auto the first of below, right, above, left with room for it (or, with room on none, the side with the most), centred on the hotspot and kept on screen.

**The audit log** records `walkthrough.create` (the name), `walkthrough.update` (the step and chapter counts, never a step's text), `walkthrough.add_step` (the still and the position), `walkthrough.replace_picture` (the step and the still), `walkthrough.duplicate` (which copy of which) and `walkthrough.delete` (the name) ([Audit log](../admin/audit-log.md)).

**The limits** - 200 steps, 1,000 characters of text, 2,000 of an instruction, 80 of a chapter name, 120 of a walkthrough's name, a hotspot of at least 8 × 8, a save of at most 2 MB - are in [Limits](../reference/limits.md).

## See also

- [Screen capture](capture.md): taking and annotating the stills
- [Keyboard shortcuts](../reference/keyboard-shortcuts.md): the builder's keys and the player's
- [Limits](../reference/limits.md)
- [Audit log](../admin/audit-log.md)
- [Data and backup](../admin/data-and-backup.md): the `walkthroughs` folder
