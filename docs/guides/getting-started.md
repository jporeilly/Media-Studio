# Getting started

This guide takes you from the first launch to the page where the work happens: signing in, the shell, and the three kinds of project the studio works on.

Media Studio Enterprise turns a slide deck or a PDF into a narrated video, and gives a video a new voice, a translation, and an edit with music and chapters.

## What you see

### Sign in

The sign-in page asks for a **Username** and a **Password** and has one button, **Sign in**. Under it: "First run: admin / admin (change it on first login)."

On a fresh install the app creates one account, `admin` with the password `admin`, as an administrator that must change its password. Sign in with it and the next page is **Set a new password**: **Current password**, **New password** (with the password rules beside it), **Confirm new password**, and **Save**. **Log out** is the other way off the page. Until the password is changed the server refuses every other signed-in request, from the app and from a script alike: it answers only the sign-in, sign-out, who-am-I and change-password requests and a read of the password rules (and, as always, the health check, which needs no sign-in).

### The shell

Once you are in, the sidebar lists **Dashboard**, **Projects**, **Docs** and **Settings**, then your avatar, display name and role, and at the foot the version the server reports. The top bar has three controls: **Accent colour** (Teal, Slate, Blue, Indigo, Purple, Green, Orange or Rose), **Toggle dark / light**, and **Log out**. The theme starts dark and Teal and is remembered in this browser, not on your account.

### The dashboard

The dashboard is four tiles, **Projects**, **Import Video**, **Generate Video** and **Translate**, and every tile's **Open** goes to the Projects page, because each of those things happens inside a project. In the desktop app a fifth tile, **Capture**, opens the Projects page with the Capture dialog ready ([Screen capture](capture.md)). An error box appears here only when the API does not answer.

### The three kinds of project

| Kind | Imported from | What the project page offers |
| --- | --- | --- |
| **Deck** | `.pptx` | The **Slides** card (previews, speaker notes, per-slide voice and pause, the AI assistant, **Export .pptx with notes**) and the **Generate video** card |
| **PDF** | `.pdf` | The same Slides and Generate video cards; a PDF has no notes of its own and cannot be exported as a deck |
| **Video** | `.mp4`, `.mov`, `.mkv`, `.avi`, `.webm`, `.m4v` | The **Transcript** card (the List and the Timeline) and the **Re-voice** card |

## What to do

1. Sign in as `admin` / `admin` and set a password of your own.
2. Open **Projects** and press **Import** (or **Import a file** on an empty page). Pick a deck, a PDF or a video.
3. Open the project from the table. A deck or a PDF opens on its slides; a video opens ready to transcribe.
4. When you are an administrator, visit **Settings**: the **Studio** card holds the narration provider and voices everyone starts from, and the **Accounts** card is where the rest of the team gets its accounts.

## Under the hood

Signing in sets a session cookie, `ms_session`, that lasts seven days; a deactivated account's session stops working at its next request. Two roles exist, `admin` and `editor`: editors do all the studio work on their own projects; administrators see every project and also manage accounts, the password rules, the studio settings, the audit log and updates. Every sign-in, failed sign-in and password change is recorded in the audit log.

## See also

- [Projects](projects.md): importing, the table, ownership and deleting
- [The slide editor](slide-editor.md), [The AI assistant](ai-assistant.md) and [Generate a video](generate-video.md) for a deck or a PDF
- [Transcribing and the transcript](transcript.md), [Re-voicing a video](re-voice.md) and [The Timeline](timeline.md) for a video
- [Accounts and roles](../admin/accounts-and-roles.md) and [Studio settings](../admin/studio-settings.md) for administrators
- [INSTALL.md](../../INSTALL.md) for installing, updating and where the data lives
