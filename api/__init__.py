"""API package for Media Studio Enterprise.

The package version lives in the repo-root ``__init__.py``. This flat layout
puts the repo root on ``sys.path`` (so ``core``/``utils``/``services`` import as
top-level packages), which makes the root package awkward to import by name, so
the version is read from that file directly rather than imported.
"""

import re
from pathlib import Path


def _read_version() -> str:
    init_file = Path(__file__).resolve().parent.parent / "__init__.py"
    try:
        match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init_file.read_text(encoding="utf-8"))
        if match:
            return match.group(1)
    except OSError:
        pass
    return "0.1.0"


__version__ = _read_version()
