"""Entry point for the desktop shell's backend.

Why this exists rather than launching ``main.py`` or ``python -m uvicorn``:

The vendored runtime is Python's Windows "embeddable package", whose ``._pth``
file REPLACES sys.path outright. The current directory is not on it, and
PYTHONPATH is ignored while a ``._pth`` is present - so ``api.app:app`` (and the
top-level ``core`` / ``utils`` / ``services`` packages it imports) is simply not
importable, no matter what working directory the process is given. The failure
is a bare ModuleNotFoundError with nothing pointing at the cause.

Putting the app root on sys.path explicitly fixes that, and gives the packaged
and development launches ONE code path instead of two that can drift.

    python boot.py --port 5680 [--app-dir <dir>]

This serves exactly what ``python main.py --no-browser`` serves - the FastAPI
API plus the built React SPA on one port - but without main.py's browser-open
and kill-previous-instance behaviour, because the Tauri shell owns the process
lifecycle (a job object kills the backend when the window closes; the shell
picks the port).

--app-dir defaults to the directory beside this file, which is the flat layout
stage-app.ps1 produces:

    app/boot.py
    app/main.py
    app/api/app.py
    app/frontend/dist/index.html
"""
import argparse
import os
import sys


def _plain(path):
    r"""Drop Windows' verbatim \\?\ prefix from a drive path.

    os.chdir() cannot use one: SetCurrentDirectory rejects the verbatim form,
    so an install under a canonicalised path failed here with every file present
    and every path check passing. The shell strips it too - this is the second
    line of defence, because the cost of getting it wrong is a server that dies
    before it can say why.

    Genuine UNC paths (\\?\UNC\...) and paths over the legacy limit still need
    the prefix, so only ordinary drive paths are unwrapped.
    """
    p = str(path)
    if p.startswith("\\\\?\\"):
        rest = p[4:]
        if len(rest) > 2 and rest[1] == ":" and rest[0].isalpha() and len(rest) < 250:
            return rest
    return p


def main():
    ap = argparse.ArgumentParser(description="Start the Media Studio Enterprise backend.")
    ap.add_argument("--port", type=int, default=5680)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--app-dir", default=None)
    args = ap.parse_args()

    here = _plain(os.path.dirname(os.path.abspath(__file__)))
    app_dir = _plain(os.path.abspath(args.app_dir or here))

    main_py = os.path.join(app_dir, "main.py")
    if not os.path.isfile(main_py):
        # Explicit beats a ModuleNotFoundError three frames deep: this is the
        # message that tells whoever is reading the log that the INSTALL is
        # wrong, not the app.
        sys.exit("boot: main.py not found at {} - the install is incomplete".format(main_py))

    # The app root holds api/, core/, utils/, services/ and __init__.py, all
    # imported as top-level packages (see CLAUDE.md: absolute package paths).
    sys.path.insert(0, app_dir)
    os.chdir(app_dir)

    # The Tauri shell is the UI; never pop a browser tab. main.py honours this
    # env var, and setting it keeps any code path that consults it in agreement.
    os.environ.setdefault("MEDIA_STUDIO_NO_BROWSER", "1")

    # Belt and braces with the shell's PYTHONDONTWRITEBYTECODE: never compile
    # bytecode into a read-only install tree - a .pyc the installer never
    # shipped is a file the uninstaller leaves behind.
    sys.dont_write_bytecode = True

    import uvicorn
    # api/app.py exposes the ready-built application object as ``app`` (the same
    # object main.py runs). Import by string so uvicorn owns the import, after
    # sys.path is set above.
    uvicorn.run("api.app:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
