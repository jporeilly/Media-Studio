"""What ``transcribe_audio`` turns Whisper's raw output into.

No model is loaded and no audio is read: the Whisper model is a stand-in that
yields whatever segments the test asks for, which is the only part of the
engine's behaviour these tests are about.
"""

import pytest

from core import video_importer


class _Word:
    def __init__(self, word, start, end, probability=0.9):
        self.word, self.start, self.end, self.probability = word, start, end, probability


class _RawSeg:
    def __init__(self, start, end, text, words=None):
        self.start, self.end, self.text = start, end, text
        self.words = words or []


class _Info:
    language = "en"
    duration = 30.0


class _Model:
    def __init__(self, segments):
        self._segments = segments

    def transcribe(self, path, **kwargs):
        return iter(self._segments), _Info()


@pytest.fixture
def engine(monkeypatch):
    """Return a callable that runs transcribe_audio over the given raw segments."""
    def _run(raw, on_progress=None):
        monkeypatch.setattr(video_importer, "_get_whisper_model", lambda *a, **k: _Model(raw))
        monkeypatch.setattr(video_importer, "last_load_device", lambda: "cpu")
        return video_importer.transcribe_audio("audio.wav", on_progress=on_progress)
    return _run


def test_segments_with_no_words_in_them_are_dropped(engine):
    """Whisper emits a handful of empty segments, usually in the last half
    second of the audio. They arrived as blank rows in the transcript editor
    that nobody could explain, and would have become empty subtitle cues as
    well. A real 341 s recording came back with four of them at 340.5-341.0.
    """
    segs, lang, duration = engine([
        _RawSeg(0.0, 3.8, "Hi there, and welcome."),
        _RawSeg(4.0, 8.3, "  "),          # whitespace only
        _RawSeg(8.5, 9.8, "Second one."),
        _RawSeg(29.9, 30.0, ""),          # the tail artefact
        _RawSeg(30.0, 30.0, None),        # and a null, which must not raise
    ])

    assert [s.text for s in segs] == ["Hi there, and welcome.", "Second one."]
    assert lang == "en" and duration == 30.0


def test_the_text_that_survives_is_stripped(engine):
    segs, _lang, _dur = engine([_RawSeg(0.0, 1.0, "  padded out  ")])

    assert [s.text for s in segs] == ["padded out"]


def test_word_timings_are_kept_for_the_segments_that_stay(engine):
    """The word-level data is what the Whisper subtitle pass writes cues from."""
    segs, _lang, _dur = engine([
        _RawSeg(0.0, 1.0, "two words", [_Word("two", 0.0, 0.4), _Word(" words", 0.4, 1.0)]),
    ])

    assert [w["word"] for w in segs[0].words] == ["two", " words"]
    assert segs[0].words[0]["probability"] == 0.9


def test_dropping_a_segment_does_not_stall_the_progress_bar(engine):
    """The position is taken from every raw segment, including the skipped
    ones, so a run that ends on empties still reports near the end."""
    seen = []
    engine(
        [
            _RawSeg(0.0, 3.0, "Only real sentence."),
            _RawSeg(3.0, 29.0, ""),
            _RawSeg(29.0, 30.0, ""),
        ],
        on_progress=lambda pct, msg="": seen.append(pct),
    )

    assert seen, "no progress was reported at all"
    assert max(seen) >= 0.15


def test_a_transcript_of_nothing_but_empties_is_empty_not_a_crash(engine):
    segs, _lang, _dur = engine([_RawSeg(0.0, 0.1, ""), _RawSeg(0.1, 0.2, "   ")])

    assert segs == []
