"""Pytest configuration: make the flat backend packages importable.

The app uses absolute imports (``from services.x``, ``from api.x``). Put the repo
root on ``sys.path`` so the test process resolves them without an install.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
