r"""ffprobe ships with the installer, and the re-voice decodes without a
machine-installed ffmpeg.

Why this file exists. 0.9.0's re-voice failed on every machine that was not the
dev box. F1 fixed the app's OWN bare ``ffprobe`` call, but that is not the only
one: the engine decodes audio through ``pydub.AudioSegment.from_file``, and for
anything that is not a ``.wav`` PYDUB runs its own prober - ``mediainfo_json``
-> ``get_prober_name`` in ``pydub/utils.py`` - as a bare ``"ffprobe"`` resolved
off PATH, whatever the app does about its own calls. Ten ``from_file`` call
sites across ``core/audio_mixer.py``, ``core/video_creator.py`` and
``services/processing.py`` go through it: the re-voice decodes every synthesised
sentence, and every narrated render decodes every slide's MP3 to build its
master track. The installer shipped ``app\bin\ffmpeg.exe`` and nothing else, so
on a clean install both failed at their first decode with ``FileNotFoundError
[WinError 2]``.

So the fix is a binary, and a binary needs guarding on three fronts, which is
the shape of this file:

* the BUILD really fetches and stages it, from a pinned archive of the matching
  build (``test_fetch_*``, ``test_dist_*``, ``test_stage_*``, ``test_staged_*``);
* the RUNTIME really resolves it explicitly, rather than winning a PATH race,
  and says so when it has to pair the shipped ffmpeg with a foreign prober
  (``test_shipped_*``, ``test_pydub_*``, ``test_mixed_pair_*``);
* and the thing it was bought for really works on a machine with no ffmpeg of
  its own (``test_clean_machine_*``), both through PATH and through the layout
  an install actually has - including the negative, which is the 0.9.0 bug
  reproduced on demand: take ffprobe away and the decode dies again with the
  same ``FileNotFoundError``.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = ROOT / "desktop"
FETCH_SCRIPT = DESKTOP / "scripts" / "fetch-ffprobe.ps1"
STAGE_SCRIPT = DESKTOP / "scripts" / "stage-app.ps1"
PACKAGE_JSON = DESKTOP / "package.json"

# Where stage-app.ps1 puts the app that tauri.conf.json bundles as "app".
STAGED_BIN = DESKTOP / "src-tauri" / "vendor" / "app" / "bin"
VENDOR_PY = DESKTOP / "src-tauri" / "vendor" / "python" / "python.exe"

# The pin. Repeated here on purpose: the test is the second place that has to
# be changed when the build moves, which is how a silent swap gets noticed.
ARCHIVE_URL = (
    "https://github.com/GyanD/codexffmpeg/releases/download/7.1/"
    "ffmpeg-7.1-essentials_build.zip"
)
ARCHIVE_SHA256 = "fa7d4d7e795db0e2503f49f105f46ed5852386f0cfdd819899be3b65ebde24fc"
FFPROBE_SHA256 = "436bf02524d50135ed9965b90d1e0ad7f26c5c236132613a2edb87ef8b6873d0"
# What both binaries print on the first line of -version.
BUILD_TOKEN = "7.1-essentials_build-www.gyan.dev"


# --------------------------------------------------------------------------
# The build: the fetch step
# --------------------------------------------------------------------------

def test_fetch_script_exists():
    assert FETCH_SCRIPT.is_file(), f"{FETCH_SCRIPT} is missing"


def test_fetch_script_is_ascii_only():
    """PowerShell 5.1 chokes on a non-ASCII byte in a .ps1 - an em-dash in a
    comment is enough to break the parse, which happens at BUILD time on
    whatever machine runs the release."""
    raw = FETCH_SCRIPT.read_bytes()
    offenders = [
        (i, raw[i]) for i in range(len(raw)) if raw[i] > 0x7F
    ]
    assert not offenders, f"non-ASCII bytes at offsets {offenders[:10]}"


@pytest.mark.parametrize("token", ["&&", "??", "?."])
def test_fetch_script_avoids_powershell_7_syntax(token):
    """5.1 has no pipeline-chain operator and no null-coalescing/conditional;
    each is a parser error, not a runtime one, so the whole script dies."""
    assert token not in FETCH_SCRIPT.read_text(encoding="ascii"), (
        f"fetch-ffprobe.ps1 uses '{token}', which Windows PowerShell 5.1 cannot parse"
    )


def test_fetch_script_pins_the_archive_and_both_hashes():
    """A build is only reproducible if the bytes are pinned, and a swapped
    binary is only noticed if the pin is checked."""
    text = FETCH_SCRIPT.read_text(encoding="ascii")
    assert ARCHIVE_URL in text, "the archive URL is not pinned in fetch-ffprobe.ps1"
    assert ARCHIVE_SHA256 in text, "the archive SHA-256 is not pinned"
    assert FFPROBE_SHA256 in text, "ffprobe's own SHA-256 is not pinned"
    assert BUILD_TOKEN in text, "the expected -version token is not pinned"


def test_fetch_script_verifies_rather_than_trusts():
    """Both hashes must be COMPARED, not merely written down."""
    text = FETCH_SCRIPT.read_text(encoding="ascii")
    assert "-ne $ArchiveSha256" in text, "the archive hash is pinned but never compared"
    assert "-ne $FfprobeSha256" in text, "ffprobe's hash is pinned but never compared"
    assert "throw" in text, "a failed check must throw, not warn and carry on"


# --------------------------------------------------------------------------
# The build: the dist chain and the staging step
# --------------------------------------------------------------------------

def test_dist_chain_fetches_ffprobe_before_staging():
    """stage:app copies what fetch:ffprobe left behind, so the order is not
    cosmetic: reversed, the first build on a fresh machine ships no prober and
    only warns about it."""
    scripts = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["scripts"]
    assert "fetch:ffprobe" in scripts, "desktop/package.json has no fetch:ffprobe script"
    assert "fetch-ffprobe.ps1" in scripts["fetch:ffprobe"]

    dist = scripts["dist"]
    assert "npm run fetch:ffprobe" in dist, "the dist chain never fetches ffprobe"
    assert dist.index("fetch:ffprobe") < dist.index("stage:app"), (
        "fetch:ffprobe must run BEFORE stage:app in the dist chain"
    )


def test_stage_script_ships_ffprobe_and_warns_when_it_cannot():
    text = STAGE_SCRIPT.read_text(encoding="ascii")
    assert 'Join-Path $binDir "ffprobe.exe"' in text, (
        "stage-app.ps1 does not copy ffprobe.exe into app\\bin"
    )
    assert "NO ffprobe.exe TO STAGE" in text, (
        "a missing ffprobe must be loud; silence ships an installer that cannot re-voice"
    )


def test_stage_script_no_longer_claims_ffprobe_is_absent():
    """The comment that shipped the bug. It told the next reader that the
    engine 'degrades gracefully' without a prober, which was the false belief
    the re-voice failure grew out of."""
    text = STAGE_SCRIPT.read_text(encoding="ascii")
    assert "ffprobe is not bundled" not in text
    assert "falls back to apad" not in text


def test_requirements_pin_the_ffmpeg_that_ships():
    """ffmpeg arrives through moviepy -> imageio-ffmpeg, and 0.6.0 bundles 7.1.
    Unpinned, a runtime rebuild could move ffmpeg to another release while
    fetch-ffprobe.ps1 holds ffprobe at 7.1 - the two pins change together."""
    lines = [
        line.split("#", 1)[0].strip()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    ]
    assert "imageio-ffmpeg==0.6.0" in lines, (
        "requirements.txt must pin imageio-ffmpeg==0.6.0 exactly - it is the ffmpeg the installer ships"
    )


def test_stage_script_fails_a_mismatched_pair_unless_forced():
    """A warning in a 13-minute build log is not a stop. The check must throw,
    and only -Force (which a dirty-tree build already needs) may let it by."""
    text = STAGE_SCRIPT.read_text(encoding="ascii")
    assert "-AllowMismatch:$Force" in text, (
        "the same-build check must be called with -AllowMismatch tied to -Force, and nothing else"
    )
    block = _same_build_block()
    assert "if (-not $AllowMismatch)" in block and "throw" in block, (
        "the same-build check no longer throws on a mismatch"
    )


def _same_build_block() -> str:
    text = STAGE_SCRIPT.read_text(encoding="ascii")
    begin, end = "# --- same-build check: begin ---", "# --- same-build check: end ---"
    assert begin in text and end in text, "stage-app.ps1 lost the same-build check's markers"
    return text.split(begin, 1)[1].split(end, 1)[0]


# --------------------------------------------------------------------------
# The build: what actually got staged
# --------------------------------------------------------------------------

def _staged(name: str) -> Path:
    return STAGED_BIN / name


def _skip_unless_staged():
    if not STAGED_BIN.is_dir():
        pytest.skip(
            f"no staged app tree at {STAGED_BIN} - run 'npm run stage:app' in desktop/ "
            "(this machine has not built the installer)"
        )


def _first_version_line(exe: Path) -> str:
    out = subprocess.run(
        [str(exe), "-version"], capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, f"{exe.name} -version exited {out.returncode}: {out.stderr[:400]}"
    return out.stdout.splitlines()[0].strip()


def test_staged_bin_holds_both_binaries():
    _skip_unless_staged()
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        assert _staged(name).is_file(), (
            f"the staged app\\bin has no {name} - the installer would ship without it"
        )


def test_staged_binaries_report_the_same_build():
    """A prober from another release is not a drop-in. 7.1 and 8.0.1 already
    differ where this app feels it: an unbounded ``-af apad`` exits in 0.2 s on
    8.0.1 and never finishes on 7.1."""
    _skip_unless_staged()
    ffmpeg, ffprobe = _staged("ffmpeg.exe"), _staged("ffprobe.exe")
    if not (ffmpeg.is_file() and ffprobe.is_file()):
        pytest.skip("the staged app\\bin does not hold both binaries yet")

    ffmpeg_line = _first_version_line(ffmpeg)
    ffprobe_line = _first_version_line(ffprobe)
    assert BUILD_TOKEN in ffmpeg_line, f"staged ffmpeg is not the expected build: {ffmpeg_line}"
    assert BUILD_TOKEN in ffprobe_line, f"staged ffprobe is not the expected build: {ffprobe_line}"

    ffmpeg_build = ffmpeg_line.split("ffmpeg version ", 1)[-1].split(" ", 1)[0]
    ffprobe_build = ffprobe_line.split("ffprobe version ", 1)[-1].split(" ", 1)[0]
    assert ffmpeg_build == ffprobe_build, (
        "the staged pair are different builds:\n"
        f"  ffmpeg  {ffmpeg_line}\n"
        f"  ffprobe {ffprobe_line}"
    )


def _run_same_build_check(tmp_path: Path, ffmpeg: Path, ffprobe: Path, allow: bool):
    """Run stage-app.ps1's OWN same-build function - the text between its
    markers - against real binaries, in Windows PowerShell 5.1.

    The whole script cannot show the failure: on a dirty tree it only runs with
    -Force, and -Force is exactly what lets a mismatch by."""
    powershell = shutil.which("powershell")
    if not powershell:
        pytest.skip("Windows PowerShell is not available on this machine")
    rig = tmp_path / "same_build_rig.ps1"
    rig.write_text(
        '$ErrorActionPreference = "Stop"\n'
        "Set-StrictMode -Version Latest\n"
        'function Ok($m)   { Write-Host "  [ok] $m" }\n'
        'function Warn($m) { Write-Host "  [!]  $m" }\n'
        + _same_build_block()
        + f"\nAssert-SameMediaBuild -FfmpegExe '{ffmpeg}' -FfprobeExe '{ffprobe}'"
        + (" -AllowMismatch" if allow else "")
        + "\nexit 0\n",
        encoding="ascii",
    )
    proc = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(rig)],
        capture_output=True, text=True, timeout=120,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _skip_unless_pair_staged():
    _skip_unless_staged()
    if not (_staged("ffmpeg.exe").is_file() and _staged("ffprobe.exe").is_file()):
        pytest.skip("the staged app\\bin does not hold both binaries yet")


def test_stage_same_build_check_passes_the_shipped_pair(tmp_path):
    _skip_unless_pair_staged()
    code, out = _run_same_build_check(
        tmp_path, _staged("ffmpeg.exe"), _staged("ffprobe.exe"), allow=False
    )
    assert code == 0, f"the shipped pair was refused:\n{out}"
    assert f"ffmpeg and ffprobe are the same build ({BUILD_TOKEN})" in out


def test_stage_same_build_check_fails_a_mismatch_without_force(tmp_path):
    """The planted mismatch: ffmpeg offered as the prober. Its version line is
    not an ffprobe's, so the check must refuse it - it fails closed on anything
    it cannot positively match."""
    _skip_unless_pair_staged()
    code, out = _run_same_build_check(
        tmp_path, _staged("ffmpeg.exe"), _staged("ffmpeg.exe"), allow=False
    )
    assert code != 0, f"a mismatched pair was staged without -Force:\n{out}"
    assert "the staged ffmpeg and ffprobe are different builds" in out, out


def test_stage_same_build_check_lets_force_through_loudly(tmp_path):
    _skip_unless_pair_staged()
    code, out = _run_same_build_check(
        tmp_path, _staged("ffmpeg.exe"), _staged("ffmpeg.exe"), allow=True
    )
    assert code == 0, f"-Force did not let the mismatch through:\n{out}"
    assert "DIFFERENT BUILDS" in out and "-Force: staging the mismatched pair anyway" in out, out


# --------------------------------------------------------------------------
# The runtime: explicit resolution, not a PATH race
# --------------------------------------------------------------------------

def _fake_bin(tmp_path: Path, *names: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (bin_dir / name).write_bytes(b"MZ")
    return bin_dir


def test_shipped_bin_beats_anything_on_path(tmp_path, monkeypatch):
    """boot.py prepends app\\bin to PATH, so a ``which`` usually finds the
    shipped pair anyway - but "usually" is an environment accident. The
    resolver takes them by absolute path so a machine with its own ffmpeg
    cannot pair a different build with the one the app was tested against."""
    import utils.config as cfg

    suffix = ".exe" if os.name == "nt" else ""
    bin_dir = _fake_bin(tmp_path, f"ffmpeg{suffix}", f"ffprobe{suffix}")
    monkeypatch.setattr(cfg, "APP_DIR", tmp_path)
    monkeypatch.setattr(
        cfg, "_discovered_ffmpeg_paths", lambda: (r"C:\elsewhere\ffmpeg.exe", r"C:\elsewhere\ffprobe.exe")
    )

    ffmpeg, ffprobe = cfg._get_ffmpeg_paths()
    assert ffmpeg == str(bin_dir / f"ffmpeg{suffix}")
    assert ffprobe == str(bin_dir / f"ffprobe{suffix}")


def test_shipped_ffmpeg_without_ffprobe_still_looks_elsewhere_for_the_prober(tmp_path, monkeypatch):
    """An install that self-updates from 0.9.0 gets the new code and keeps its
    old ``bin\\`` - the update pulls the checkout, not the binaries - so ffmpeg
    can be there with no ffprobe beside it. The shipped ffmpeg must still win
    while the prober falls through to discovery."""
    import utils.config as cfg

    suffix = ".exe" if os.name == "nt" else ""
    bin_dir = _fake_bin(tmp_path, f"ffmpeg{suffix}")
    monkeypatch.setattr(cfg, "APP_DIR", tmp_path)
    monkeypatch.setattr(
        cfg, "_discovered_ffmpeg_paths", lambda: (r"C:\elsewhere\ffmpeg.exe", r"C:\elsewhere\ffprobe.exe")
    )

    ffmpeg, ffprobe = cfg._get_ffmpeg_paths()
    assert ffmpeg == str(bin_dir / f"ffmpeg{suffix}")
    assert ffprobe == r"C:\elsewhere\ffprobe.exe"


def test_nothing_shipped_falls_back_entirely(tmp_path, monkeypatch):
    """A source checkout has no bin\\ at all; the developer's own ffmpeg wins."""
    import utils.config as cfg

    monkeypatch.setattr(cfg, "APP_DIR", tmp_path)
    monkeypatch.setattr(
        cfg, "_discovered_ffmpeg_paths", lambda: (r"C:\elsewhere\ffmpeg.exe", None)
    )
    assert cfg._get_ffmpeg_paths() == (r"C:\elsewhere\ffmpeg.exe", None)


