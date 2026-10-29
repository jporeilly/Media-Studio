"""Publish to Vimeo (V2): a rendered project video uploaded to Vimeo with the tus protocol, polled until Vimeo has
processed it, and answered as the record the project keeps.

**The token** is a Studio setting (``services.studio_settings.vimeo_token``: the stored value, or the environment's
``MEDIA_STUDIO_VIMEO_TOKEN`` over it) handed in by the caller; it goes into the ``Authorization`` header and
nowhere else - never into a log line, an audit row, an error message or a result (:func:`scrub` takes it out of
any message that might echo it).

**The calls**, every one over ``httpx`` with a timeout, through ONE :class:`VimeoClient` whose transport a test
replaces (:data:`TRANSPORT`, an ``httpx.MockTransport``): ``POST /me/videos`` with ``upload.approach: "tus"``, the
size, the name, the description and ``privacy.view`` (our ``private`` is Vimeo's ``nobody``) with ``privacy.embed:
"public"`` so a course can embed it; the tus upload in :data:`VIMEO_CHUNK_BYTES` PATCHes carrying
``Upload-Offset``, a failed chunk resumed from the offset ``HEAD`` answers, up to :data:`VIMEO_CHUNK_TRIES` tries;
then ``GET /videos/{id}?fields=…`` every :data:`VIMEO_TRANSCODE_POLL_S` seconds until ``transcode.status`` is
``complete`` - ``error`` fails the job with Vimeo's words - bounded by :data:`VIMEO_TRANSCODE_WAIT_S`. A 429 is
retried after ``Retry-After`` (at most :data:`VIMEO_RETRY_AFTER_MAX_S` a wait, :data:`VIMEO_RATE_TRIES` tries); 401
is the token; 403 or 400 on a privacy other than ``anybody`` is the account (Unlisted needs Vimeo Plus or higher).

**Cancel** is checked before every chunk and before every poll (:func:`publish` takes the job's cancel check); a
cancelled publish deletes the video on Vimeo (``DELETE /videos/{id}``) when its id is known and answers None.

**The record** (:func:`vimeo_record`): ``{id, link, hash, player_url, privacy, published_at, title}`` - the hash
parsed from an Unlisted video's link (``https://vimeo.com/<id>/<hash>``; the content manager needs it to embed
such a video), the privacy in our words.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

from utils.logger import get_logger

log = get_logger("VIMEO")

VIMEO_API = "https://api.vimeo.com"
VIMEO_ACCEPT = "application/vnd.vimeo.*+json;version=3.4"
#: The fields the poll asks for: what the record needs and nothing more.
VIDEO_FIELDS = "uri,link,transcode.status,privacy,player_embed_url"
#: One tus PATCH carries this much of the file.
VIMEO_CHUNK_BYTES = 50 * 1024 * 1024
#: A chunk is tried this many times (the offset read back with HEAD between them) before the publish fails.
VIMEO_CHUNK_TRIES = 3
#: How long Vimeo is given to process the upload, and how often it is asked.
VIMEO_TRANSCODE_WAIT_S = 1800
VIMEO_TRANSCODE_POLL_S = 5
#: A 429 is retried this many times, each after Retry-After seconds - never more than this many.
VIMEO_RATE_TRIES = 3
VIMEO_RETRY_AFTER_MAX_S = 60
#: The timeouts: an API call, and a chunk's upload.
VIMEO_API_TIMEOUT_S = 30.0
VIMEO_CHUNK_TIMEOUT_S = 300.0
#: Vimeo's own limits on a video's name and description.
VIMEO_NAME_MAX = 128
VIMEO_DESCRIPTION_MAX = 5000

#: The privacies the setting offers, in our words, with the labels and notes the Studio card shows.
PRIVACIES = {
    "unlisted": {"label": "Unlisted", "note": "Anyone with the link; the course embeds it with its hash. Needs Vimeo Plus or higher."},
    "private": {"label": "Private", "note": "Only you can see it on vimeo.com. Needs Vimeo Plus or higher."},
    "anybody": {"label": "Anybody", "note": "Public on vimeo.com; the one privacy a free account may set."},
}
DEFAULT_PRIVACY = "unlisted"
#: Our word -> Vimeo's ``privacy.view``.
_VIEW_OF = {"unlisted": "unlisted", "private": "nobody", "anybody": "anybody"}
_PRIVACY_OF_VIEW = {view: word for word, view in _VIEW_OF.items()}

NO_TOKEN_MESSAGE = "No Vimeo access token is set: an administrator adds one in Settings › Studio (Vimeo access token)."
NO_VIDEO_MESSAGE = "Generate the video first: this project has no rendered video to publish."
BAD_TOKEN_MESSAGE = ("Vimeo refused the access token (401): check the Vimeo access token in Settings › Studio - a personal access "
                     "token from developer.vimeo.com with the scopes public private upload edit video_files.")
PRIVACY_REFUSED_MESSAGE = ("Vimeo refused the privacy {label} ({status}): Unlisted and Private videos need Vimeo Plus or higher - "
                           "choose Anybody in Settings › Studio (Vimeo privacy) or upgrade the account. Vimeo said: {words}")
REFUSED_MESSAGE = "Vimeo refused the video ({status}): {words}"
CHUNK_FAILED_MESSAGE = "The upload failed at {offset} of {size} bytes after {tries} tries (the last answer: {status}); the unfinished video was removed from Vimeo."
RATE_LIMITED_MESSAGE = "Vimeo is rate-limiting this account (429) and still refused after {tries} tries: wait a while and publish again."
TRANSCODE_ERROR_MESSAGE = "Vimeo could not process the video: {words}"
TRANSCODE_WAIT_MESSAGE = ("Vimeo did not finish processing the video within {minutes} minutes; it is on Vimeo ({link}) and finishes "
                          "there. Look on vimeo.com, or publish again later.")
UNREACHABLE_MESSAGE = "Could not reach Vimeo: {reason}"
CANCELLED_MESSAGE = "Publish cancelled; the unfinished video was removed from Vimeo."

#: The transport the client uses: None for the network; a test puts an ``httpx.MockTransport`` here.
TRANSPORT: httpx.BaseTransport | None = None
#: The waits and the clock, replaceable by a test.
SLEEP: Callable[[float], None] = time.sleep
CLOCK: Callable[[], float] = time.monotonic

_VIDEO_ID = re.compile(r"/videos/(\d+)")
_LINK_HASH = re.compile(r"^https?://(?:www\.)?vimeo\.com/\d+/([A-Za-z0-9]+)/?$")


class VimeoError(RuntimeError):
    """A publish that cannot go on: the message is for the user, ``status`` the HTTP status when there was one."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class VimeoTimeout(VimeoError):
    """Vimeo did not finish processing within the wait: the video IS on Vimeo and is left there (the message names it)."""


