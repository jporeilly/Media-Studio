"""Q1: the encode every render writes, held to what its label says.

A final deck render hands ffmpeg the output preset's x264 preset
(``medium``), H.264 profile (High), video bitrate and AAC bitrate, plus
``-pix_fmt yuv420p``, and its last write ``-movflags +faststart``
(``VideoCreator.encode_settings``, the one place the render takes them from,
as ffmpeg arguments since T1). The 15-second preview is the same render at
``ultrafast``. The
re-voice has no preset of its own: its picture cut is encoded like a final
render and told the source's frame rate, its mux and its music pass give AAC
one named bitrate at 48 kHz stereo (the unity up-mix, never ``-ac 2``), its
master reaches the mux as a lossless WAV, and a re-voice with no cut still
copies the source's picture untouched.

The re-voice is read back too: a real job through the route - the cut, the
mux, the music pass and the chapter remux on the real binary, the voice
service alone faked with dense noise in Edge's own format - and the file's
sample rate, channels, bitrate, level, the voice's loudness and the index's
place are read from what it wrote. Before this, no test read any re-voice
output back, which is how every one of them came to be 24 kHz AAC at about
100 kbit/s while the guides said 192.

The real-binary checks render a one-slide synthetic deck through
``create_video`` itself on every real ffmpeg on the machine (the one the app
resolves here and, where the product is installed, the 7.1 build it ships -
the skip rule of ``test_generation_options``), then read the file back with
the prober beside that binary: the profile, the pixel format, the audio
bitrate, and whether the index (``moov``) sits in front of the media
(``mdat``). Two parameters leave nothing in the file to read when they are
missing - ``-profile:v`` is a ceiling and ``medium`` writes High without it,
and the render's graph ends in yuv420p anyway (see ``core.video_creator``) -
so the encoder's own command line is checked too: the chunk's encode and the
join that writes the file.
"""

import json
import subprocess
import time
import types
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import utils.config as config_module
from api import store as auth_store
from core import video_creator
from services import music, output_presets, processing
from services import projects as store
from utils.config import config

from test_chapters import _ffprobe_beside
from test_generation_options import KEEP, REAL_FFMPEGS, _fake_cut, _mux_fixtures, needs_ffmpeg

PARAMS = ["-profile:v", "high", "-pix_fmt", "yuv420p"]
FASTSTART = ["-movflags", "+faststart"]
# How ffprobe names the stream each ``profile`` value produces at ``medium``.
PROFILE_NAMES = {"main": "Main", "high": "High"}


def _distinct_encodes() -> list[str]:
    """One preset id per DISTINCT encode, in declaration order. The presets
    differ in resolution too, which is not an encode parameter (and a 4K
    render would only make the test slow), so a preset whose encode an
    earlier one already has adds nothing to a real render."""
    seen, ids = set(), []
    for p in output_presets.list_presets():
        key = (p["x264_preset"], p["profile"], p["video_bitrate"], p["audio_bitrate"])
        if key not in seen:
            seen.add(key)
            ids.append(p["id"])
    return ids


DISTINCT_ENCODES = _distinct_encodes()


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)


def _contains(cmd: list, part: list) -> bool:
    """Whether ``part`` appears in ``cmd`` as one contiguous run."""
    return any(cmd[i:i + len(part)] == part for i in range(len(cmd) - len(part) + 1))


# ── the settings, without a binary ────────────────────────────────────────────

@pytest.mark.parametrize("x264_preset", ["medium", "ultrafast"])
def test_the_encode_settings_carry_the_preset_and_the_same_parameters_whatever_the_speed(x264_preset):
    creator = video_creator.VideoCreator(
        resolution=(320, 240), video_bitrate="10M", x264_preset=x264_preset, h264_profile="high", audio_bitrate="192k",
    )
    assert creator.encode_settings() == {
        "video": ["-r", str(video_creator.STATIC_FPS), "-c:v", "libx264", "-preset", x264_preset, *PARAMS,
                  "-b:v", "10M", "-threads", "0"],
        "audio": ["-c:a", "aac", "-b:a", "192k"],
        "container": FASTSTART,
    }
    # "" is the codec's own default, exactly as the video bitrate has always been.
    plain = video_creator.VideoCreator(resolution=(320, 240)).encode_settings()
    assert "-b:v" not in plain["video"] and plain["audio"] == ["-c:a", "aac"]


