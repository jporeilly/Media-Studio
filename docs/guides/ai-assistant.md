# The AI assistant

The AI bar on the Slides card puts an Ollama model to work on the speaker notes: it writes, rewrites, reviews, translates and paces them, and drafts a Q&A document.

Nothing goes to a cloud service: the notes go only to the Ollama server the studio is configured with (by default `http://localhost:11434`, on this machine).

## What you see

**The status line** says whether the model is ready: "Checking Ollama…", "Ollama unreachable at *url* — AI actions disabled", "*model* is not pulled on the Ollama server (ollama pull *model*) — AI actions will fail until it is", "*model* · vision" when the model can see the slide images, or "*model* · vision off: *reason*". After a review it adds "QA score n/10 · n open issues · *when*".

**The buttons**, left to right: **Generate notes (n)** for the n slides without any; **Enhance all**; **QA review**; **Tone**; **Translate**; **Pacing**; then **Analyze**, **Q&A doc** and, once one exists, **Download Q&A doc**. Each of the first six opens a dialog or starts a job; while a job runs the card shows its progress and, for every kind but the Q&A document, a **Cancel** button. When a job finishes, a result line summarises it ("Enhanced 12 of 12 slides", "QA score 8/10 — 3 issues on 12 slides", "Q&A document ready: 10 questions") with a **Download** where there is a file and a cross to dismiss it; when a job fails, the card shows its error instead.

In the editor, **AI Enhance** asks for a rewrite of one slide's notes and **Revert** discards the proposal. Under the notes, a reviewed slide lists its issues by criterion, Grammar, Tone, Flow and Transitions, each with **Fix**, which becomes **Fixed** once applied.

## What to do

**Generate notes.** The dialog **Generate speaker notes** writes notes for the slides without any, from each slide's title and text, and its image when **Show the model each slide's image** is on. Slides that already have notes are left alone.

**Enhance all.** **Enhance all notes** rewrites every slide's notes for natural narration; a slide without notes gets them from its content.

**QA review.** Starts at once. Every slide with notes is reviewed for grammar, tone, flow and transitions in passes of up to twenty slides. When it finishes the score appears in the status line, each thumbnail with issues carries a **QA n** badge, and each issue has a **Fix** under the notes. **Fix** asks the model to change only that one thing, saves the result and marks the row fixed.

**Tone.** **Adapt the tone** rewrites every note for an audience: **Technical**, **Executive**, **Student**, **Sales**, **Casual**, **Formal**, or **Custom** with your own **Instruction** (up to 2,000 characters).

**Translate.** **Translate the notes** rewrites every note into the **Target language** (Spanish, French, German, Italian, Portuguese, Dutch, Polish, Russian, Japanese, Korean, Chinese (Simplified), Arabic, Hindi or English) in place. With **Switch narration to a matching voice** on, the Generate card's voice changes to the first voice of the narration provider in that language when the job finishes; the dialog names it beforehand, or says the provider has none. Export a copy of the deck first if you want to keep the original language.

**Pacing.** **Narration pacing** inserts pauses (`...`) after transition phrases, before statistics and around questions. **Apply the rules** does it at once with no model; **Pace with the model** asks the model per slide and falls back to the rules where the answer loses words.

**Analyze.** Scores the deck for video (text density, notes coverage, visuals) and opens **Slide analysis**: the overall score, a summary, the model's suggestions when Ollama answers (the rules alone otherwise, and the dialog says so), and a score per slide.

**Q&A doc.** **Q&A document** writes anticipated audience questions with answers, 1 to 50 of them (10 by default), from the notes, as a text file you download from the result line or with **Download Q&A doc**.

**The vision checkbox.** Present in the notes and enhance dialogs. It is enabled only when the configured model is a vision model *and* this project's previews are real renders; otherwise it is off with the reason beside it: render the previews, or **Render again**, or choose a vision model.

**Cancel.** Stops a per-slide job after the slide the model is working on, keeping what was written; stops a QA review between passes, saving nothing. The Q&A document is one prompt and cannot be cancelled.

**Unsaved drafts.** Generate notes, Enhance all, Tone, Translate and Pacing work on the saved notes and would bury a draft under their result, so they wait until **Save all** has saved the drafts. There is no button that discards drafts; reloading the page drops them. The review, Analyze and the Q&A document do not wait.

## Under the hood

Generate notes, Enhance all, QA review, Tone, Translate, Pace with the model and the Q&A document are jobs (`ai-notes`, `ai-enhance`, `ai-qa`, `ai-tone`, `ai-translate`, `ai-pacing`, `ai-qa-doc`) attached to the project, so the editor is read-only while it runs and no other job can start. The request is validated first (a bad value is refused, an unreachable Ollama is a 503) and no job exists for a request that cannot run. A job warms the model with one short call. The five per-slide jobs (notes, enhance, tone, translate, pacing with the model) then make one call per slide, save each answer as it arrives, count a failed slide rather than stopping, and check the cancel flag between slides; only Ollama going away stops such a loop early. Each rewritten slide's previous text goes onto its undo history, and the slide is flagged as AI-written. The QA review makes one call per pass and fails when a pass fails; the Q&A document is one call.

Timeouts: 3 seconds to probe the server, 120 seconds per slide, 180 seconds per review pass, 60 seconds for the Q&A document, 30 seconds for Analyze's suggestions. A review pass carries at most 20 slides and 12,000 characters of notes; its answer is mapped back onto the slides by the slide numbers it returns and refused whole when they do not match. The review is saved on the project only when every pass is in; a cancel or a failed pass keeps the previous review.

Vision is offered when the model's family is one of gemma3, llava, moondream, llama3.2-vision, qwen2.5vl, qwen2-vl, minicpm-v, llava-llama3 or bakllava, and the project's previews were made by PowerPoint or from a PDF. The title-only fallback is never shown to a model: a white card with a title reads as an empty slide.

The model-paced version of Pacing keeps an answer only when it is at least 80 % of the note's length; otherwise the rules place the pauses. The Q&A document lands at `data\projects\<id>\exports\<name>-qa.txt`. Analyze and the per-slide AI Enhance are synchronous calls, not jobs; a per-slide proposal is not saved until you press Save.

The model and its URL come from the studio settings and `data\config.json`; see [Ollama](../ai/ollama.md). The jobs, **Apply the rules** and a **Fix** are recorded in the audit log (`ai.notes`, `ai.enhance`, `ai.qa`, `ai.tone`, `ai.translate`, `ai.pacing`, `ai.qa_doc`, `ai.qa_fix`), never with the text: a job with its job id, **Apply the rules** as `ai.pacing` with "rules", a Fix with the slide and the criterion. Analyze and the per-slide AI Enhance save nothing and write no row; saving a proposal is recorded as `slides.update`.

## See also

- [Ollama](../ai/ollama.md): the model, the URL, vision models and the status line
- [The slide editor](slide-editor.md): Save, Undo, Reset and Revert
- [Jobs](jobs.md): cancelling, and one job per project
