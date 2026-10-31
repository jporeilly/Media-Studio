"""One SSL context per process for the ``httpx`` clients (``services.content_editor``, ``services.vimeo``).

``httpx.Client()`` builds an SSL context on every construction unless it is handed one, and building it - loading the
certificate store - takes several seconds on some Windows machines (4.5 s measured on the owner's: every call the
Content Editor proxy made paid it, so the Publish stage spun for 13 s). Built once, lazily, under a lock; handed to
each client as ``verify=``. A client given a ``transport`` (the tests' ``httpx.MockTransport``) never builds one.
"""

from __future__ import annotations

import ssl
import threading

import httpx

_LOCK = threading.Lock()
_CONTEXT: ssl.SSLContext | None = None


def shared_ssl_context() -> ssl.SSLContext:
    """The process's one SSL context for outbound ``httpx`` calls, built on first use."""
    global _CONTEXT
    with _LOCK:
        if _CONTEXT is None:
            _CONTEXT = httpx.create_ssl_context()
        return _CONTEXT
