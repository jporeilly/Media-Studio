# Projects

The Projects page is where every deck, PDF and video enters the studio: import a file, open it, and delete it when it is done with.

## What you see

**Import** in the page header opens a file picker that accepts `.pptx`, `.pdf`, `.mp4`, `.mov`, `.mkv`, `.avi`, `.webm` and `.m4v`. On a page with no projects the header button is replaced by **Import a file** in the centre, under "No projects yet". A status line reports the import ("Imported "name".") or the refusal, with a **Dismiss** cross.

The table has one row per project: **Name** (a link to the project, with the source file's name under it), **Type** (Deck, PDF or Video), **Slides** (the number of slides or pages; a dash for a video, and for a PDF until it has been opened), **Size**, **Owner** (administrators only), **Imported** (how long ago), and a **Delete project** button at the end of the row.

In the Owner column a project imported before the app had owners shows **unassigned**, with a tooltip saying only administrators can see it.

## What to do

**Import.** Press **Import**, choose one file, wait for the status line. A file over 2 GB, an empty file, or a file whose extension is not in the list is refused before anything is stored; the refusal of a wrong type names the accepted extensions.

**Open.** Click the name. Every feature lives on the project page.

**Delete.** Press the bin at the end of the row. The confirmation, **Delete project**, says "Delete *name*? This removes its files." The delete removes the project's whole directory: the source file, the transcript, every render and every export. There is no undo and no recycle bin.

**A project busy with a job.** Each project runs one job at a time: a transcription, a render, a re-voice, the slide previews or an AI action. While one is queued or running, every other job and every change to the project's content is refused with "A job is running for this project (*kind*). Wait for it to finish, then try again." Reads still work: you can look at the slides, play a sentence, download the transcript. Deleting the project is not held by the job (on Windows it fails while the job has a file open). See [Jobs](jobs.md).

## Under the hood

A project is a directory, `data\projects\<id>\`, named by a twelve-character hexadecimal id, holding the uploaded file exactly as it was sent and a `project.json` record: the name (the file's stem), the kind, the source filename, the size, a deck's slide count, the import time, and the owner's id and display name. Everything the project gains later, the transcript, the edit, the renders and the inner slide-editor project, lands in the same directory, so a project can be copied or zipped as one folder.

Ownership is decided in one place. An administrator may see and act on every project; an editor only on projects they imported; a project with no owner (imported before ownership existed) is treated as administrator-owned. The list you see is already filtered, and a direct request for someone else's project is refused.

A delete first renames the directory out of the id space, then removes it. On Windows a directory cannot be renamed while a file inside it is open, so a project whose video is open in another program is refused whole, nothing half-removed, with the names of the files in use; close them and try again.

The import and the delete are recorded in the audit log as `project.import` and `project.delete`.

## See also

- [Jobs](jobs.md): one job per project, and what that refuses
- [Accounts and roles](../admin/accounts-and-roles.md): who sees which projects
- [Data and backup](../admin/data-and-backup.md): the project directory in full
