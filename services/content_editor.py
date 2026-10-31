"""The Content Editor's publish API, called thinly (the Workshop video canvas's last stage, C6): Media Studio never runs
git on the courses itself - the Pentaho Content Editor (``Pentaho-Content-Editor/api/routers/publish.py``) is the one
publisher, and these three calls are its own routes, reached at the Studio setting **Content Editor address**
(``services.studio_settings.content_editor_url``: the editor's uvicorn default ``http://localhost:8000`` unless set).

- :func:`config` - ``GET /api/publish/config`` → ``{url, ref}``: the distribution repository and its branch; the
  probe that says the editor is running.
- :func:`diff` - ``GET /api/courses/{course}/publish/diff`` → what a publish would change upstream (``added``,
  ``modified``, ``removed``, ``skippedIgnored``, ``upToDate``, ``newCourse``, ``remoteCommit``).
- :func:`publish` - ``POST /api/courses/{course}/publish`` with ``{message, commit}`` → the editor copies the course
  into its clone of the PUBLIC ``Pentaho-Courses`` repository, commits and pushes (``commit: true`` commits the
  authoring repository too); the answer carries ``ok``, ``upToDate``, ``commit``, ``changed``, ``authoring``.

Every call goes over ``httpx`` with :data:`TIMEOUT_S`, through a transport a test replaces (:data:`TRANSPORT`, an
``httpx.MockTransport``; no test ever reaches a running editor). A connection that fails is :class:`ContentEditorDown`
with ONE plain line naming the address; an answer of 400 or more is :class:`ContentEditorError` carrying the editor's
own ``detail`` text and its status, which the proxy route answers as it is.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from services.studio_settings import content_editor_url
from utils.httpx_ssl import shared_ssl_context

#: One call to the editor waits this long (a publish freshens a clone, copies the course, commits and pushes).
TIMEOUT_S = 30.0
#: The transport the client uses: None for the network; a test puts an ``httpx.MockTransport`` here.
TRANSPORT: httpx.BaseTransport | None = None

NOT_RUNNING_MESSAGE = "The Content Editor is not running at {address}: start it, then press Publish again."
#: The commit message the stage prefills (the editor's own default is "Update <course> from the Content Editor").
DEFAULT_MESSAGE = "Walkthrough video: {title}"


class ContentEditorDown(RuntimeError):
    """The editor could not be reached at its address (nothing listening, a refused connection, a timeout)."""


class ContentEditorError(RuntimeError):
    """The editor answered an error: ``str(exc)`` is its own detail text, ``status`` its HTTP status."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def _detail(response: httpx.Response) -> str:
    """The editor's own words for a refusal: its ``detail``, else the body's first line, else the status."""
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict) and data.get("detail"):
        detail = data["detail"]
        return detail if isinstance(detail, str) else str(detail)
    text = (response.text or "").strip().splitlines()
    return text[0][:300] if text else f"The Content Editor answered HTTP {response.status_code}."


def _call(method: str, path: str, json: dict | None = None) -> dict:
    """One call at the address in force; the editor's JSON answer."""
    address = content_editor_url()
    try:
        # One SSL context per process (utils.httpx_ssl): building one per call cost seconds on the owner's machine.
        with httpx.Client(base_url=address, timeout=httpx.Timeout(TIMEOUT_S), transport=TRANSPORT, verify=shared_ssl_context()) as client:
            response = client.request(method, path, json=json)
    except httpx.HTTPError:
        raise ContentEditorDown(NOT_RUNNING_MESSAGE.format(address=address)) from None
    if response.status_code >= 400:
        raise ContentEditorError(_detail(response), response.status_code)
    try:
        data = response.json()
    except ValueError:
        raise ContentEditorError(f"The Content Editor answered something other than JSON at {address}{path}.", 502) from None
    return data if isinstance(data, dict) else {"answer": data}


def config() -> dict:
    """``GET /api/publish/config``: the distribution repository's ``url`` and ``ref``."""
    return _call("GET", "/api/publish/config")


def diff(course: str) -> dict:
    """``GET /api/courses/{course}/publish/diff``: what publishing the course would change upstream."""
    return _call("GET", f"/api/courses/{quote(course, safe='')}/publish/diff")


def publish(course: str, message: str, *, commit: bool = True) -> dict:
    """``POST /api/courses/{course}/publish`` with ``{message, commit}``: the editor commits and pushes."""
    body: dict = {"commit": bool(commit)}
    text = (message or "").strip()
    if text:
        body["message"] = text
    return _call("POST", f"/api/courses/{quote(course, safe='')}/publish", json=body)


# -- the lines ---------------------------------------------------------------------------------------------------------

def _files(answer: dict, key: str) -> list[str]:
    value = answer.get(key)
    return [str(x) for x in value] if isinstance(value, list) else []


def diff_summary(answer: dict) -> str:
    """One line of the diff: "2 files change upstream: 01-first-win/guide.md, 01-first-win/manifest.json" (the first
    six named, "and N more" past them), "A new course: 12 files go upstream." for a course not in the repository yet,
    "Nothing to publish: the repository already holds the course as it is." when nothing changes."""
    changed = _files(answer, "added") + _files(answer, "modified") + _files(answer, "removed")
    if answer.get("newCourse") and changed:
        return f"A new course: {len(changed)} file{'' if len(changed) == 1 else 's'} go{'es' if len(changed) == 1 else ''} upstream."
    if not changed:
        return "Nothing to publish: the repository already holds the course as it is."
    shown = ", ".join(changed[:6]) + (f" and {len(changed) - 6} more" if len(changed) > 6 else "")
    return f"{len(changed)} file{'' if len(changed) == 1 else 's'} change{'s' if len(changed) == 1 else ''} upstream: {shown}"


def publish_summary(answer: dict) -> str:
    """One line of the editor's answer to a publish: "committed abc1234 and pushed (2 files)" or "nothing to publish:
    the repository was up to date" - with ", the authoring repository committed too" when the editor committed it."""
    commit = str(answer.get("commit") or "")[:7]
    if answer.get("upToDate"):
        line = "nothing to publish: the repository was up to date"
    else:
        changed = answer.get("changed") if isinstance(answer.get("changed"), dict) else {}
        total = sum(int(changed.get(k) or 0) for k in ("added", "modified", "removed"))
        files = f" ({total} file{'' if total == 1 else 's'})" if total else ""
        line = f"committed {commit} and pushed{files}" if commit else f"committed and pushed{files}"
    authoring = answer.get("authoring")
    if isinstance(authoring, dict) and authoring.get("committed"):
        line += ", the authoring repository committed too"
    return line