def test_pydub_probes_with_the_resolved_path_not_a_bare_name():
    """The seam that actually reaches pydub.

    pydub 0.25.1 has NO ffprobe attribute it reads: ``AudioSegment.converter``
    steers the ENCODER, and every decode probes through
    ``utils.mediainfo_json`` -> ``get_prober_name()``, which returns the bare
    name and leaves resolving to Windows. utils/config.py used to set
    ``AudioSegment.ffprobe``, an attribute nothing ever reads. Replacing
    ``get_prober_name`` is what works - and it also stops pydub running an
    ``ffprobe.exe`` that merely happens to sit in the working directory, which
    pydub's own ``which`` searches FIRST.
    """
    import pydub.utils

    from utils.config import _FFPROBE_PATH

    if not _FFPROBE_PATH:
        pytest.skip("no ffprobe resolvable on this machine, so there is nothing to point pydub at")

    prober = pydub.utils.get_prober_name()
    assert prober == _FFPROBE_PATH, (
        f"pydub would spawn {prober!r}; it must spawn the resolved {_FFPROBE_PATH!r}"
    )
    assert os.path.isabs(prober), "the prober must be an absolute path, not a name off PATH"


# --- the mixed pair: allowed, never silent -----------------------------------

BIN_FFMPEG = r"C:\Media-Studio-Enterprise\app\bin\ffmpeg.exe"
BIN_FFPROBE = r"C:\Media-Studio-Enterprise\app\bin\ffprobe.exe"
SYS_FFMPEG = r"C:\Tools\ffmpeg-8.0.1\bin\ffmpeg.exe"
SYS_FFPROBE = r"C:\Tools\ffmpeg-8.0.1\bin\ffprobe.exe"


