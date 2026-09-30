# The slide editor

The **Slides** card of a deck or a PDF project is where the narration is written: each slide's speaker notes, and the voice and pause the render will use for it.

## What you see

The card's subtitle counts the slides and how many have notes that differ from the deck's own ("12 slides · notes edited on 3"). Its header offers **Render slide previews** while the images are not there yet, **Render again** when previews exist but the app does not know how they were made, **Export .pptx with notes** on a deck, and **Save all**, which reads **Save all (n)** while n slides carry unsaved text.

Above the rail sits the AI bar, which [The AI assistant](ai-assistant.md) describes.

**The rail** on the left is one thumbnail per slide, or a placeholder until the previews are rendered. Under each: the slide number, a dot when the slide has notes (coloured while they are unsaved), a pencil when the saved notes differ from the deck's own, a sparkle when the AI wrote them, a **QA n** badge for open review issues, and **Animated** on a slide PowerPoint exported as a clip.

**The editor** on the right shows the slide's image ("Not rendered yet" until it is), its label ("Slide 3 · its title"), an **AI** badge on AI-written notes, and the deck's own body text, collapsed behind **Show more** past 280 characters. Then the **Speaker notes** box, with a count of undo steps and, after a per-slide AI Enhance, "AI proposal — Save to keep, Revert to discard". Under it: **Save**, **Undo**, **Reset**, **AI Enhance**, **Revert** (only while a proposal is in the box), and the hint "← → move between slides · Ctrl+S saves". After a QA review, that slide's issues are listed with a **Fix** for each. Finally **Voice override** (**Studio default**, or one of the narration provider's voices) and **Pause after slide (s)**, blank for the render's own pause between slides.

## What to do

**Render the previews.** Press **Render slide previews**. A deck goes through PowerPoint on the server, a minute or two for a large deck; a PDF's pages render in seconds. Without PowerPoint the previews show each slide's title only, and the card says so.

**Write the notes.** Type in **Speaker notes**; the dot on the thumbnail changes while the text is unsaved. **Save** (or Ctrl+S in the box) saves this slide; **Save all** saves every slide with unsaved text in one request. Moving between slides never loses a draft. A slide with no notes shows for a few seconds in the video without narration.

**Undo.** Restores the text before the last saved edit. It is disabled while the slide has an unsaved draft: save or reset first. Each slide keeps its own history of twenty steps.

**Reset.** Puts the deck's own notes back (a PDF has none, so the notes are cleared) after a confirmation, **Reset the notes of slide N?**. The text you are replacing stays in the undo history; an unsaved draft on the slide is discarded.

**Voice and pause.** Choose a **Voice override** to narrate this slide in a voice other than the one chosen on the Generate card; it saves as soon as it changes. Type a **Pause after slide (s)** of 0 to 30 seconds and leave the box (or press Enter); it saves then. Blank means the render's default pause between slides.

**Export the deck.** **Export .pptx with notes** downloads a copy of the deck with every slide's notes replaced by the edited ones. Decks only.

**Keys.** ← and → change slide whenever the focus is not in a box and no dialog is open, and the slide list scrolls to keep the selected slide in view; Ctrl+S in the notes box saves.

## Under the hood

The editor reads and writes the engine's own project for the deck, `data\projects\<id>\<stem>_project\project.json`, which is created the first time the Slides card opens. Speaker notes are limited to 20,000 characters and the pause to 0–30 seconds; a voice override is checked against the provider it belongs to, so an Edge voice cannot be saved under Kokoro or the other way round. Each save is one request for one slide (Save all is one request for several); a save is a deliberate act (the button, Ctrl+S, a dropdown, or leaving the pause box), never a keystroke. Each is recorded in the audit log: a one-slide save as `slides.update` with the slide and the names of the fields the request carried, **Save all** as `slides.bulk_update` with the number of slides.

The previews are `slide_NNN.png` (a deck) or `page_NNN.png` (a PDF) in the inner project's image folder. Rendering them is a job of kind `render-slides`; a second request while one runs joins it rather than starting another. The app records where the images came from, PowerPoint, the title-only fallback, or the PDF's pages, because the AI assistant shows the model a slide's image only when it is a real render. Previews rendered before that record existed have no recorded source, which is what **Render again** fixes.

While any job holds the project (a video render, the previews, an AI action), whoever started it, the notes box is read-only and every save is refused. The job carries its own copy of the slides and would write over the edit.

The export lands at `data\projects\<id>\exports\<name>-notes.pptx` and is rebuilt on every download.

## See also

- [The AI assistant](ai-assistant.md): the bar above the rail
- [Generate a video](generate-video.md): turning the notes into narration
- [Jobs](jobs.md): why the editor is read-only during a job
