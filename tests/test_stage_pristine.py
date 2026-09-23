r"""Nothing under data\ ships with the installer, and the staging script proves it.

Why this file exists. 0.9.1's installer carried a data\ folder: data\.gitkeep
(tracked at the time, so the git clone that IS the staged tree brought it) and,
beside it, data\cache\, data\temp\ and an EMPTY data\logs\app.log created by the
stage's own import check - importing api.app runs utils\config.py and
utils\logger.py, which make the app's runtime folders next to the code, and the
code was the staged tree. Tauri bundles the tree as it stands; an NSIS upgrade
writes every bundled file over the install; so upgrading 0.9.0 -> 0.9.1
truncated the user's app.log to 0 bytes. config.json and media_studio.db
survived only because the import happens not to create them.

Three guards, in the order they act:

* the SOURCE carries no data\ at all (``test_repo_*``): the placeholder is
  untracked and the folder is ignored wholesale, so a clone has none;
* the STAGE undoes what the import created and then proves, with git, that the
  tree is the committed one plus the two overlays (``test_stage_script_*``,
  ``test_pristine_check_*``). The check is run for real - its exact text, cut
  from the script between markers as tests/test_ffprobe_shipped.py does for the
  same-build check - against a throwaway clone, and watched refusing each kind
  of stray: the log the import leaves, an empty directory git cannot see, and a
  deleted tracked file, which is what "delete .gitkeep from the clone" would
  have left in every install;
* the BUNDLE adds nothing back (``test_bundle_*``): vendor/app is the only
  resource that lands under app\.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = ROOT / "desktop"
STAGE_SCRIPT = DESKTOP / "scripts" / "stage-app.ps1"
TAURI_CONF = DESKTOP / "src-tauri" / "tauri.conf.json"

OVERLAYS = ("boot.py", "bin")


def _git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60, check=True,
    )
    return proc.stdout


def _skip_unless_git():
    if not shutil.which("git"):
        pytest.skip("git is not on PATH")


# --------------------------------------------------------------------------
# The source: a clone carries no data\
# --------------------------------------------------------------------------

def test_repo_tracks_nothing_under_data():
    r"""The stage is a git clone of HEAD, so a tracked file under data\ ships
    with every installer. 0.9.1's was data\.gitkeep."""
    _skip_unless_git()
    tracked = _git("ls-files", "--", "data", cwd=ROOT).split()
    assert tracked == [], f"tracked under data/, so the installer would ship it: {tracked}"


def test_gitignore_ignores_data_wholesale():
    lines = [ln.strip() for ln in (ROOT / ".gitignore").read_text(encoding="ascii").splitlines()]
    assert "data/" in lines, ".gitignore no longer ignores data/ as a whole"
    negations = [ln for ln in lines if ln.startswith("!data")]
    assert negations == [], f".gitignore lets something under data/ back in: {negations}"


# --------------------------------------------------------------------------
# The script: undo the import, then prove the tree
# --------------------------------------------------------------------------

def test_stage_script_removes_the_import_debris_after_the_import_checks():
    text = STAGE_SCRIPT.read_text(encoding="ascii")
    soft_check = text.index("import api.app")
    cleanup = text.index('foreach ($debris in @("data", "assets"))')
    assert cleanup > soft_check, "data\\ and assets\\ must be removed AFTER the import that creates them"


def test_stage_script_runs_the_pristine_check_last_and_unconditionally():
    r"""After the cleanup, and outside the vendored-runtime block: a stage with
    no runtime skips the import check but still must not ship data\."""
    text = STAGE_SCRIPT.read_text(encoding="ascii")
    cleanup = text.index('foreach ($debris in @("data", "assets"))')
    no_runtime = text.index("skipping the import check")
    call = text.index('Assert-StagePristine -StageDir $stageDir -Overlays @("boot.py", "bin")')
    assert call > cleanup > no_runtime
    assert text.count("Assert-StagePristine -StageDir") == 1


def _pristine_block() -> str:
    text = STAGE_SCRIPT.read_text(encoding="ascii")
    begin, end = "# --- pristine check: begin ---", "# --- pristine check: end ---"
    assert begin in text and end in text, "stage-app.ps1 lost the pristine check's markers"
    return text.split(begin, 1)[1].split(end, 1)[0]