def scrub(message: str, token: str) -> str:
    """``message`` with the token, should anything echo it, replaced by ``***``."""
    return message.replace(token, "***") if token else message


def video_id_of(uri: str) -> str:
    """``"123456789"`` from ``"/videos/123456789"``; ``ValueError`` for anything else."""
    match = _VIDEO_ID.search(uri or "")
    if not match:
        raise ValueError(f"Not a Vimeo video uri: {uri!r}")
    return match.group(1)


def hash_of(link: str) -> str | None:
    """An Unlisted video's hash from its link (``https://vimeo.com/<id>/<hash>``); None for a bare link."""
    match = _LINK_HASH.match((link or "").strip())
    return match.group(1) if match else None


def privacy_of_view(view: str | None, fallback: str) -> str:
    """Vimeo's ``privacy.view`` in our words (``nobody`` is ``private``); the requested word when it is none we know."""
    return _PRIVACY_OF_VIEW.get(view or "", fallback)


def cut_description(text: str) -> str:
    """The description within Vimeo's limit: cut at a word with an ellipsis past :data:`VIMEO_DESCRIPTION_MAX`."""
    text = text or ""
    if len(text) <= VIMEO_DESCRIPTION_MAX:
        return text
    cut = text[:VIMEO_DESCRIPTION_MAX - 1]
    space = cut.rfind(" ")
    if space > VIMEO_DESCRIPTION_MAX // 2:
        cut = cut[:space]
    return cut.rstrip() + "…"


def vimeo_record(video: dict, *, title: str, privacy: str) -> dict:
    """The record the project keeps of a published video."""
    link = str(video.get("link") or "")
    return {
        "id": video_id_of(str(video.get("uri") or "")),
        "link": link,
        "hash": hash_of(link),
        "player_url": video.get("player_embed_url") or None,
        "privacy": privacy_of_view((video.get("privacy") or {}).get("view"), privacy),
        "published_at": datetime.now(timezone.utc).isoformat(),
        "title": title,
    }


def _words(response: httpx.Response) -> str:
    """Vimeo's own words for a refusal: ``developer_message``, else ``error``, else the body's first line."""
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        for key in ("developer_message", "error", "error_code"):
            if data.get(key):
                return str(data[key])
    text = (response.text or "").strip().splitlines()
    return text[0][:200] if text else f"HTTP {response.status_code}"


def _megabytes(n: int) -> str:
    return f"{n / (1024 * 1024):.0f}"