class _FakePM:
    def __init__(self):
        self.state = types.SimpleNamespace(slides=[])


def _capture_creator(monkeypatch, tmp_path) -> list[dict]:
    seen: list[dict] = []

    class _FakeCreator:
        def __init__(self, **kwargs):
            seen.append(kwargs)

        def create_video(self, **kwargs):
            return True

    monkeypatch.setattr(processing, "VideoCreator", _FakeCreator)
    monkeypatch.setattr(processing, "TEMP_DIR", tmp_path / "temp")
    return seen


@pytest.mark.parametrize("preset_id", [p["id"] for p in output_presets.list_presets()])
def test_the_final_render_gets_the_presets_encode_and_the_preview_keeps_ultrafast(tmp_path, monkeypatch, preset_id):
    """The processor hands the VideoCreator the preset's encode as the
    generate route passed it; a preview keeps all of it but the x264 preset,
    which is ``ultrafast`` - it is a look at the opening seconds, not a
    deliverable, so Preview stays quick while the final render pays for
    ``medium``."""
    preset = output_presets.get_preset(preset_id)
    seen = _capture_creator(monkeypatch, tmp_path)
    processor = processing.VideoProcessor(
        resolution=tuple(preset["resolution"]), video_bitrate=preset["video_bitrate"],
        x264_preset=preset["x264_preset"], h264_profile=preset["profile"], audio_bitrate=preset["audio_bitrate"],
    )

    assert processor._build_video(_FakePM(), tmp_path / "deck.mp4")
    assert processor._build_video(_FakePM(), tmp_path / "deck_preview.mp4", preview_seconds=15)

    final, preview = seen
    encode = ("x264_preset", "h264_profile", "audio_bitrate", "video_bitrate")
    assert [final[key] for key in encode] == [
        preset["x264_preset"], preset["profile"], preset["audio_bitrate"], preset["video_bitrate"],
    ]
    assert preview["x264_preset"] == output_presets.PREVIEW_X264_PRESET == "ultrafast"
    assert [preview[key] for key in encode[1:]] == [final[key] for key in encode[1:]], (
        "the preview keeps the profile, the audio bitrate and the video bitrate"
    )


# ── the re-voice's argv ───────────────────────────────────────────────────────

def test_the_cut_picture_is_encoded_like_a_final_render(tmp_path, monkeypatch):
    """The cut is the picture the re-voiced file carries, so it gets the final
    render's encode: x264 ``medium``, High, 4:2:0 (it was ``ultrafast``,
    which x264 flags Constrained Baseline)."""
    started = _fake_cut(monkeypatch)
    source = tmp_path / "finished.mp4"
    source.write_bytes(b"mp4")

    assert video_creator.cut_picture(source, KEEP, tmp_path / "cut.mp4") is True
    (proc,) = started
    cmd = proc.cmd
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-preset") + 1] == video_creator.REVOICE_X264_PRESET == "medium"
    assert _contains(cmd, ["-profile:v", "high", "-pix_fmt", "yuv420p"]), cmd
    assert "ultrafast" not in cmd
    # The source's rate, told to x264 alone: never -r, which would make a
    # variable-rate recording constant by duplicating and dropping frames.
    assert cmd[cmd.index("-x264-params") + 1] == "fps=30" and "-r" not in cmd


def test_the_source_frame_rate_is_read_from_ffmpegs_header(monkeypatch):
    """The base rate ffmpeg prints as "<n> tbr" - the nominal rate of a
    variable-rate recording too - and nothing when there is none or it is a
    time base rather than a picture's rate ("1k tbr", or anything above
    ``MAX_FRAME_RATE``: an audio file's cover art reads "90k tbr")."""
    line = "  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p, 640x360, 641 kb/s, {} (default)\n"
    for header, rate in [
        (line.format("22.69 fps, 30 tbr, 15360 tbn"), "30"),
        (line.format("29.97 fps, 29.97 tbr, 30k tbn"), "29.97"),
        (line.format("25 fps, 25 tbr, 12800 tbn"), "25"),
        (line.format("1k fps, 1k tbr, 1k tbn"), None),
        (line.format("240 fps, 240 tbr, 15360 tbn"), "240"),   # the ceiling itself is a picture's rate
        (line.format("300 fps, 300 tbr, 19200 tbn"), None),    # above MAX_FRAME_RATE: a time base, not a picture
        ("  Stream #0:0: Audio: aac (LC), 48000 Hz, stereo, fltp, 192 kb/s\n", None),
        ("", None),
    ]:
        monkeypatch.setattr(video_creator, "_ffmpeg_header", lambda path, header=header: header)
        assert video_creator.source_frame_rate("clip.mp4") == rate, header