def _throwaway_stage(tmp_path: Path) -> Path:
    r"""A repo shaped like this one where it matters - the same ignore rules for
    data\, the overlays and logs - cloned the way stage-app.ps1 clones, with the
    two overlays laid on top."""
    src = tmp_path / "src"
    src.mkdir()
    _git("init", "-q", cwd=src)
    (src / "main.py").write_text("print('hi')\n", encoding="ascii")
    (src / "api").mkdir()
    (src / "api" / "__init__.py").write_text("", encoding="ascii")
    (src / ".gitignore").write_text("data/\n*.log\n/assets/\n/bin/\n/boot.py\n__pycache__/\n", encoding="ascii")
    _git("add", "-A", cwd=src)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "tree", cwd=src)
    stage = tmp_path / "stage"
    _git("clone", "-q", str(src), str(stage), cwd=tmp_path)
    (stage / "boot.py").write_text("# overlay\n", encoding="ascii")
    (stage / "bin").mkdir()
    (stage / "bin" / "ffmpeg.exe").write_bytes(b"MZ")
    return stage


def _run_pristine_check(tmp_path: Path, stage: Path):
    _skip_unless_git()
    powershell = shutil.which("powershell")
    if not powershell:
        pytest.skip("Windows PowerShell is not available on this machine")
    rig = tmp_path / "pristine_rig.ps1"
    rig.write_text(
        '$ErrorActionPreference = "Stop"\n'
        "Set-StrictMode -Version Latest\n"
        'function Ok($m)   { Write-Host "  [ok] $m" }\n'
        'function Warn($m) { Write-Host "  [!]  $m" }\n'
        + _pristine_block()
        + f"\nAssert-StagePristine -StageDir '{stage}' -Overlays @(\"boot.py\", \"bin\")"
        + "\nexit 0\n",
        encoding="ascii",
    )
    proc = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(rig)],
        capture_output=True, text=True, timeout=120,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_pristine_check_passes_the_committed_tree_plus_overlays(tmp_path):
    stage = _throwaway_stage(tmp_path)
    code, out = _run_pristine_check(tmp_path, stage)
    assert code == 0, f"a clean stage was refused:\n{out}"
    assert "the staged tree is the committed tree plus boot.py, bin - nothing under data\\ ships" in out


def test_pristine_check_refuses_the_log_the_import_leaves(tmp_path):
    r"""The 0.9.1 payload: data\logs\app.log, empty. *.log is ignored, which is
    exactly why the check must ask for ignored files too."""
    stage = _throwaway_stage(tmp_path)
    (stage / "data" / "logs").mkdir(parents=True)
    (stage / "data" / "logs" / "app.log").write_bytes(b"")
    code, out = _run_pristine_check(tmp_path, stage)
    assert code != 0, f"an app.log in the stage was let through:\n{out}"
    # git collapses a wholly ignored directory to its top: "!! data/".
    assert "stray: !! data/" in out, out
    assert "the staged tree is not the committed tree plus boot.py, bin" in out, out


def test_pristine_check_refuses_an_empty_directory_git_cannot_see(tmp_path):
    r"""data\cache\ and data\temp\ as the import leaves them: empty, so absent
    from git status. The top-level scan is what catches them."""
    stage = _throwaway_stage(tmp_path)
    (stage / "data" / "cache").mkdir(parents=True)
    code, out = _run_pristine_check(tmp_path, stage)
    assert code != 0, f"an empty data\\cache\\ was let through:\n{out}"
    assert "stray: ?? data/ (not committed)" in out, out


def test_pristine_check_refuses_a_deleted_tracked_file(tmp_path):
    r"""The fix NOT taken: deleting a tracked data\.gitkeep from the clone would
    have left every install with a permanent local deletion. The check treats
    that as the defect it is."""
    stage = _throwaway_stage(tmp_path)
    (stage / "main.py").unlink()
    code, out = _run_pristine_check(tmp_path, stage)
    assert code != 0, f"a deleted tracked file was let through:\n{out}"
    assert "stray: D main.py" in out, out


# --------------------------------------------------------------------------
# The bundle: nothing else lands under app\
# --------------------------------------------------------------------------

def test_bundle_puts_only_the_staged_tree_under_app():
    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    resources = conf["bundle"]["resources"]
    assert resources["vendor/app"] == "app"
    others = {src: dst for src, dst in resources.items() if src != "vendor/app"}
    under_app = {src: dst for src, dst in others.items() if dst == "app" or dst.startswith("app/")}
    assert under_app == {}, f"another resource lands under app\\ and could re-add data\\: {under_app}"
    assert not any("data" in src or "data" in dst for src, dst in resources.items()), resources
