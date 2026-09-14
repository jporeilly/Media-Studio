"""``desktop/boot.py`` is the production entry point of the packaged app (the
Tauri shell launches it on the vendored Python). Keep its path handling and its
install-incomplete exit honest - they are what a customer sees first when an
install is wrong."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOOT = ROOT / "desktop" / "boot.py"


@pytest.fixture(scope="module")
def boot():
    spec = importlib.util.spec_from_file_location("boot_under_test", BOOT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "given, expected",
    [
        # The verbatim prefix Tauri's canonicalised resource_dir carries; os.chdir rejects it.
        (r"\\?\C:\Users\jane doe\AppData\Local\Media Studio Enterprise\app",
         r"C:\Users\jane doe\AppData\Local\Media Studio Enterprise\app"),
        (r"C:\plain\path", r"C:\plain\path"),
        # Genuine UNC paths keep their prefix.
        (r"\\?\UNC\server\share\app", r"\\?\UNC\server\share\app"),
    ],
)
def test_plain_strips_only_drive_verbatim_prefixes(boot, given, expected):
    assert boot._plain(given) == expected


def test_bin_dir_is_put_first_on_path_exactly_once(boot):
    env = {"PATH": os.pathsep.join([r"C:\Windows", r"C:\Windows\System32"])}
    first = boot._put_first_on_path(r"C:\app\bin", env)
    again = boot._put_first_on_path(r"C:\app\bin", env)  # an in-app restart inherits PATH
    assert first.split(os.pathsep)[0] == r"C:\app\bin"
    assert again == first, "a second launch must not add another copy"
    assert env["PATH"].count(r"C:\app\bin") == 1


def test_bin_dir_leads_an_empty_path(boot):
    env = {}
    assert boot._put_first_on_path(r"C:\app\bin", env) == r"C:\app\bin"


def test_boot_exits_clearly_when_main_py_is_missing(tmp_path):
    r = subprocess.run(
        [sys.executable, str(BOOT), "--app-dir", str(tmp_path), "--port", "0"],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode != 0
    out = r.stderr + r.stdout
    assert "main.py not found" in out and "install is incomplete" in out