def test_the_mux_copies_the_picture_and_gives_the_voice_48k_stereo_at_the_revoice_bitrate(tmp_path, monkeypatch):
    """A re-voice with no cut muxes onto the SOURCE's picture, copied
    untouched - no libx264 anywhere in the command. The narration is up-mixed
    to stereo at unity (the music graph's own ``pan``, never ``-ac 2``),
    resampled to 48 kHz - at the TTS's 24 kHz the AAC could not carry its
    bitrate - and given the re-voice's one bitrate; the file gets faststart,
    because with no music and no markers it is the file the user downloads."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", "ffmpeg-test")
    video, master = _mux_fixtures(tmp_path)
    cmd = video_creator._build_replace_audio_cmd(video, master, tmp_path / "out.mp4", 5.0)

    assert cmd[cmd.index("-c:v") + 1] == "copy" and "libx264" not in cmd and "-preset" not in cmd
    assert cmd[cmd.index("-af") + 1] == "pan=stereo|FL=FL+FC|FR=FR+FC,aresample=48000,apad=whole_dur=5.000"
    assert "-ac" not in cmd and "-ar" not in cmd, "the chain does both, at unity"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert cmd[cmd.index("-b:a") + 1] == video_creator.REVOICE_AUDIO_BITRATE == "192k"
    assert _contains(cmd, ["-movflags", "+faststart"]), cmd


# ── the real binary ───────────────────────────────────────────────────────────

def _top_level_atoms(path: Path) -> list[str]:
    """The MP4's top-level boxes in file order (``ftyp``, ``moov``, ``mdat`` ...):
    a 32-bit size and a 4-character type each, a size of 1 meaning a 64-bit
    size follows and 0 meaning "to the end of the file"."""
    names, total, pos = [], path.stat().st_size, 0
    with open(path, "rb") as fh:
        while pos + 8 <= total:
            fh.seek(pos)
            header = fh.read(16)
            size, kind = int.from_bytes(header[:4], "big"), header[4:8].decode("latin-1")
            if size == 1:
                size = int.from_bytes(header[8:16], "big")
            elif size == 0:
                size = total - pos
            names.append(kind)
            if size < 8:
                break
            pos += size
    return names


def _index_in_front(path: Path) -> bool:
    atoms = _top_level_atoms(path)
    return "moov" in atoms and "mdat" in atoms and atoms.index("moov") < atoms.index("mdat")


def _streams(ffprobe: Path, video: Path) -> dict:
    out = subprocess.run(
        [str(ffprobe), "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,profile,level,pix_fmt,bit_rate,sample_rate,channels",
         "-of", "json", str(video)],
        capture_output=True, timeout=60, check=True,
    ).stdout.decode("utf-8", errors="replace")
    return {s["codec_type"]: s for s in json.loads(out)["streams"] if s.get("codec_type") in ("video", "audio")}


def _render(tmp_path, monkeypatch, ffmpeg: str, creator_kwargs: dict, titles=None) -> tuple[Path, list[str], list[str], list[str]]:
    """A one-slide deck through ``VideoCreator.create_video`` on ``ffmpeg``
    (the render's encode, its join and the chapter remux alike): a 320×240
    slide and 2 s of stereo pink noise - noise, because an AAC encoder spends
    its bitrate on it, where on silence or a pure tone it does not and the
    check would measure the content rather than the setting. Returns the file,
    the picture encoder's own command line, the sound encoder's, and the
    join's (the write that gives the file its index)."""
    ffprobe = _ffprobe_beside(ffmpeg)
    if ffprobe is None:
        pytest.skip(f"no ffprobe beside {ffmpeg}")
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    monkeypatch.setattr(config_module, "TEMP_DIR", tmp_path / "temp")

    image = tmp_path / "slide.png"
    Image.new("RGB", (320, 240), (40, 90, 160)).save(image)
    narration = tmp_path / "slide.mp3"
    made = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "anoisesrc=color=pink:sample_rate=44100:duration=2,aformat=channel_layouts=stereo",
         "-c:a", "libmp3lame", "-b:a", "320k", str(narration)],
        capture_output=True, text=True, timeout=120,
    )
    if made.returncode != 0:
        pytest.skip(f"this ffmpeg cannot make the fixture: {made.stderr[-200:]}")

    seen: list[list[str]] = []
    real_popen = subprocess.Popen

    class _Recording(real_popen):
        def __init__(self, cmd, *args, **kwargs):
            seen.append([str(part) for part in cmd] if isinstance(cmd, (list, tuple)) else [str(cmd)])
            super().__init__(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", _Recording)
    creator = video_creator.VideoCreator(
        resolution=(320, 240), transition_pause=0.0, voice_start_delay=0.0, **creator_kwargs,
    )
    out = tmp_path / "deck.mp4"
    assert creator.create_video(
        [video_creator.SlideClipInfo(slide_index=0, image_path=image, audio_path=narration)],
        out, slide_titles=titles,
    ) is True
    (encode,) = [cmd for cmd in seen if "-c:v" in cmd and "libx264" in cmd]
    (sound,) = [cmd for cmd in seen if "-c:a" in cmd and "sound.m4a" in cmd]
    (join,) = [cmd for cmd in seen if "concat" in cmd and "-c" in cmd and "copy" in cmd]
    return out, encode, sound, join


def _creator_kwargs(preset: dict, x264_preset: str | None = None) -> dict:
    return {
        "video_bitrate": preset["video_bitrate"], "x264_preset": x264_preset or preset["x264_preset"],
        "h264_profile": preset["profile"], "audio_bitrate": preset["audio_bitrate"],
    }


def _audio_within(stream: dict, bitrate: str, tolerance: float = 0.15) -> bool:
    wanted = int(bitrate[:-1]) * 1000
    return abs(int(stream["bit_rate"]) - wanted) <= tolerance * wanted


@needs_ffmpeg
@pytest.mark.parametrize("preset_id", DISTINCT_ENCODES)
@pytest.mark.parametrize("ffmpeg", REAL_FFMPEGS, ids=lambda p: Path(p).parent.parent.name or "ffmpeg")
def test_a_final_render_is_what_its_preset_says(tmp_path, monkeypatch, ffmpeg, preset_id):
    """The file a final render writes, read back: H.264 High, 4:2:0, the AAC
    bitrate within 15 % of the preset's, and the index in front of the media.
    No slide titles, so no chapter remux runs after the encode: this is the
    render's OWN faststart (the remux's is the next test)."""
    preset = output_presets.get_preset(preset_id)
    out, encode, sound, join = _render(tmp_path, monkeypatch, ffmpeg, _creator_kwargs(preset))

    # The file first: what a player sees.
    streams = _streams(_ffprobe_beside(ffmpeg), out)
    video, audio = streams["video"], streams["audio"]
    assert (video["codec_name"], video["profile"], video["pix_fmt"]) == ("h264", PROFILE_NAMES[preset["profile"]], "yuv420p")
    assert audio["codec_name"] == "aac"
    assert _audio_within(audio, preset["audio_bitrate"]), (audio["bit_rate"], preset["audio_bitrate"])
    assert _index_in_front(out), _top_level_atoms(out)
    # Then the commands: the profile and the pixel format leave nothing in the
    # file to read when they are missing (see the module docstring).
    assert encode[encode.index("-preset") + 1] == preset["x264_preset"]
    assert _contains(encode, PARAMS), encode
    assert sound[sound.index("-c:a") + 1] == "aac" and sound[sound.index("-b:a") + 1] == preset["audio_bitrate"]
    if preset["video_bitrate"]:
        assert encode[encode.index("-b:v") + 1] == preset["video_bitrate"]
    else:
        assert "-b:v" not in encode
    assert _contains(join, FASTSTART) and join[join.index("-c") + 1] == "copy", join


@needs_ffmpeg
@pytest.mark.parametrize("ffmpeg", REAL_FFMPEGS, ids=lambda p: Path(p).parent.parent.name or "ffmpeg")
def test_the_chapter_remux_keeps_the_index_in_front(tmp_path, monkeypatch, ffmpeg):
    """A deck with slide titles gets its chapters by one stream-copy remux
    AFTER the encode - the last write of the file the user downloads - and a
    plain remux puts the index back at the end. So the remux passes faststart
    too, and the chaptered file still has it."""
    preset = output_presets.get_preset(output_presets.DEFAULT_PRESET_ID)
    out, _, _, _ = _render(tmp_path, monkeypatch, ffmpeg, _creator_kwargs(preset), titles=["Opening slide"])

    assert b"chpl" in out.read_bytes(), "the chapters were written"
    assert _index_in_front(out), _top_level_atoms(out)
    assert _streams(_ffprobe_beside(ffmpeg), out)["video"]["profile"] == "High", "a stream copy keeps the encode"


@needs_ffmpeg
@pytest.mark.parametrize("ffmpeg", REAL_FFMPEGS, ids=lambda p: Path(p).parent.parent.name or "ffmpeg")
def test_the_preview_is_ultrafast_and_plays_where_the_final_render_does(tmp_path, monkeypatch, ffmpeg):
    """The preview's encode: ``ultrafast``, with the final render's parameters,
    so it is 4:2:0 with its index in front and the preset's audio bitrate.
    Its stream is Constrained Baseline, not High: ``-profile:v high`` is a
    ceiling on the tools x264 may use, and ``ultrafast`` uses none of High's -
    a Baseline stream every High decoder plays."""
    preset = output_presets.get_preset(output_presets.DEFAULT_PRESET_ID)
    out, encode, _, join = _render(tmp_path, monkeypatch, ffmpeg,
                                   _creator_kwargs(preset, x264_preset=output_presets.PREVIEW_X264_PRESET))

    streams = _streams(_ffprobe_beside(ffmpeg), out)
    assert (streams["video"]["profile"], streams["video"]["pix_fmt"]) == ("Constrained Baseline", "yuv420p")
    assert _audio_within(streams["audio"], preset["audio_bitrate"]), streams["audio"]["bit_rate"]
    assert _index_in_front(out), _top_level_atoms(out)
    assert encode[encode.index("-preset") + 1] == "ultrafast"
    assert _contains(encode, PARAMS), encode
    assert _contains(join, FASTSTART), join


# ── the re-voice, read back ───────────────────────────────────────────────────
#
# A real re-voice job through the route: the real engine, the real cut (x264
# on the binary under test), the real mux, the real music pass and the real
# chapter remux. Only the voice service is faked, and it speaks dense noise in
# Edge's own format (24 kHz mono MP3 at 48 kbit/s): an AAC encoder spends its
# bitrate on noise, so the file's bitrate measures the setting, not the words.

REVOICE_SECONDS = 6.0
# About 1 s of speech per 15 characters (the fake voice's rate), each filling
# its window, so the file is narration rather than silence.
REVOICE_SENTENCES = [
    {"start": 0.1, "end": 2.9, "text": "The first sentence fills the opening part."},
    {"start": 3.1, "end": 5.9, "text": "The second one runs on to the very end, too."},
]
# The voice's level is compared inside the first sentence, where no music plays.
LEVEL_WINDOW = (0.6, 2.4)
REVOICE_PATHS = {
    # The mux is the file the user downloads: no cut, no music, no markers.
    "mux": {},
    # The music pass is: the picture cut, a clip under the second sentence.
    "music": {
        "video": [[0.0, 3.0], [3.05, 6.0]],
        "music": [{"id": "m", "file": "bed.wav", "at": 3.5, "in": 0.5, "out": 2.0,
                   "gain": 0.2, "fade_in": 0.0, "fade_out": 0.5}],
    },
    # The chapter remux is, after all three.
    "chapters": {
        "video": [[0.0, 3.0], [3.05, 6.0]],
        "music": [{"id": "m", "file": "bed.wav", "at": 3.5, "in": 0.5, "out": 2.0,
                   "gain": 0.2, "fade_in": 0.0, "fade_out": 0.5}],
        "markers": [{"id": "a", "at": 0.5, "name": "Opening"}, {"id": "b", "at": 3.4, "name": "Closing"}],
    },
}

# H.264 Table A-1: (level_idc, MaxMBPS, MaxFS) - macroblocks a second and a frame.
H264_LEVELS = [
    (10, 1485, 99), (11, 3000, 396), (12, 6000, 396), (13, 11880, 396), (20, 11880, 396),
    (21, 19800, 792), (22, 20250, 1620), (30, 40500, 1620), (31, 108000, 3600), (32, 216000, 5120),
    (40, 245760, 8192), (41, 245760, 8192), (42, 522240, 8704), (50, 589824, 22080), (51, 983040, 36864),
    (52, 2073600, 36864), (60, 4177920, 139264), (61, 8355840, 139264), (62, 16711680, 139264),
]


def _level_rows_that_fit(width: int, height: int, fps: float) -> list[int]:
    """The lowest level whose macroblock limits hold a picture of this size and
    rate, and the two after it (x264 steps up for its reference frames' DPB)."""
    frame = -(-width // 16) * -(-height // 16)
    fits = [level for level, mbps, fs in H264_LEVELS if mbps >= frame * fps and fs >= frame]
    return fits[:3]


class _NoiseVoice:
    """The voice service: dense pink noise, about one second per fifteen
    characters, written as Edge writes (24 kHz mono MP3, 48 kbit/s)."""

    ffmpeg = ""

    def generate_audio(self, text, voice_id=None, output_path=None, speed=1.0, **kwargs):
        seconds = max(1.0, len(text) / 15.0 / max(speed, 0.5))
        subprocess.run(
            [self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
             "-i", f"anoisesrc=color=pink:amplitude=0.25:sample_rate=24000:duration={seconds:.3f}",
             "-c:a", "libmp3lame", "-b:a", "48k", "-ac", "1", str(output_path)],
            check=True, capture_output=True, timeout=60,
        )
        return True


@pytest.fixture
def revoice_client(tmp_path, monkeypatch):
    """A logged-in TestClient with the store, the auth DB, the scratch and the
    music library isolated. The binary is set by each test."""
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(store, "_locks", {})
    monkeypatch.setattr(processing, "TEMP_DIR", tmp_path / "temp")
    monkeypatch.setattr(config_module, "TEMP_DIR", tmp_path / "temp")
    monkeypatch.setattr(music, "MUSIC_DIR", tmp_path / "music")
    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    monkeypatch.setattr(processing.VideoProcessor, "_create_tts_generator", lambda self: _NoiseVoice())
    monkeypatch.setattr(processing.VideoProcessor, "_calibrate_tts_baseline", lambda self, *a, **k: 15.0)

    from api.app import app

    with TestClient(app) as c:
        seeded = auth_store.authenticate("admin", "admin")
        auth_store.change_password(seeded["id"], "admin", must_change=False)
        assert c.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        yield c


def _revoice_project(ffmpeg: str, tmp_path: Path) -> str:
    """A 6 s 320×240 30 fps recording with a sound track, transcribed."""
    source = tmp_path / "recording.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=30:duration={REVOICE_SECONDS}",
         "-f", "lavfi", "-i", f"sine=frequency=330:duration={REVOICE_SECONDS}",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
        check=True, capture_output=True, timeout=120,
    )
    pid = store.import_upload("clip.mp4", source.read_bytes())["id"]
    store.set_transcript(pid, [dict(s) for s in REVOICE_SENTENCES])
    with wave.open(str(store.PROJECTS_DIR / pid / "audio.wav"), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(np.zeros(int(REVOICE_SECONDS * 16000), dtype="<i2").tobytes())
    return pid


def _first_channel(ffmpeg: str, media: Path, wav: Path, window: tuple[float, float]) -> np.ndarray:
    """The first channel over ``window``, decoded at 48 kHz with no down-mix
    (a down-mix would hide exactly the up-mix's level)."""
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(media), "-vn",
                    "-ar", "48000", "-c:a", "pcm_s16le", str(wav)], check=True, capture_output=True, timeout=60)
    with wave.open(str(wav), "rb") as wf:
        channels = wf.getnchannels()
        samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype(np.float64)
    return samples.reshape(-1, channels)[int(window[0] * 48000):int(window[1] * 48000), 0]