def _fake_version(path: str) -> str:
    return "7.1-essentials" if "Media-Studio" in path else "8.0.1-full_build"


def _no_version(path: str) -> str:
    raise AssertionError(f"asked the version of {path} in a state that must stay silent")


def test_mixed_pair_warning_fires_for_a_0_9_0_install_updated_in_place():
    """bin holds ffmpeg only; the prober came from the machine. Decoding works,
    so the pair is allowed - but it is not the pair the install was tested
    with, and the owner decided that must be said at every start."""
    from utils.config import _mixed_pair_warning

    warning = _mixed_pair_warning(
        (BIN_FFMPEG, None), (BIN_FFMPEG, SYS_FFPROBE), version_of=_fake_version
    )
    assert warning, "a shipped ffmpeg paired with a foreign prober went unreported"
    for needle in (BIN_FFMPEG, SYS_FFPROBE, "7.1-essentials", "8.0.1-full_build", "installer"):
        assert needle in warning, f"the warning does not name {needle!r}:\n{warning}"


def test_mixed_pair_warning_fires_the_other_way_round_too():
    from utils.config import _mixed_pair_warning

    warning = _mixed_pair_warning(
        (None, BIN_FFPROBE), (SYS_FFMPEG, BIN_FFPROBE), version_of=_fake_version
    )
    assert warning and SYS_FFMPEG in warning and BIN_FFPROBE in warning


