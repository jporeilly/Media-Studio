"""Backend-only path constants used by the video engine.

Extracted from the desktop edition's ``gui/styles.py`` so the engine keeps the
same on-disk layout (``<repo>/assets`` and its sub-directories) without pulling
in any of the NiceGUI/CSS UI code. Only the path constants the engine imports
live here: ``ASSETS_DIR`` (Kokoro model cache in core/kokoro_tts_generator.py)
and ``TEMP_DIR`` (scratch files in services/processing.py).
"""

from pathlib import Path

# ``services`` sits one level below the repo root, so parent.parent is the root -
# the same relationship ``gui`` had, which keeps ASSETS_DIR pointing at <repo>/assets.
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
MUSIC_DIR = ASSETS_DIR / "music"
TRANSITIONS_DIR = ASSETS_DIR / "transitions"
PPTX_CACHE_DIR = ASSETS_DIR / "pptx"
TEMP_DIR = ASSETS_DIR / "temp"
