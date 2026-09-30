"""Every Ollama request switches thinking off, and gemma4:12b is the default.

gemma4 thinks by default: it reasons before answering, which was 16 s against
1.4 s for one rewritten sentence, and it answered "one sentence" with a list of
options. Every request body is built by ``ollama_client.request_body``, which
sends ``"think": false``; models that cannot think ignore it.
"""
import json
import re
from pathlib import Path

import pytest

from core import ollama_client
from utils.config import DEFAULT_CONFIG

ROOT = Path(__file__).resolve().parent.parent


def test_request_body_switches_thinking_off():
    body = ollama_client.request_body("gemma4:12b", prompt="hi")
    assert body == {"model": "gemma4:12b", "stream": False, "think": False, "prompt": "hi"}


def test_request_body_lets_a_stream_through():
    assert ollama_client.request_body("m", messages=[], stream=True)["stream"] is True
    assert ollama_client.request_body("m", messages=[], stream=True)["think"] is False


class _Response:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __iter__(self):
        return iter([self._payload])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("call, reply", [
    (lambda: ollama_client.generate("p", "gemma4:12b"), {"response": "ok"}),
    (lambda: list(ollama_client.generate_stream("p", "gemma4:12b")), {"response": "ok", "done": True}),
    (lambda: ollama_client.chat([{"role": "user", "content": "p"}], "gemma4:12b"), {"message": {"content": "ok"}}),
    (lambda: list(ollama_client.chat_stream([{"role": "user", "content": "p"}], "gemma4:12b")),
     {"message": {"content": "ok"}, "done": True}),
])
def test_the_client_functions_send_think_false(monkeypatch, call, reply):
    sent = {}

    def fake_urlopen(req, timeout=None):
        sent.update(json.loads(req.data.decode()))
        return _Response(reply)

    monkeypatch.setattr(ollama_client.urllib.request, "urlopen", fake_urlopen)
    call()
    assert sent["think"] is False and sent["model"] == "gemma4:12b"


HAND_BUILT = re.compile(r"""(json\s*=\s*|json\.dumps\(\s*|=\s*)\{\s*["']model["']\s*:""")


def test_no_request_body_is_built_by_hand():
    """Every module that calls an Ollama inference endpoint builds its body with
    request_body; a dict literal (on one line or several) would let a model think."""
    offenders = []
    for folder in ("core", "services", "api", "utils"):
        for path in (ROOT / folder).rglob("*.py"):
            if path.name == "ollama_client.py":
                continue
            text = path.read_text(encoding="utf-8")
            if not re.search(r"/api/(generate|chat)\b", text):
                continue
            rel = path.relative_to(ROOT).as_posix()
            if "request_body(" not in text:
                offenders.append(f"{rel}: calls Ollama without request_body")
            if HAND_BUILT.search(text):
                offenders.append(f"{rel}: builds a request body by hand")
    assert offenders == []


def test_gemma4_is_the_default_and_a_vision_model():
    assert DEFAULT_CONFIG["ollama_model"] == "gemma4:12b"
    vision = {m["name"] for m in ollama_client.MODEL_CATALOG if m["category"] == "Vision"}
    assert "gemma4:12b" in vision