@pytest.mark.parametrize(
    "shipped, resolved",
    [
        pytest.param((BIN_FFMPEG, BIN_FFPROBE), (BIN_FFMPEG, BIN_FFPROBE), id="shipped-together"),
        pytest.param((None, None), (SYS_FFMPEG, SYS_FFPROBE), id="source-checkout"),
        pytest.param((BIN_FFMPEG, None), (BIN_FFMPEG, None), id="no-prober-anywhere"),
        pytest.param((None, None), (None, None), id="nothing-found"),
        # A source checkout whose machine has ffmpeg but no ffprobe: nothing was
        # shipped, so there is no pair to have come from different places. The
        # re-review's planted MP1 (the "no pair" guard removed) made this state
        # raise TypeError at import and no test noticed without a staged tree.
        pytest.param((None, None), (SYS_FFMPEG, None), id="source-checkout-no-prober"),
    ],
)
def test_mixed_pair_warning_is_silent_otherwise(shipped, resolved):
    """Silent when the pair came from one place, and when there is no pair to
    mix at all - a missing binary fails loudly on its own. It must not even
    run a binary to ask its version in these states: that would be two
    process launches on every normal start."""
    from utils.config import _mixed_pair_warning

    assert _mixed_pair_warning(shipped, resolved, version_of=_no_version) is None