def _rms_db(samples: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(samples ** 2))))


def _share_above(samples: np.ndarray, hertz: float) -> float:
    """The fraction of the signal's energy above ``hertz``: what a 32 kbit/s
    MP3 master cuts. The fake voice (a 48 kbit/s 24 kHz MP3, as Edge writes)
    carries about 3 % of its energy above 9 kHz; a 32 kbit/s MP3 of it,
    pydub's default, carries none (measured on the bundled 7.1)."""
    power = np.abs(np.fft.rfft(samples)) ** 2
    return float(power[np.fft.rfftfreq(len(samples), 1 / 48000) > hertz].sum() / power.sum())


@needs_ffmpeg
@pytest.mark.parametrize("path", list(REVOICE_PATHS))
@pytest.mark.parametrize("ffmpeg", REAL_FFMPEGS, ids=lambda p: Path(p).parent.parent.name or "ffmpeg")
def test_a_revoice_file_carries_48k_stereo_at_its_bitrate_with_the_index_in_front(
    revoice_client, tmp_path, monkeypatch, ffmpeg, path,
):
    """Each writer of a re-voice file, read back from the file it wrote last:
    the mux (no edit), the music pass (a cut and a clip) and the chapter remux
    (a cut, a clip and markers).

    - AAC at 48 kHz stereo, given 192 kbit/s and reading at or a little under
      it (the narration here is dense; silence would read lower). Every
      re-voice file used to be 24 kHz - the TTS's own rate, where AAC wrote
      about 100 kbit/s whatever it was given - through a 32 kbit/s MP3 master.
    - The voice at its own level: up-mixed to stereo at unity, so the file's
      first channel is within a decibel of the standalone narration track
      (``-ac 2`` would put it 3 dB down).
    - The index in front of the media, from whichever pass wrote the file.
    - A cut picture declares an H.264 level its size and rate fit (it declared
      6.2 for any size: see ``cut_picture``).
    """
    ffprobe = _ffprobe_beside(ffmpeg)
    if ffprobe is None:
        pytest.skip(f"no ffprobe beside {ffmpeg}")
    from pydub import AudioSegment

    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    monkeypatch.setattr(AudioSegment, "converter", ffmpeg)
    monkeypatch.setattr(_NoiseVoice, "ffmpeg", ffmpeg)

    pid = _revoice_project(ffmpeg, tmp_path)
    edit_body = REVOICE_PATHS[path]
    if "music" in edit_body:
        bed = tmp_path / "bed.wav"
        subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "anoisesrc=color=pink:amplitude=0.25:sample_rate=44100:duration=4,"
                              "aformat=channel_layouts=stereo",
                        "-c:a", "pcm_s16le", str(bed)], check=True, capture_output=True, timeout=60)
        music.add_file("bed.wav", bed.read_bytes(), "u")
    if edit_body:
        r = revoice_client.put(f"/api/projects/{pid}/edit", json=edit_body)
        assert r.status_code == 200, r.text

    r = revoice_client.post(f"/api/projects/{pid}/revoice", json={"provider": "edge_tts", "voice_id": "en-US-AriaNeural"})
    assert r.status_code == 200, r.text
    deadline = time.time() + 300
    while (job := revoice_client.get(f"/api/jobs/{r.json()['job_id']}").json())["status"] not in ("done", "error"):
        assert time.time() < deadline, "the re-voice did not finish"
        time.sleep(0.1)
    assert job["status"] == "done", job
    record = store.get_project(pid)
    out = store.PROJECTS_DIR / pid / record["revoiced_video"]
    track = store.PROJECTS_DIR / pid / record["narration_audio"]

    streams = _streams(ffprobe, out)
    audio = streams["audio"]
    assert (audio["codec_name"], audio["sample_rate"], audio["channels"]) == ("aac", "48000", 2), audio
    bitrate = int(audio["bit_rate"])
    assert 0.85 * 192_000 <= bitrate <= 1.05 * 192_000, f"given 192k, read {bitrate}"
    assert _index_in_front(out), _top_level_atoms(out)

    voice = _first_channel(ffmpeg, out, tmp_path / "out.wav", LEVEL_WINDOW)
    alone = _first_channel(ffmpeg, track, tmp_path / "track.wav", LEVEL_WINDOW)
    drop = _rms_db(voice) - _rms_db(alone)
    assert abs(drop) < 1.0, f"the voice is {drop:+.2f} dB against its own track (-ac 2 is -3.01)"
    # The narration kept its band: the mux read a lossless master, not a
    # 32 kbit/s MP3 of it (which keeps nothing above 9 kHz).
    assert _share_above(voice, 9000) > 0.5 * _share_above(alone, 9000), (
        _share_above(voice, 9000), _share_above(alone, 9000))
    # The standalone track is an MP3 at the most 24 kHz allows, not pydub's 32k.
    assert int(_streams(ffprobe, track)["audio"]["bit_rate"]) >= 150_000, _streams(ffprobe, track)

    if "video" in edit_body:
        level = int(streams["video"]["level"])
        assert level in _level_rows_that_fit(320, 240, 30), f"a 320x240 30 fps cut declares level {level}"
    if "markers" in edit_body:
        assert b"chpl" in out.read_bytes(), "the chapters were written"
