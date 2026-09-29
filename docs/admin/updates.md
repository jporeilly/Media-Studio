# Updates

The **Updates** card on the Settings page checks the app's Git remote for newer commits and, for an administrator, pulls them in place and restarts the backend.

## What you see

**Installed** (the commit), **Branch**, and **Latest upstream** once a check has run; then "An update is available — N new commits on *branch*." or "You're on the latest version." Buttons: **Check for updates** for everyone; **Update now** (only while an update is available) and **Restart backend** for administrators. During an update the card shows the job's message and a progress bar; when it is done: "Update applied (*a* → *b*). Restart to finish." and **Restart now**. While restarting: "Restarting the backend…", then "Back online — reloading…" and the page reloads itself. After four minutes without an answer, an error box says the backend has not come back and offers **Keep waiting**. A footnote explains that updates pull the latest commits from the install's Git repository and reinstall Python dependencies in place.

When the install cannot update itself the card shows the reason instead: "git is not installed on this machine, so the app cannot self-update.", "This install is not a Git checkout, so the app cannot self-update.", "Could not reach the Git remote: …", or "No upstream branch to compare against: …".

## What to do

1. Open Settings. The check runs on every visit.
2. When an update is available and you are an administrator, press **Update now** and wait for the progress bar.
3. Press **Restart now**. The page waits for the backend and reloads with the new code; the version in the sidebar footer changes when the update carried a new version number.
4. If the backend has not come back after four minutes, relaunch the app (or restart the server) and return to Settings to confirm the installed version.

**Restart backend** on its own relaunches the backend without an update, for instance after installing the GPU libraries.

## Under the hood

**Check for updates** runs `git fetch` in the app's directory with a 45-second limit and counts the commits between the installed commit and its upstream. **Update now** is a job of kind `update` (it belongs to no project) that runs `git pull --ff-only` (up to 300 seconds), then `pip install -q -r requirements.txt` into the interpreter that is running the app (the bundled Python in a desktop install, the venv from a checkout; up to 30 minutes), then removes untracked files under `frontend/dist` so a browser cannot load a stale UI from leftover chunks. **Restart backend** and **Restart now** answer the request, wait three quarters of a second, and relaunches the same interpreter with the same arguments; the page polls the health endpoint every 1.5 seconds and reloads when it answers.

What it needs: the app directory must be a Git checkout (a `.git` folder), `git` must be on the machine's PATH, and the machine's Git credentials must be able to read the remote. Git runs with prompts disabled, so a machine without stored credentials fails at once with "Could not reach the Git remote" instead of hanging. The fast-forward refuses when the update touches a file that was edited by hand under the checkout, or when the local branch has commits upstream does not (it has diverged); the card then shows "git pull failed: …" with Git's message. A hand edit to a file the update does not touch is left alone.

What an update cannot deliver: the Python interpreter itself, the desktop window, and the `ffmpeg` and `ffprobe` binaries in `app\bin\` (those come with a new installer, as INSTALL.md explains), nor anything under `data\` or `assets\`, which are yours. An update changes the version the app reports; the desktop installer's entry in Add or remove programs keeps the installer's.

Both actions are recorded in the audit log: `system.update` with the job id, and `system.restart`, written before the restart, since the process is gone after it.

## See also

- [INSTALL.md](../../INSTALL.md): updating a desktop install, and what needs a new installer
- [Troubleshooting](troubleshooting.md)
- [Audit log](audit-log.md)