# --- no prober at all under a shipped ffmpeg: the worst state, never silent ----

def test_missing_prober_warning_fires_for_a_0_9_0_install_updated_in_place():
    """bin holds the shipped ffmpeg, no ffprobe is shipped and none is on the
    machine: the first re-voice or narrated render will die with [WinError 2].
    The start must say so, and say what fixes it."""
    from utils.config import _missing_prober_warning

    warning = _missing_prober_warning((BIN_FFMPEG, None), (BIN_FFMPEG, None))
    assert warning, "a shipped ffmpeg with no prober anywhere went unreported"
    for needle in (BIN_FFMPEG, "ffprobe.exe", "never refreshes app\\bin", "installer",
                   "re-voice", "narrated render", "WinError 2"):
        assert needle in warning, f"the warning does not say {needle!r}:\n{warning}"


@pytest.mark.parametrize(
    "shipped, resolved",
    [
        # A developer's machine: no shipped ffmpeg, and no prober - not a broken install.
        pytest.param((None, None), (SYS_FFMPEG, None), id="source-checkout-no-prober"),
        pytest.param((None, None), (None, None), id="source-checkout-nothing"),
        # The shipped ffmpeg with the machine's prober: the OTHER warning's state.
        pytest.param((BIN_FFMPEG, None), (BIN_FFMPEG, SYS_FFPROBE), id="mixed-pair"),
        pytest.param((BIN_FFMPEG, BIN_FFPROBE), (BIN_FFMPEG, BIN_FFPROBE), id="shipped-together"),
    ],
)
def test_missing_prober_warning_is_silent_otherwise(shipped, resolved):
    from utils.config import _missing_prober_warning

    assert _missing_prober_warning(shipped, resolved) is None


def test_mixed_pair_warning_names_a_version_it_cannot_read(tmp_path):
    """The real version reader, on a file that is not a program: the warning
    must still be written, saying so, rather than raising at import."""
    from utils.config import _binary_version

    not_a_program = tmp_path / "ffprobe.exe"
    not_a_program.write_bytes(b"MZ")
    assert _binary_version(str(not_a_program)).startswith("version unreadable")


# --------------------------------------------------------------------------
# The proof: a machine with no ffmpeg of its own
# --------------------------------------------------------------------------

# Runs in a CHILD process because the point is the environment: PATH cut down
# to one directory. Reports every step as data so a failure says which one
# broke rather than just "exited 1".
_PROBE = r'''
import json, os, shutil, sys, tempfile
from pathlib import Path

repo, mp3 = sys.argv[1], sys.argv[2]
app_root = sys.argv[3] if len(sys.argv) > 3 else None
sys.path.insert(0, repo)
if app_root:
    # An installed-layout root: its OWN utils/ (so utils.config's APP_DIR is
    # this root, and APP_DIR/bin is what it resolves from) in front of the
    # repo, which still supplies core/ and services/.
    sys.path.insert(0, app_root)
out = {"path": os.environ.get("PATH", ""), "app_root": app_root}

# Before the app touches PATH: what the ENVIRONMENT alone would resolve.
out["which_ffprobe_before_config"] = shutil.which("ffprobe")

# The app configures its binaries at import (boot.py -> uvicorn -> api.app ->
# ... -> utils.config), and utils.logger - which every engine module imports -
# logs the mixed-pair warning; do the same, in the same order.
try:
    import utils.config as cfg
    import utils.logger  # noqa: F401 - imported for its startup side effect
    out["app_dir"] = str(cfg.APP_DIR)
    out["ffmpeg_path"] = cfg.FFMPEG_PATH
    out["ffprobe_path"] = cfg._FFPROBE_PATH
    out["pair_warning"] = cfg.FFMPEG_PAIR_WARNING
    out["missing_warning"] = cfg.FFPROBE_MISSING_WARNING
    log = cfg.CONFIG_DIR / "logs" / "app.log"
    tail = ""
    if log.is_file():
        with open(log, "rb") as fh:
            fh.seek(max(0, log.stat().st_size - 65536))
            tail = fh.read().decode("utf-8", "replace")
    out["pair_warning_logged"] = bool(cfg.FFMPEG_PAIR_WARNING) and cfg.FFMPEG_PAIR_WARNING in tail
    out["missing_warning_logged"] = (
        bool(cfg.FFPROBE_MISSING_WARNING) and cfg.FFPROBE_MISSING_WARNING in tail
    )
except Exception as exc:
    out["config_error"] = "%s: %s" % (type(exc).__name__, exc)

# (a) the shipped prober is the one that gets found
try:
    out["which_ffprobe"] = shutil.which("ffprobe")
except Exception as exc:
    out["which_error"] = "%s: %s" % (type(exc).__name__, exc)

try:
    import pydub.utils
    out["prober"] = pydub.utils.get_prober_name()
except Exception as exc:
    out["prober_error"] = "%s: %s" % (type(exc).__name__, exc)

# (b) pydub decodes a real MP3 - the call every re-voiced sentence makes
try:
    from pydub import AudioSegment
    out["decoded_ms"] = len(AudioSegment.from_file(mp3))
except Exception as exc:
    out["decode_error"] = "%s: %s" % (type(exc).__name__, exc)

# (c) the re-voice's own decode path. calibrate_tts_baseline is the smallest
# real call in services.processing that reaches AudioSegment.from_file: the
# re-voice runs it to measure the speaking rate before it synthesises anything.
# It swallows a failed sample and returns DEFAULT_BASELINE_RATE, so "did it get
# through" is "did it measure something other than the default".
try:
    from services.processing import calibrate_tts_baseline, DEFAULT_BASELINE_RATE

    class _FakeTTS(object):
        def generate_audio(self, text, voice_id, output_path, speed=1.0):
            shutil.copyfile(mp3, str(output_path))

    segments = [{"text": "The quick brown fox jumps over the lazy dog today."}]
    with tempfile.TemporaryDirectory() as td:
        rate = calibrate_tts_baseline(
            segments, _FakeTTS(), Path(td),
            provider="edge_tts", voice_id="en-US-AriaNeural",
        )
    out["baseline_rate"] = rate
    out["default_rate"] = DEFAULT_BASELINE_RATE
except Exception as exc:
    out["revoice_error"] = "%s: %s" % (type(exc).__name__, exc)

# (d) the NARRATED RENDER's decode path. create_video always builds one master
# track first, and _build_master_audio decodes every slide's narration MP3 with
# a bare from_file and nothing around it: a failed decode fails the render. So
# a missing prober breaks generating a narrated deck, not only the re-voice.
#
# The error is caught INSIDE the temporary directory: when the prober cannot be
# started pydub leaves the MP3 open, and on Windows the directory's cleanup then
# fails with [WinError 32] and replaces the error that matters.
try:
    from core.video_creator import _build_master_audio, SlideClipInfo

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        try:
            slide_mp3 = Path(td) / "slide_001_audio.mp3"
            shutil.copyfile(mp3, str(slide_mp3))  # the build trims the file in place
            master_path, slide_info = _build_master_audio(
                [SlideClipInfo(0, audio_path=slide_mp3)],
                voice_start_delay=0.0, transition_pause=0.0,
            )
            out["render_slide_s"] = slide_info[0] if slide_info else None
            if master_path is not None and Path(master_path).exists():
                os.remove(str(master_path))
        except Exception as exc:
            out["render_error"] = "%s: %s" % (type(exc).__name__, exc)
except Exception as exc:
    out["render_error"] = "%s: %s" % (type(exc).__name__, exc)

sys.stdout.write(json.dumps(out))
'''


