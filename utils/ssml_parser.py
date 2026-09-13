"""
Lightweight SSML-like markup parser for speaker notes.

The TTS engine doesn't consume SSML directly, so we provide our own simple
markup tags that can be embedded in speaker notes text:

    [pause:Xs] or [pause:Xms]  — insert silence (seconds or milliseconds)
    [emphasis]text[/emphasis]   — emphasis wrapper (stripped for TTS)
    [speed:X]text[/speed]       — speed modifier wrapper (stripped for TTS)
    [pronounce:X]               — phonetic hint (replaced with hint text)
    [break]                     — short 0.5s pause
"""

import re
from typing import List, Dict

# -- Patterns ----------------------------------------------------------------

# Self-closing tags
_PAUSE_RE = re.compile(r"\[pause:(\d+(?:\.\d+)?)(s|ms)\]", re.IGNORECASE)
_BREAK_RE = re.compile(r"\[break\]", re.IGNORECASE)
_PRONOUNCE_RE = re.compile(r"\[pronounce:([^\]]+)\]", re.IGNORECASE)

# Wrapper tags
_EMPHASIS_RE = re.compile(r"\[emphasis\](.*?)\[/emphasis\]", re.IGNORECASE | re.DOTALL)
_SPEED_RE = re.compile(r"\[speed:[^\]]*\](.*?)\[/speed\]", re.IGNORECASE | re.DOTALL)

# Catch-all for any supported tag (used by has_markup)
_ANY_TAG_RE = re.compile(
    r"\[(?:pause:\d+(?:\.\d+)?(?:s|ms)|break|emphasis|/emphasis|speed:[^\]]*|/speed|pronounce:[^\]]+)\]",
    re.IGNORECASE,
)


def has_markup(text: str) -> bool:
    """Quick check whether *text* contains any supported markup tags."""
    return bool(_ANY_TAG_RE.search(text))


def strip_markup(text: str) -> str:
    """Remove all markup tags, returning clean text suitable for TTS.

    - Wrapper tags ([emphasis], [speed:X]) are removed but their inner text
      is kept.
    - [pronounce:X] is replaced with the hint text *X*.
    - [pause:Xs], [pause:Xms], and [break] are replaced with ``"..."``
      which acts as a natural pause cue for most TTS engines.
    """
    # Unwrap wrapper tags first (keep inner text)
    result = _EMPHASIS_RE.sub(r"\1", text)
    result = _SPEED_RE.sub(r"\1", result)

    # Replace pronounce with its hint text
    result = _PRONOUNCE_RE.sub(r"\1", result)

    # Replace pause/break with ellipsis pause cue
    result = _PAUSE_RE.sub("...", result)
    result = _BREAK_RE.sub("...", result)

    return result


def extract_pauses(text: str) -> List[Dict]:
    """Extract pause instructions with their positions in the stripped text.

    Returns a list of dicts, each with:
        ``position``     – character index in the *stripped* text where the
                           pause should be inserted.
        ``duration_ms``  – pause duration in milliseconds.

    The list is sorted by position (ascending).  Both explicit ``[pause:Xs]``
    / ``[pause:Xms]`` tags and ``[break]`` tags (0.5 s) are included.
    """
    pauses: List[Dict] = []

    # We need to walk through the original text, stripping tags as we go,
    # so we can record the position in the *output* string where each pause
    # lands.

    # Build a unified list of all tag matches with their kind + duration.
    tag_entries = []

    for m in _PAUSE_RE.finditer(text):
        value = float(m.group(1))
        unit = m.group(2).lower()
        duration_ms = int(value * 1000) if unit == "s" else int(value)
        tag_entries.append((m.start(), m.end(), duration_ms))

    for m in _BREAK_RE.finditer(text):
        tag_entries.append((m.start(), m.end(), 500))

    if not tag_entries:
        return []

    # Sort by position in the original text
    tag_entries.sort(key=lambda e: e[0])

    # Now compute the position of each pause in the stripped text.
    # We need to account for *all* tag removals/replacements that happen
    # before each pause tag.

    # Collect every tag span and what it becomes in stripped text.
    replacements = []

    for m in _EMPHASIS_RE.finditer(text):
        replacements.append((m.start(), m.end(), m.group(1)))
    for m in _SPEED_RE.finditer(text):
        replacements.append((m.start(), m.end(), m.group(1)))
    for m in _PRONOUNCE_RE.finditer(text):
        replacements.append((m.start(), m.end(), m.group(1)))
    for m in _PAUSE_RE.finditer(text):
        replacements.append((m.start(), m.end(), "..."))
    for m in _BREAK_RE.finditer(text):
        replacements.append((m.start(), m.end(), "..."))

    replacements.sort(key=lambda r: r[0])

    # For each pause tag, figure out the cumulative offset shift from all
    # replacements that start before it.
    for tag_start, tag_end, duration_ms in tag_entries:
        stripped_pos = tag_start
        for rep_start, rep_end, rep_text in replacements:
            if rep_start >= tag_start:
                break
            # This replacement removed (rep_end - rep_start) chars and
            # inserted len(rep_text) chars.
            stripped_pos -= (rep_end - rep_start) - len(rep_text)

        pauses.append({"position": stripped_pos, "duration_ms": duration_ms})

    return pauses