class VimeoClient:
    """The calls, one method each, over one ``httpx.Client`` with the token in its headers."""

    def __init__(self, token: str, *, transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] | None = None):
        self._token = token
        self._sleep = sleep or SLEEP
        self._client = httpx.Client(
            transport=transport, timeout=httpx.Timeout(VIMEO_API_TIMEOUT_S),
            headers={"Authorization": f"bearer {token}", "Accept": VIMEO_ACCEPT},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "VimeoClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- the one request routine ---------------------------------------------------------------------------------

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """One call: a 429 is retried after Retry-After (bounded), a connection failure is a plain line."""
        for attempt in range(1, VIMEO_RATE_TRIES + 1):
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                raise VimeoError(UNREACHABLE_MESSAGE.format(reason=scrub(str(exc) or exc.__class__.__name__, self._token))) from None
            if response.status_code != 429:
                return response
            if attempt == VIMEO_RATE_TRIES:
                raise VimeoError(RATE_LIMITED_MESSAGE.format(tries=VIMEO_RATE_TRIES), 429)
            self._sleep(_retry_after(response))
        raise AssertionError("unreachable")  # pragma: no cover

    # -- the calls ----------------------------------------------------------------------------------------------------

    def create_video(self, *, size: int, name: str, description: str, privacy: str) -> dict:
        """``POST /me/videos`` with the tus approach: the id, the link, the upload link and the player url."""
        body = {
            "upload": {"approach": "tus", "size": str(int(size))},
            "name": (name or "")[:VIMEO_NAME_MAX],
            "description": (description or "")[:VIMEO_DESCRIPTION_MAX],
            "privacy": {"view": _VIEW_OF.get(privacy, _VIEW_OF[DEFAULT_PRIVACY]), "embed": "public"},
        }
        response = self._request("POST", f"{VIMEO_API}/me/videos", json=body, headers={"Content-Type": "application/json"})
        if response.status_code == 401:
            raise VimeoError(BAD_TOKEN_MESSAGE, 401)
        if response.status_code in (400, 403) and privacy != "anybody":
            label = PRIVACIES.get(privacy, {}).get("label", privacy)
            raise VimeoError(PRIVACY_REFUSED_MESSAGE.format(label=label, status=response.status_code, words=_words(response)), response.status_code)
        if response.status_code >= 400:
            raise VimeoError(REFUSED_MESSAGE.format(status=response.status_code, words=_words(response)), response.status_code)
        data = response.json()
        upload = data.get("upload") or {}
        if not upload.get("upload_link"):
            raise VimeoError("Vimeo answered the new video without an upload link.")
        return {
            "uri": data.get("uri") or "", "id": video_id_of(str(data.get("uri") or "")), "link": data.get("link") or "",
            "upload_link": upload["upload_link"], "player_url": data.get("player_embed_url") or None,
        }

    def upload_offset(self, upload_link: str) -> int:
        """``HEAD`` on the upload link: how many bytes Vimeo holds."""
        response = self._request("HEAD", upload_link, headers={"Tus-Resumable": "1.0.0"})
        try:
            return int(response.headers.get("Upload-Offset", "0"))
        except ValueError:
            return 0

    def upload(self, path: Path, upload_link: str, size: int, *, progress: Callable[[int, int], None] | None = None,
               cancel_check: Callable[[], bool] | None = None) -> bool:
        """The tus upload in chunks: PATCH after PATCH with ``Upload-Offset``, a failed chunk tried again from the
        offset Vimeo answers to ``HEAD``, up to :data:`VIMEO_CHUNK_TRIES` tries. False when the cancel check said
        stop between two chunks (nothing more is sent)."""
        offset = 0
        with open(path, "rb") as handle:
            while offset < size:
                if cancel_check and cancel_check():
                    return False
                handle.seek(offset)
                chunk = handle.read(VIMEO_CHUNK_BYTES)
                if not chunk:
                    break
                last_status: int | str = "no answer"
                for attempt in range(1, VIMEO_CHUNK_TRIES + 1):
                    try:
                        response = self._request(
                            "PATCH", upload_link, content=chunk, timeout=httpx.Timeout(VIMEO_CHUNK_TIMEOUT_S),
                            headers={"Tus-Resumable": "1.0.0", "Upload-Offset": str(offset), "Content-Type": "application/offset+octet-stream"},
                        )
                        last_status = response.status_code
                        if response.status_code in (200, 204):
                            try:
                                offset = int(response.headers.get("Upload-Offset", offset + len(chunk)))
                            except ValueError:
                                offset += len(chunk)
                            break
                    except VimeoError as exc:
                        last_status = str(exc)
                    if attempt == VIMEO_CHUNK_TRIES:
                        raise VimeoError(CHUNK_FAILED_MESSAGE.format(offset=offset, size=size, tries=VIMEO_CHUNK_TRIES, status=last_status))
                    # Resumed from what Vimeo holds, not from what was sent.
                    offset = self.upload_offset(upload_link)
                    handle.seek(offset)
                    chunk = handle.read(VIMEO_CHUNK_BYTES)
                if progress:
                    progress(offset, size)
        return True

    def get_video(self, video_id: str) -> dict:
        response = self._request("GET", f"{VIMEO_API}/videos/{video_id}", params={"fields": VIDEO_FIELDS})
        if response.status_code == 401:
            raise VimeoError(BAD_TOKEN_MESSAGE, 401)
        if response.status_code >= 400:
            raise VimeoError(REFUSED_MESSAGE.format(status=response.status_code, words=_words(response)), response.status_code)
        return response.json()

    def delete_video(self, video_id: str) -> None:
        """``DELETE /videos/{id}``; a refusal is logged, never raised - the publish is already over."""
        try:
            response = self._request("DELETE", f"{VIMEO_API}/videos/{video_id}")
        except VimeoError as exc:
            log.warning("Vimeo: the unfinished video %s could not be removed: %s", video_id, exc)
            return
        if response.status_code >= 400:
            log.warning("Vimeo: the unfinished video %s could not be removed (%s)", video_id, response.status_code)

    def wait_for_transcode(self, video_id: str, *, cancel_check: Callable[[], bool] | None = None, clock: Callable[[], float] | None = None) -> dict | None:
        """Poll until Vimeo has processed the video (the video's fields), ``error`` a :class:`VimeoError` with
        Vimeo's words, the wait bounded by :data:`VIMEO_TRANSCODE_WAIT_S`; None when the cancel check said stop."""
        now = clock or CLOCK
        deadline = now() + VIMEO_TRANSCODE_WAIT_S
        link = ""
        while True:
            if cancel_check and cancel_check():
                return None
            video = self.get_video(video_id)
            link = video.get("link") or link
            status = (video.get("transcode") or {}).get("status")
            if status == "complete":
                return video
            if status == "error":
                raise VimeoError(TRANSCODE_ERROR_MESSAGE.format(words=f"transcode status {status}"))
            if now() >= deadline:
                raise VimeoTimeout(TRANSCODE_WAIT_MESSAGE.format(minutes=VIMEO_TRANSCODE_WAIT_S // 60, link=link or f"https://vimeo.com/{video_id}"))
            self._sleep(VIMEO_TRANSCODE_POLL_S)


def _retry_after(response: httpx.Response) -> float:
    try:
        seconds = float(response.headers.get("Retry-After", "1"))
    except ValueError:
        seconds = 1.0
    return max(0.0, min(seconds, float(VIMEO_RETRY_AFTER_MAX_S)))


def publish(path: Path, *, token: str, title: str, description: str, privacy: str, progress: Callable[[float, str], None],
            cancel_check: Callable[[], bool], transport: httpx.BaseTransport | None = None) -> dict | None:
    """Publish the video at ``path``: the create, the chunked upload, the wait - progress reported from 0 to 1 -
    and the record (:func:`vimeo_record`); None when cancelled (the video removed from Vimeo). A failure is a
    :class:`VimeoError` with a line for the user, the token in none of them."""
    path = Path(path)
    size = path.stat().st_size
    progress(0.0, "Creating the video on Vimeo…")
    try:
        with VimeoClient(token, transport=transport if transport is not None else TRANSPORT) as client:
            created = client.create_video(size=size, name=title, description=description, privacy=privacy)
            video_id = created["id"]

            def on_chunk(done: int, total: int) -> None:
                share = done / total if total else 1.0
                progress(0.02 + 0.88 * share, f"Uploading… {_megabytes(done)} of {_megabytes(total)} MB")

            try:
                if not client.upload(path, created["upload_link"], size, progress=on_chunk, cancel_check=cancel_check):
                    client.delete_video(video_id)
                    return None
                progress(0.92, "Vimeo is processing the video…")
                video = client.wait_for_transcode(video_id, cancel_check=cancel_check)
            except VimeoTimeout:
                # The upload is whole and Vimeo is still processing it: the video stays, the message names its link.
                raise
            except VimeoError:
                # A chunk that failed every try, or a transcode error: nothing half-made is left on Vimeo.
                client.delete_video(video_id)
                raise
            if video is None:
                client.delete_video(video_id)
                return None
            record = vimeo_record(video, title=title, privacy=privacy)
            progress(1.0, f"Published: {record['link']}")
            return record
    except VimeoError as exc:
        raise exc.__class__(scrub(str(exc), token), exc.status) from None
