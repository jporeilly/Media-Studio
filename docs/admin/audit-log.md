# Audit log

The **Audit log** card on the Settings page shows who changed what through the app, from sign-ins to slide edits, newest first; administrators only.

## What you see

Three selects and two buttons across the top: **Filter by action** (All actions, or one of the actions below), **Filter by user** (All users, or an account), **Number of entries** (Latest 50, 100, 250 or 1000), **Clear filters** while a filter is set, and **Refresh**. Then the table: **When** (how long ago; the exact time in the tooltip), **User** (the account's display name, else its username, as it was when the entry was written; for a failed sign-in, the username that was tried, with a tooltip saying no account is behind the entry; "system" when an entry has no name at all), **Action**, **Entity** (with its id), **Detail**. At the foot: "Retention is yours to set:", a select (**older than 30 days**, **older than 90 days**, **older than 365 days**) and **Purge**, which confirms with "Delete every audit entry older than N days? This cannot be undone." and then reports how many entries went.

## The actions

| Action | Recorded when | Detail |
| --- | --- | --- |
| `auth.login`, `auth.login_failed`, `auth.logout`, `auth.password_change` | a sign-in, a failed sign-in (the username tried, no user), a sign-out, a password change | none |
| `user.create`, `user.update`, `user.reset_password` | an account is added, edited (display name, role, active) or has its password reset | the username and role; "username: field names"; the username |
| `settings.update`, `settings.password_policy` | the Studio card or the Password policy card is saved | key names, never a value: for the Studio card the keys the save changed, for the policy all six keys on every save |
| `project.import`, `project.delete` | a file is imported; a project is deleted | the kind and file name; the project name |
| `project.transcribe`, `project.transcript_edit`, `project.transcript_timing` | a transcription starts; the words are saved; a sentence's offset, mute, voice or speed changes | the job id; the sentence count and any adjustments dropped; the sentence index or indices and the field names ("segment 3: muted", "segments 3, 4, 5: offset") |
| `project.generate`, `project.revoice` | a render or a re-voice starts | the job id, the preset ("preview" for a preview) or the language, the provider and voice |
| `project.edit` | the Timeline's cut, split, trim, music or markers are saved, or the edit is cleared | per track the range count and seconds removed, the clip count, the marker count; never a time, a file or a name |
| `music.upload`, `music.delete` | a file joins or leaves the studio's music library | the name and size; the name |
| `slides.update`, `slides.bulk_update`, `slides.render`, `slides.undo`, `slides.reset` | one slide is saved (notes, voice, pause); Save all; the previews are rendered; Undo; Reset | "slide N: field names"; "N slides"; the job id, with ", forced" for **Render again**; "slide N"; "slide N" |
| `ai.notes`, `ai.enhance`, `ai.qa`, `ai.tone`, `ai.translate`, `ai.pacing`, `ai.qa_doc`, `ai.qa_fix` | an AI action starts; **Apply the rules** in Pacing; a QA fix is applied | the job id, with the scope (notes, enhance), the tone or the language; `ai.qa` the job id only; `ai.pacing` "job …, model", or "rules" for the rules; `ai.qa_doc` "job …, N questions"; `ai.qa_fix` "slide N, criterion" |
| `job.cancel` | a job is asked to stop | the job kind |
| `system.update`, `system.restart` | an update is applied; the backend is restarted | the update's job id; none |
| `audit.purge` | the log is purged | the window and how many entries went |

## What to do

Filter by an action or a user to answer "who changed this"; the filters run on the server, so an old entry is found however many rows sit above it. Raise **Number of entries** when you need more history at once; 1000 is the most one read returns. Purge with the window you want to keep: **Purge** deletes everything older than the chosen number of days and then records itself, so the purge is the newest entry after it.

## Under the hood

Entries live in the `audit_log` table of `data\media_studio.db`, indexed by time, action and user. Every mutating endpoint writes one except two that save nothing, Analyze and the per-slide AI Enhance (its proposal is audited as `slides.update` if you save it), and a test fails the build when a new endpoint writes none; the actions are named constants, so a typo cannot create a row nobody can filter for. A failed audit write is logged and dropped: it never fails the user's request, because in this product the log is a convenience for administrators, not a compliance control. A read takes 1 to 1000 rows (100 when unspecified) and a request outside that is refused with a 422; a purge takes 1 to 3650 days. Details never carry a value that would be embarrassing to read back: settings record key names, a sentence adjustment records field names, an edit records counts.

## See also

- [Accounts and roles](accounts-and-roles.md)
- [Data and backup](data-and-backup.md): where the database lives