def _python_for_probe() -> Path:
    """The vendored runtime when it is here (that is what ships), else this one."""
    return VENDOR_PY if VENDOR_PY.is_file() else Path(sys.executable)


def _run_probe(tmp_path: Path, path_dirs, mp3: Path, app_root: Path | None = None) -> dict:
    script = tmp_path / "_clean_machine_probe.py"
    script.write_text(_PROBE, encoding="utf-8")

    # A deliberately bare environment: PATH is the whole point, and the rest is
    # only what Windows needs to start a process at all.
    env = {
        "PATH": os.pathsep.join(str(d) for d in path_dirs),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
        "WINDIR": os.environ.get("WINDIR", r"C:\Windows"),
        "TEMP": str(tmp_path),
        "TMP": str(tmp_path),
        "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
        "NUMBER_OF_PROCESSORS": os.environ.get("NUMBER_OF_PROCESSORS", "1"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    argv = [str(_python_for_probe()), "-B", str(script), str(ROOT), str(mp3)]
    if app_root is not None:
        argv.append(str(app_root))
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=600, env=env)
    assert proc.returncode == 0, (
        f"the probe process exited {proc.returncode}\n"
        f"stdout: {proc.stdout[-2000:]}\nstderr: {proc.stderr[-2000:]}"
    )
    got = json.loads(proc.stdout)
    assert "config_error" not in got, f"utils.config failed to import: {got['config_error']}"
    return got


def _link_or_copy(src: Path, dst: Path) -> None:
    """Hard-link, so a test does not copy 84 MB; copy when a link cannot be made."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


SYSTEM32 = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32"


def _installed_layout(tmp_path: Path, *binaries: str) -> Path:
    """An app root shaped like an install: this tree's ``utils/`` - so
    ``utils.config.APP_DIR`` IS the root - and a ``bin/`` holding the named
    staged binaries. The root's name has a space in it, as a per-user install
    under "Media Studio Enterprise" does."""
    root = tmp_path / "Media Studio app"
    shutil.copytree(ROOT / "utils", root / "utils", ignore=shutil.ignore_patterns("__pycache__"))
    (root / "bin").mkdir()
    for name in binaries:
        _link_or_copy(_staged(name), root / "bin" / name)
    return root


@pytest.fixture(scope="module")
def bundled_mp3(tmp_path_factory):
    """A real MP3, encoded by the ffmpeg that ships - not a fixture checked in.

    An MP3 because that is what the re-voice writes for every sentence, and
    because a ``.wav`` is the one format pydub decodes with the stdlib and no
    prober at all: a WAV fixture would pass this file without ffprobe existing.
    """
    _skip_unless_staged()
    ffmpeg = _staged("ffmpeg.exe")
    if not ffmpeg.is_file():
        pytest.skip("no staged ffmpeg.exe to encode a fixture with")

    out = tmp_path_factory.mktemp("bundled") / "tone.mp3"
    proc = subprocess.run(
        [
            str(ffmpeg), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:a", "libmp3lame", "-q:a", "9", "-y", str(out),
        ],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0 and out.is_file(), (
        f"the bundled ffmpeg could not encode the fixture: {proc.stderr[:500]}"
    )
    return out


def test_clean_machine_revoice_decodes_with_only_app_bin_on_path(tmp_path, bundled_mp3):
    """The whole point, end to end.

    PATH is cut to the one directory the installer creates. No system ffmpeg,
    no WinGet, nothing inherited - which is what a customer's machine looks
    like. Then: the shipped prober is found, pydub decodes a real MP3, the
    re-voice's own decode path measures a rate instead of falling back, and a
    narrated render builds its master track.
    """
    _skip_unless_staged()
    if not _staged("ffprobe.exe").is_file():
        pytest.skip("no staged ffprobe.exe - run 'npm run fetch:ffprobe' then 'npm run stage:app'")

    got = _run_probe(tmp_path, [STAGED_BIN], bundled_mp3)

    # (a)
    assert got.get("which_ffprobe"), f"nothing on the cut-down PATH resolves ffprobe: {got}"
    assert Path(got["which_ffprobe"]).parent == STAGED_BIN, (
        f"ffprobe resolved to {got['which_ffprobe']}, not the shipped one"
    )
    assert got.get("ffprobe_path"), f"utils.config resolved no ffprobe: {got}"
    assert got.get("prober") == got["ffprobe_path"], (
        f"pydub would spawn {got.get('prober')!r}, not the resolved prober: {got}"
    )

    # (b)
    assert "decode_error" not in got, f"pydub could not decode the MP3: {got['decode_error']}"
    assert 1500 < got["decoded_ms"] < 2500, f"decoded a 2 s tone as {got['decoded_ms']} ms"

    # (c)
    assert "revoice_error" not in got, f"the re-voice decode path failed: {got['revoice_error']}"
    assert got["baseline_rate"] != got["default_rate"], (
        "calibrate_tts_baseline returned the fallback rate, which is what it does "
        f"when every sample fails to decode: {got}"
    )

    # (d)
    _assert_narrated_render_decodes(got)

    # Both binaries came from one place (the PATH), so there is nothing to warn about.
    assert got["pair_warning"] is None, got["pair_warning"]
    assert got["missing_warning"] is None, got["missing_warning"]


def test_clean_machine_without_ffprobe_is_the_0_9_0_failure(tmp_path, bundled_mp3):
    """The negative, kept rather than performed once.

    Same cut-down PATH, ffmpeg still on it, ffprobe removed - which is exactly
    what 0.9.0's installer produced. If this ever stops failing, the decode has
    found a prober somewhere this file does not know about, and the check above
    has stopped proving anything.
    """
    _skip_unless_staged()
    ffmpeg = _staged("ffmpeg.exe")
    if not ffmpeg.is_file():
        pytest.skip("no staged ffmpeg.exe")

    # A PATH with ffmpeg and no ffprobe.
    lone = tmp_path / "ffmpeg-only"
    lone.mkdir()
    _link_or_copy(ffmpeg, lone / "ffmpeg.exe")

    got = _run_probe(tmp_path, [lone], bundled_mp3)

    assert not got.get("which_ffprobe"), f"something still resolves ffprobe: {got}"
    assert got.get("ffprobe_path") in (None, ""), f"utils.config found a prober anyway: {got}"
    _assert_missing_prober(got)
    # APP_DIR here is the repo root, which has no bin\: a source checkout, a
    # developer's machine. It lacks a prober, but it is not a broken install,
    # so the missing-prober warning stays silent.
    assert got["missing_warning"] is None, got["missing_warning"]


def _assert_missing_prober(got: dict) -> None:
    """Red for the RIGHT reason: pydub spawning a prober that is not there.

    An ImportError or a bad fixture would also be 'a decode error' - and a
    MISSING INPUT FILE is even a FileNotFoundError, ``[Errno 2]``, raised when
    pydub opens it. A prober that cannot be started is ``[WinError 2]``:
    Windows failing to create the process. That is the 0.9.0 failure."""
    assert "decode_error" in got, f"pydub decoded an MP3 with no prober present: {got}"
    assert got["decode_error"].startswith("FileNotFoundError"), (
        f"the decode failed, but not because the prober is missing: {got['decode_error']}"
    )
    if os.name == "nt":
        assert "[WinError 2]" in got["decode_error"], (
            f"the decode failed, but not because the prober is missing: {got['decode_error']}"
        )
    assert got.get("prober") == "ffprobe", (
        f"with no prober found pydub should fall back to the bare name: {got.get('prober')!r}"
    )
    assert got.get("baseline_rate", got.get("default_rate")) == got["default_rate"], (
        "the re-voice decode path measured a rate with no prober present - "
        f"then the positive test proves nothing: {got}"
    )
    # ... and the narrated render dies the same way: 0.9.0 could not generate a
    # narrated deck on a clean machine either.
    assert "render_error" in got, f"a narrated render built its track with no prober: {got}"
    assert got["render_error"].startswith("FileNotFoundError"), got["render_error"]
    if os.name == "nt":
        assert "[WinError 2]" in got["render_error"], got["render_error"]


def _assert_narrated_render_decodes(got: dict) -> None:
    assert "render_error" not in got, f"the narrated render's decode failed: {got['render_error']}"
    assert got.get("render_slide_s") and 1.5 < got["render_slide_s"] < 2.5, (
        f"the master track holds {got.get('render_slide_s')} s for a 2 s slide: {got}"
    )


# --- the installed layout: APP_DIR/bin, the resolution a customer machine uses ---

def test_clean_machine_installed_layout_needs_nothing_from_path(tmp_path, bundled_mp3):
    """The positive case the way an install is actually shaped.

    The PATH-based test above proves pydub finds a prober on PATH. An install
    does not rely on that: ``utils.config`` takes both binaries from
    ``APP_DIR/bin`` by absolute path. So here PATH holds System32 and nothing
    else - no binary on it could be found - and the decode must still succeed.
    """
    _skip_unless_pair_staged()
    root = _installed_layout(tmp_path, "ffmpeg.exe", "ffprobe.exe")

    got = _run_probe(tmp_path, [SYSTEM32], bundled_mp3, app_root=root)

    assert got["app_dir"] == str(root), f"utils.config did not load from the layout: {got}"
    assert got["which_ffprobe_before_config"] is None, (
        f"the environment alone resolves an ffprobe, so this proves nothing: {got}"
    )
    assert got["ffmpeg_path"] == str(root / "bin" / "ffmpeg.exe"), got
    assert got["ffprobe_path"] == str(root / "bin" / "ffprobe.exe"), got
    assert got["prober"] == got["ffprobe_path"], got
    assert "decode_error" not in got, f"pydub could not decode the MP3: {got['decode_error']}"
    assert 1500 < got["decoded_ms"] < 2500, got
    assert got["baseline_rate"] != got["default_rate"], got
    _assert_narrated_render_decodes(got)
    assert got["pair_warning"] is None, got["pair_warning"]
    assert got["missing_warning"] is None, got["missing_warning"]


def test_clean_machine_installed_layout_with_ffmpeg_only_is_a_self_updated_0_9_0(tmp_path, bundled_mp3):
    """0.9.0's ``bin`` under 0.9.1's code - what Settings > Updates leaves on a
    machine with no ffmpeg of its own. The shipped ffmpeg is used, there is no
    prober anywhere, and the decode fails as 0.9.0's did. Nothing is mixed, so
    no pair warning - but this is the worst state an install can be in, and
    the start must say so before the first re-voice or render dies."""
    _skip_unless_pair_staged()
    root = _installed_layout(tmp_path, "ffmpeg.exe")

    got = _run_probe(tmp_path, [SYSTEM32], bundled_mp3, app_root=root)

    assert got["ffmpeg_path"] == str(root / "bin" / "ffmpeg.exe"), got
    assert got["ffprobe_path"] is None, got
    _assert_missing_prober(got)
    assert got["pair_warning"] is None, got["pair_warning"]

    warning = got["missing_warning"]
    assert warning, f"a shipped ffmpeg with no prober anywhere started without a word: {got}"
    for needle in (str(root / "bin" / "ffmpeg.exe"), "ffprobe.exe", "installer",
                   "re-voice", "narrated render"):
        assert needle in warning, f"the warning does not say {needle!r}:\n{warning}"
    assert got["missing_warning_logged"], "the warning was worked out but never reached app.log"


def test_mixed_pair_warning_is_logged_at_startup(tmp_path, bundled_mp3):
    """0.9.0's ``bin`` under 0.9.1's code on a machine that HAS its own
    ffprobe: the owner's decision is that this works and is logged. The
    decode succeeds with the machine's prober, and the start writes a warning -
    naming both binaries - into app.log, where a support request will find it.
    """
    _skip_unless_pair_staged()
    root = _installed_layout(tmp_path, "ffmpeg.exe")
    machine = tmp_path / "machine ffmpeg" / "bin"
    machine.mkdir(parents=True)
    _link_or_copy(_staged("ffprobe.exe"), machine / "ffprobe.exe")

    got = _run_probe(tmp_path, [SYSTEM32, machine], bundled_mp3, app_root=root)

    assert got["ffmpeg_path"] == str(root / "bin" / "ffmpeg.exe"), got
    assert Path(got["ffprobe_path"]).parent == machine, got
    warning = got["pair_warning"]
    assert warning, f"a shipped ffmpeg ran with a foreign prober and nothing said so: {got}"
    assert str(root / "bin" / "ffmpeg.exe") in warning and got["ffprobe_path"] in warning, warning
    assert warning.count(BUILD_TOKEN) == 2, f"the warning must name both versions:\n{warning}"
    assert "installer" in warning, warning
    assert got["pair_warning_logged"], "the warning was worked out but never reached app.log"
    assert got["missing_warning"] is None, "a prober WAS found - this is not the missing-prober state"
    assert "decode_error" not in got, f"the mixed pair should still decode: {got['decode_error']}"
    assert 1500 < got["decoded_ms"] < 2500, got
    _assert_narrated_render_decodes(got)
