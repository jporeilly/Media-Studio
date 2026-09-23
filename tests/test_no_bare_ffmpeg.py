"""No module under ``core/`` or ``services/`` may spawn a bare ``ffmpeg`` or
``ffprobe``.

Trap 3, swept rather than spot-checked. A bare name is resolved against
whatever PATH the host happens to have, and the PACKAGED app's PATH is not a
developer's: ``utils.config`` prepends the bundled binary's directory at
import, so a bare ``"ffmpeg"`` works on a dev box by accident and may find a
different binary - or none - anywhere else. The engine's own code therefore
names the RESOLVED path, and since F1 it never needs a prober at all: it reads
a file's duration and chapters out of ffmpeg's own header. (The installer
ships an ffprobe from 0.9.1, but for pydub's decode, not for the engine.)

That is how F1 shipped: ``core.video_creator._probe_duration`` spawned a bare
``ffprobe``, 0.9.0's package had none, the probe answered None on every
customer machine, and the mux was then handed an unbounded ``apad`` the bundled
ffmpeg 7.1 never finishes - the product's main feature stalling for ten minutes
and failing, on every machine except the one it was developed on. The point of
a sweep is that the next one is caught the day it is written rather than the
day a customer meets it.

The rule: the first element of an argv list or tuple, and the command string
of a shell call, must be a RESOLVED path (``utils.config.FFMPEG_PATH``), never
a bare binary name - written out, or hoisted into a string constant first.

**What it cannot see**, said plainly so a green run is not over-read: a name
built at runtime (an f-string, a concatenation, ``os.path.join``, a
``shutil.which`` result); a constant imported from another module or read off
an attribute (``from x import PROBE``, ``self.binary``); an argv list whose
first element is added later (``.append``/``.insert``) or that comes from
``str.split``; a spawn through anything not in :data:`SPAWNERS`; and every file
outside ``core/`` and ``services/`` - pydub's own spawns in site-packages
included.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TREES = ("core", "services")
BARE = {"ffmpeg", "ffprobe", "ffmpeg.exe", "ffprobe.exe"}
# subprocess.*, os.system / os.popen, and asyncio's two spawners: the first
# positional argument is the program (or the whole shell command).
SPAWNERS = {
    "run", "Popen", "call", "check_call", "check_output",
    "system", "popen",
    "create_subprocess_exec", "create_subprocess_shell",
}


def _modules() -> list[Path]:
    return sorted(
        path
        for tree in TREES
        for path in (ROOT / tree).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _string_constants(tree: ast.AST) -> dict[str, str]:
    """Every name bound to a plain string literal anywhere in the module -
    ``PROBE = "ffprobe"`` at module scope, or a local ``binary = "ffmpeg"``
    inside a function. Scopes are merged on purpose: a name bound to a bare
    binary ANYWHERE in the file is suspect wherever it is spawned, and a false
    alarm costs a rename where a miss costs a release."""
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            for target in targets:
                if isinstance(target, ast.Name):
                    # Keep a bare value if ANY binding of the name is bare.
                    if found.get(target.id, "").split(" ")[0].lower() not in BARE:
                        found[target.id] = value.value
    return found


def _resolve(node: ast.AST, constants: dict[str, str]) -> str | None:
    """The string ``node`` stands for, when the sweep can know it: a literal,
    or a name bound to one. None for anything built at runtime."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _is_bare(command: str | None) -> bool:
    return command is not None and command.strip().split(" ")[0].lower() in BARE


def _offenders(path: Path) -> list[str]:
    """``file:line`` for every bare ffmpeg/ffprobe spawn in ``path``.

    Two shapes, because both have been written here: an argv LIST or TUPLE
    whose first element is the bare name (``["ffprobe", "-v", ...]``, or
    ``[PROBE, ...]`` with ``PROBE = "ffprobe"``), wherever it is built -
    assigned to a name and run later, or passed straight to ``subprocess`` -,
    and a shell STRING starting with the bare name, given to any of
    :data:`SPAWNERS` (``shell=True``, ``os.system``) literally or through a
    constant.
    """
    found: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    constants = _string_constants(tree)
    label = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name

    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            first = node.elts[0]
            value = _resolve(first, constants)
            if value is not None and value.lower() in BARE:
                via = "" if isinstance(first, ast.Constant) else f" (via {first.id})"
                found.append(f"{label}:{first.lineno}: argv[0] is {value!r}{via}")
        elif isinstance(node, ast.Call) and node.args:
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            first = node.args[0]
            value = _resolve(first, constants)
            if name in SPAWNERS and _is_bare(value):
                via = "" if isinstance(first, ast.Constant) else f" (via {first.id})"
                found.append(f"{label}:{first.lineno}: command is {value!r}{via}")
    return found


def test_no_backend_module_spawns_a_bare_ffmpeg_or_ffprobe():
    modules = _modules()
    assert modules, "the sweep found no modules to sweep - it would pass vacuously"
    offenders = [line for module in modules for line in _offenders(module)]
    assert not offenders, (
        "a bare binary name is spawned; use the resolved utils.config.FFMPEG_PATH "
        "(and the engine never needs ffprobe - it reads ffmpeg's own header):\n  "
        + "\n  ".join(offenders)
    )


def test_the_sweep_sees_a_bare_name_when_there_is_one(tmp_path):
    """The guard's own guard: the parser really does find every shape it claims
    to, so a green sweep means "none there" rather than "looked in the wrong
    place" - and a resolved path, which is the rule, is left alone."""
    planted = tmp_path / "planted.py"
    planted.write_text(
        "import os\n"
        "import subprocess\n"
        "PROBE = 'ffprobe'\n"
        "SHELL = 'ffmpeg -i x.mp4'\n"
        "cmd = ['ffprobe', '-i', 'x.mp4']\n"
        "subprocess.run(cmd)\n"
        "subprocess.Popen('ffmpeg -i x.mp4', shell=True)\n"
        "subprocess.run([PROBE, '-i', 'x.mp4'], timeout=30)\n"
        "subprocess.run((PROBE, '-i', 'x.mp4'))\n"
        "os.system(SHELL)\n"
        "def f():\n"
        "    binary = 'ffmpeg'\n"
        "    subprocess.run([binary, '-i', 'x.mp4'])\n"
        "subprocess.run([FFMPEG_PATH, '-i', 'x.mp4'])\n",
        encoding="utf-8",
    )
    found = _offenders(planted)
    assert len(found) == 6, "\n".join(found)
    joined = "\n".join(found)
    assert "planted.py:5: argv[0] is 'ffprobe'" in joined, "a literal argv[0]"
    assert "planted.py:7: command is 'ffmpeg -i x.mp4'" in joined, "a shell=True string"
    assert "planted.py:8: argv[0] is 'ffprobe' (via PROBE)" in joined, "a hoisted module constant"
    assert "planted.py:9: argv[0] is 'ffprobe' (via PROBE)" in joined, "an argv tuple"
    assert "planted.py:10: command is 'ffmpeg -i x.mp4' (via SHELL)" in joined, "os.system via a constant"
    assert "planted.py:13: argv[0] is 'ffmpeg' (via binary)" in joined, "a hoisted local"
    assert "FFMPEG_PATH" not in joined, "the resolved path is the rule, never an offender"
