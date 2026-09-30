"""Configuration management for the application."""

import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Pass through Ollama GPU config to environment (Ollama reads this at runtime)
_ollama_num_gpu = os.getenv("OLLAMA_NUM_GPU")
if _ollama_num_gpu:
    os.environ["OLLAMA_NUM_GPU"] = _ollama_num_gpu

# Application paths
APP_DIR = Path(__file__).parent.parent

CONFIG_DIR = APP_DIR / "data"
CONFIG_FILE = CONFIG_DIR / "config.json"
CACHE_DIR = CONFIG_DIR / "cache"
TEMP_DIR = CONFIG_DIR / "temp"

# Ensure directories exist
CONFIG_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)
(APP_DIR / "assets" / "finished").mkdir(parents=True, exist_ok=True)


def _shipped_ffmpeg_paths() -> tuple[str | None, str | None]:
    """The ffmpeg/ffprobe the INSTALLER put in the app's own ``bin/``.

    ``desktop/scripts/stage-app.ps1`` stages both there and ``desktop/boot.py``
    prepends that directory to PATH, so a plain ``shutil.which`` would usually
    find them anyway. "Usually" is the problem: PATH order is an environment
    accident, and a machine with its own ffmpeg installed ahead of the app's
    would silently pair a different build with the one the app was tested
    against. 7.1 and 8.0.1 already differ where this app feels it. Taking them
    by absolute path makes the pair explicit rather than lucky.

    Each is resolved on its own: an install that self-updates from 0.9.0 gets
    the new code but keeps its old ``bin/`` (the update pulls the checkout, not
    the binaries), so ffmpeg can be there with no ffprobe beside it. Then the
    prober comes from the machine, if it has one - allowed, because refusing it
    would break a machine that otherwise works, but never silent: see
    :func:`_mixed_pair_warning`.
    """
    suffix = ".exe" if os.name == "nt" else ""
    bin_dir = APP_DIR / "bin"
    ffmpeg = bin_dir / f"ffmpeg{suffix}"
    ffprobe = bin_dir / f"ffprobe{suffix}"
    return (
        str(ffmpeg) if ffmpeg.is_file() else None,
        str(ffprobe) if ffprobe.is_file() else None,
    )


def _get_ffmpeg_paths() -> tuple[str | None, str | None]:
    """Find ffmpeg and ffprobe executable paths.

    Priority: the app's own ``bin/`` > system PATH > static-ffmpeg >
    imageio-ffmpeg. Returns (ffmpeg_path, ffprobe_path) — either may be None.
    """
    shipped_ffmpeg, shipped_ffprobe = _shipped_ffmpeg_paths()
    if shipped_ffmpeg and shipped_ffprobe:
        return shipped_ffmpeg, shipped_ffprobe

    found_ffmpeg, found_ffprobe = _discovered_ffmpeg_paths()
    # A shipped binary always beats a discovered one of unknown version; the
    # discovery only fills a gap the install left.
    return (shipped_ffmpeg or found_ffmpeg), (shipped_ffprobe or found_ffprobe)


def _discovered_ffmpeg_paths() -> tuple[str | None, str | None]:
    """Find ffmpeg/ffprobe anywhere on the machine (source checkout, dev box)."""
    import shutil

    # 1. System PATH
    system_ffmpeg = shutil.which("ffmpeg")
    system_ffprobe = shutil.which("ffprobe")
    if system_ffmpeg and system_ffprobe:
        return system_ffmpeg, system_ffprobe

    # 2. static-ffmpeg (ships both ffmpeg and ffprobe)
    try:
        import static_ffmpeg
        ffmpeg_exe, ffprobe_exe = static_ffmpeg.run.get_or_fetch_platform_executables_else_raise()
        if ffmpeg_exe and Path(ffmpeg_exe).exists():
            return ffmpeg_exe, ffprobe_exe
    except (ImportError, Exception):
        pass

    # 3. imageio-ffmpeg (ships ffmpeg only — no ffprobe)
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        if ffmpeg_exe and Path(ffmpeg_exe).exists():
            return ffmpeg_exe, system_ffprobe  # ffprobe may still be None
    except ImportError:
        pass

    return system_ffmpeg, system_ffprobe


# Resolve FFmpeg/ffprobe paths once at import time
FFMPEG_PATH, _FFPROBE_PATH = _get_ffmpeg_paths()

# Configure pydub to use the discovered binaries
if FFMPEG_PATH:
    _ffmpeg_dir = str(Path(FFMPEG_PATH).parent)
    if _ffmpeg_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")

    if _FFPROBE_PATH:
        _ffprobe_dir = str(Path(_FFPROBE_PATH).parent)
        if _ffprobe_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = _ffprobe_dir + os.pathsep + os.environ.get("PATH", "")

    try:
        from pydub import AudioSegment
        import pydub.utils as _pydub_utils

        AudioSegment.converter = FFMPEG_PATH
        AudioSegment.ffmpeg = FFMPEG_PATH

        if _FFPROBE_PATH:
            # pydub 0.25.1 has NO ffprobe attribute it reads. `converter`
            # (and its back-compat alias `ffmpeg`) steers the ENCODER only;
            # every decode probes through utils.mediainfo_json ->
            # get_prober_name(), which returns the bare name "ffprobe" and
            # leaves the resolving to Windows. Setting AudioSegment.ffprobe,
            # as this module used to, creates an attribute nothing ever reads,
            # so the engine's ten from_file call sites - the re-voice's and
            # every narrated render's - were resolving their prober off PATH
            # no matter what was configured here.
            #
            # Replacing get_prober_name is the seam that works: pydub then
            # spawns the ABSOLUTE path of the binary that shipped with the
            # app. Two things follow. The decode no longer depends on PATH
            # ORDER, so a system ffmpeg of another version cannot win; and it
            # no longer runs an ffprobe.exe that merely happens to sit in the
            # working directory, which pydub's own `which` searches FIRST.
            #
            # The only behaviour this changes is pydub's file-OBJECT branch,
            # where mediainfo_json compares the prober's NAME to "ffprobe" to
            # pick `cache:pipe:0` over `-`. Every from_file call in this app
            # passes a path, not a file object.
            # ``or "ffprobe"`` so that nulling _FFPROBE_PATH later restores
            # pydub's own behaviour rather than handing Popen a None.
            _pydub_utils.get_prober_name = lambda: _FFPROBE_PATH or "ffprobe"
    except ImportError:
        pass

# Patch pydub's subprocess calls to suppress console windows on Windows
if os.name == "nt":
    import subprocess as _sp
    _orig_popen = _sp.Popen

    class _SilentPopen(_orig_popen):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("creationflags", 0x08000000)  # CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    _sp.Popen = _SilentPopen


def _binary_version(path: str) -> str:
    """The first line of ``<path> -version``, or why it could not be read."""
    import subprocess

    try:
        out = subprocess.run(
            [path, "-version"], capture_output=True, timeout=10,
            text=True, encoding="utf-8", errors="replace",
            creationflags=0x08000000 if os.name == "nt" else 0,  # CREATE_NO_WINDOW
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"version unreadable ({type(exc).__name__})"
    lines = (out.stdout or "").splitlines()
    return lines[0].strip() if lines else f"version unreadable (exit {out.returncode})"


def _mixed_pair_warning(shipped, resolved, version_of=_binary_version) -> str | None:
    """The startup warning for an ffmpeg/ffprobe pair that did not come from one place.

    ``shipped`` is ``_shipped_ffmpeg_paths()``; ``resolved`` is what the app
    will actually run, ``(FFMPEG_PATH, _FFPROBE_PATH)``. The state this exists
    for: a 0.9.0 install updated in place, whose ``bin/`` holds the shipped
    ffmpeg 7.1 and no ffprobe, on a machine with an ffprobe of its own - so the
    render runs the tested ffmpeg and pydub probes with whatever the machine
    has (8.0.1 on the dev box). It is ALLOWED: the decode is ffmpeg's, the
    prober only reports stream metadata, and refusing the foreign one would
    break a machine that works. What it must not be is silent.

    Returns None - no warning - when the pair is shipped together, when both
    come from the machine (a source checkout), and when either one is missing
    altogether: there is no pair to mix, and a missing binary fails loudly on
    its own.
    """
    shipped_ffmpeg, shipped_ffprobe = shipped
    ffmpeg, ffprobe = resolved
    if not (ffmpeg and ffprobe):
        return None
    ffmpeg_shipped = ffmpeg == shipped_ffmpeg
    ffprobe_shipped = ffprobe == shipped_ffprobe
    if ffmpeg_shipped == ffprobe_shipped:
        return None

    def origin(is_shipped: bool) -> str:
        return "shipped in app\\bin" if is_shipped else "found on this machine"

    return (
        "ffmpeg and ffprobe did not come from the same place, so this install is "
        "not running the pair it was built and tested with. "
        f"ffmpeg: {ffmpeg} ({origin(ffmpeg_shipped)}; {version_of(ffmpeg)}). "
        f"ffprobe: {ffprobe} ({origin(ffprobe_shipped)}; {version_of(ffprobe)}). "
        "This is what a 0.9.0 install updated in place looks like: Settings > "
        "Updates delivers the code but never app\\bin. Narrated renders and the "
        "re-voice still run, but on a prober the app was not tested with. Run the "
        "latest Media Studio Enterprise installer over this install to restore "
        "the matched pair."
    )


def _missing_prober_warning(shipped, resolved) -> str | None:
    """The startup warning for an install whose ``bin/`` has no prober, on a
    machine that has none either.

    The worst of the three states a 0.9.0 install updated in place can be in:
    the shipped ffmpeg is in ``app\\bin``, no ffprobe is there (an in-place
    update never refreshes ``bin``) and none is installed anywhere else, so the
    first re-voice or narrated render dies with ``FileNotFoundError
    [WinError 2]`` and nothing before that says why. :func:`_mixed_pair_warning`
    is silent here on purpose - there is no pair to mix - so this is its own
    warning.

    Returns None - no warning - unless the app runs the SHIPPED ffmpeg and has
    no prober at all. A source checkout with no shipped ffmpeg is a developer's
    machine, not a broken install, and stays silent whatever it lacks; so does
    an install whose prober was found somewhere (shipped, or the machine's).
    Never runs a binary: it has nothing to ask.
    """
    shipped_ffmpeg, _shipped_ffprobe = shipped
    ffmpeg, ffprobe = resolved
    if not shipped_ffmpeg or ffmpeg != shipped_ffmpeg or ffprobe:
        return None
    return (
        "No ffprobe was found. This install's app\\bin carries ffmpeg "
        f"({ffmpeg}) but no ffprobe.exe, and none is installed anywhere else on "
        "this machine. That is what a 0.9.0 install updated in place looks like: "
        "Settings > Updates delivers the code but never refreshes app\\bin, and "
        "0.9.0 shipped no ffprobe. Until the latest Media Studio Enterprise "
        "installer is run over this install, the re-voice and every narrated "
        "render will fail (FileNotFoundError [WinError 2])."
    )


# Worked out once, here, and LOGGED by utils.logger the moment logging exists:
# utils.logger imports this module, so this module cannot import it back. The
# two describe different states and never fire together (one needs a prober,
# the other its absence).
FFMPEG_PAIR_WARNING = _mixed_pair_warning(
    _shipped_ffmpeg_paths(), (FFMPEG_PATH, _FFPROBE_PATH)
)
FFPROBE_MISSING_WARNING = _missing_prober_warning(
    _shipped_ffmpeg_paths(), (FFMPEG_PATH, _FFPROBE_PATH)
)

# Default configuration
DEFAULT_CONFIG = {
    "default_voice_id": "en-US-AriaNeural",  # Edge TTS default voice
    "transition_pause": 0.0,
    "music_volume": 0.25,
    "output_folder": str(APP_DIR / "assets" / "finished"),
    "last_used_folder": "",
    "ollama_url": "http://localhost:11434",
    "ollama_model": "gemma4:12b",
    "ollama_system_prompt": "",
    "ollama_enabled": True,
    "translation_language": "",
    "mcp_servers": [
        {"name": "docs.pentaho.com", "url": "https://docs.pentaho.com/~gitbook/mcp"},
        {"name": "academy.pentaho.com", "url": "https://academy.pentaho.com/~gitbook/mcp"},
    ],
    "mcp_enabled": False,
    "slide_transition": "none",
    "transition_duration": 0.5,
    "watermark_text": "",
    "watermark_position": "bottom-right",
    "watermark_opacity": 0.5,
    # A font FILE (.ttf/.otf path) for the title cards and the text watermark;
    # "" = the host's Arial / Segoe UI / DejaVu ... (core/fonts.py). No UI.
    "title_font": "",
    "intro_text": "",
    "intro_subtitle": "",
    "intro_duration": 3.0,
    "outro_text": "",
    "outro_duration": 3.0,
    "voice_start_delay": 0.0,
    "ssml_enabled": False,
    "tts_language": "",
    "edge_tts_voice": "en-US-AriaNeural",
    "edge_tts_locale": "en-US",
    "tts_provider": "edge_tts",
    "kokoro_voice": "af_heart",
    "kokoro_lang": "en-us",
    "kokoro_model_dir": "",
    "whisper_model": "",  # "" = the recommended default (turbo on GPU, else medium)
}


class Config:
    """Application configuration manager."""

    # Environment variable overrides — keys map to config keys
    _ENV_OVERRIDES = {
        "MEDIA_STUDIO_PORT": ("port", int),
        "MEDIA_STUDIO_OLLAMA_URL": ("ollama_url", str),
        "MEDIA_STUDIO_OLLAMA_MODEL": ("ollama_model", str),
        "MEDIA_STUDIO_OLLAMA_ENABLED": ("ollama_enabled", lambda v: v.lower() in ("1", "true", "yes")),
        "MEDIA_STUDIO_EDGE_TTS_VOICE": ("edge_tts_voice", str),
        "MEDIA_STUDIO_MUSIC_VOLUME": ("music_volume", float),
        "MEDIA_STUDIO_TRANSITION_PAUSE": ("transition_pause", float),
    }

    def __init__(self):
        self._config = self._load_config()

    def _load_config(self) -> dict:
        """Load configuration from file, apply .env overrides."""
        # Load .env file if present (does not override existing env vars)
        env_file = APP_DIR / ".env"
        if env_file.exists():
            try:
                with open(env_file) as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            key, _, val = line.partition("=")
                            key, val = key.strip(), val.strip().strip('"').strip("'")
                            os.environ.setdefault(key, val)
            except IOError:
                pass

        # Load saved config or defaults
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    config = json.load(f)
                saved_output = config.get("output_folder", "")
                if "Videos" in saved_output or ".pptx_to_video" in saved_output:
                    config.pop("output_folder", None)
                config = {**DEFAULT_CONFIG, **config}
            except (json.JSONDecodeError, IOError):
                config = DEFAULT_CONFIG.copy()
        else:
            config = DEFAULT_CONFIG.copy()

        # Apply environment variable overrides
        for env_key, (cfg_key, converter) in self._ENV_OVERRIDES.items():
            val = os.environ.get(env_key)
            if val is not None:
                try:
                    config[cfg_key] = converter(val)
                except (ValueError, TypeError):
                    pass

        return config

    def save(self):
        """Save current configuration to file."""
        with open(CONFIG_FILE, "w") as f:
            json.dump(self._config, f, indent=2)

    @property
    def default_voice_id(self) -> str:
        return self._config.get("default_voice_id", DEFAULT_CONFIG["default_voice_id"])

    @default_voice_id.setter
    def default_voice_id(self, value: str):
        self._config["default_voice_id"] = value
        self.save()

    @property
    def transition_pause(self) -> float:
        return self._config.get("transition_pause", DEFAULT_CONFIG["transition_pause"])

    @transition_pause.setter
    def transition_pause(self, value: float):
        self._config["transition_pause"] = value
        self.save()

    @property
    def music_volume(self) -> float:
        return self._config.get("music_volume", DEFAULT_CONFIG["music_volume"])

    @music_volume.setter
    def music_volume(self, value: float):
        self._config["music_volume"] = value
        self.save()

    @property
    def output_folder(self) -> str:
        return self._config.get("output_folder", DEFAULT_CONFIG["output_folder"])

    @output_folder.setter
    def output_folder(self, value: str):
        self._config["output_folder"] = value
        self.save()

    @property
    def last_used_folder(self) -> str:
        return self._config.get("last_used_folder", "")

    @last_used_folder.setter
    def last_used_folder(self, value: str):
        self._config["last_used_folder"] = value
        self.save()

    def cache_voices(self, voices: list):
        """Cache voice list to avoid re-fetching on app restart.

        Args:
            voices: List of Voice objects with voice_id, name, category attributes.
        """
        import time
        self._config["cached_voices"] = [
            {"voice_id": v.voice_id, "name": v.name, "category": getattr(v, 'category', 'premade')}
            for v in voices
        ]
        self._config["voices_cached_at"] = time.time()
        self.save()

    def get_cached_voices(self, max_age_hours: float = 24) -> list:
        """Get cached voices if not expired.

        Args:
            max_age_hours: Maximum age of cache in hours (default 24).

        Returns:
            List of voice dicts or None if cache is expired/missing.
        """
        import time
        cached = self._config.get("cached_voices", [])
        cached_at = self._config.get("voices_cached_at", 0)
        if cached and time.time() - cached_at < max_age_hours * 3600:
            return cached
        return None

    # -- TTS provider settings ---------------------------------------------

    @property
    def tts_provider(self) -> str:
        """Active TTS provider id, validated to a known provider.

        Invalid/unknown stored values fall back to "edge_tts".
        """
        value = self._config.get("tts_provider", "edge_tts")
        return value if value in ("edge_tts", "kokoro") else "edge_tts"

    @tts_provider.setter
    def tts_provider(self, value: str):
        self._config["tts_provider"] = value if value in ("edge_tts", "kokoro") else "edge_tts"
        self.save()

    # -- Kokoro settings ---------------------------------------------------

    @property
    def kokoro_voice(self) -> str:
        """Default Kokoro voice id (e.g. "af_heart") used when none is supplied."""
        return self._config.get("kokoro_voice", "af_heart")

    @kokoro_voice.setter
    def kokoro_voice(self, value: str):
        self._config["kokoro_voice"] = value
        self.save()

    @property
    def kokoro_lang(self) -> str:
        """Default Kokoro language code (e.g. "en-us") for synthesis."""
        return self._config.get("kokoro_lang", "en-us")

    @kokoro_lang.setter
    def kokoro_lang(self, value: str):
        self._config["kokoro_lang"] = value
        self.save()

    @property
    def kokoro_model_dir(self) -> str:
        """Override directory for Kokoro model files ("" = built-in models dir)."""
        return self._config.get("kokoro_model_dir", "")

    @kokoro_model_dir.setter
    def kokoro_model_dir(self, value: str):
        self._config["kokoro_model_dir"] = value
        self.save()

    @property
    def whisper_model(self) -> str:
        """Last-chosen Whisper model for Import Video ("" = recommended default)."""
        return self._config.get("whisper_model", "")

    @whisper_model.setter
    def whisper_model(self, value: str):
        self._config["whisper_model"] = value or ""
        self.save()

    def cache_kokoro_voices(self, voices: list):
        """Cache Kokoro voice list."""
        import time
        self._config["cached_kokoro_voices"] = [
            {"voice_id": v.voice_id, "name": v.name, "category": v.category}
            for v in voices
        ]
        self._config["kokoro_voices_cached_at"] = time.time()
        self.save()

    def get_cached_kokoro_voices(self, max_age_hours: float = 168) -> list:
        """Get cached Kokoro voices (default cache: 7 days)."""
        import time
        cached = self._config.get("cached_kokoro_voices", [])
        cached_at = self._config.get("kokoro_voices_cached_at", 0)
        if cached and time.time() - cached_at < max_age_hours * 3600:
            return cached
        return None

    @property
    def edge_tts_voice(self) -> str:
        return self._config.get("edge_tts_voice", "en-US-AriaNeural")

    @edge_tts_voice.setter
    def edge_tts_voice(self, value: str):
        self._config["edge_tts_voice"] = value
        self.save()

    @property
    def edge_tts_locale(self) -> str:
        return self._config.get("edge_tts_locale", "en-US")

    @edge_tts_locale.setter
    def edge_tts_locale(self, value: str):
        self._config["edge_tts_locale"] = value
        self.save()

    def cache_edge_voices(self, voices: list):
        """Cache Edge TTS voice list."""
        import time
        self._config["cached_edge_voices"] = [
            {"voice_id": v.voice_id, "name": v.name, "category": v.category}
            for v in voices
        ]
        self._config["edge_voices_cached_at"] = time.time()
        self.save()

    def get_cached_edge_voices(self, max_age_hours: float = 168, allow_stale: bool = False) -> list:
        """Get cached Edge TTS voices (default cache: 7 days).

        With ``allow_stale`` the list is returned whatever its age, so the UI
        can show it immediately and refresh in the background; use
        :meth:`edge_voice_cache_is_fresh` to decide whether a refresh is due.
        Returns None when nothing usable is cached.
        """
        cached = self._config.get("cached_edge_voices", [])
        if not cached:
            return None
        if allow_stale or self.edge_voice_cache_is_fresh(max_age_hours):
            return cached
        return None

    def edge_voice_cache_is_fresh(self, max_age_hours: float = 168) -> bool:
        """True when a cached Edge voice list exists and is younger than the limit."""
        import time
        cached_at = self._config.get("edge_voices_cached_at", 0)
        return bool(self._config.get("cached_edge_voices")) and (
            time.time() - cached_at < max_age_hours * 3600
        )

    # -- Ollama settings ---------------------------------------------------

    @property
    def ollama_enabled(self) -> bool:
        """Whether Ollama AI features are enabled."""
        return self._config.get("ollama_enabled", False)

    @ollama_enabled.setter
    def ollama_enabled(self, value: bool):
        self._config["ollama_enabled"] = value
        self.save()

    @property
    def translation_language(self) -> str:
        """Target language for notes translation."""
        return self._config.get("translation_language", "")

    @translation_language.setter
    def translation_language(self, value: str):
        self._config["translation_language"] = value
        self.save()

    @property
    def ollama_url(self) -> str:
        """Get Ollama URL from config, .env, or default."""
        return (
            self._config.get("ollama_url")
            or os.getenv("OLLAMA_URL", "")
            or DEFAULT_CONFIG["ollama_url"]
        )

    @ollama_url.setter
    def ollama_url(self, value: str):
        self._config["ollama_url"] = value
        self.save()

    @property
    def ollama_model(self) -> str:
        """Get Ollama model from config or .env."""
        return self._config.get("ollama_model") or os.getenv("OLLAMA_MODEL", "")

    @ollama_model.setter
    def ollama_model(self, value: str):
        self._config["ollama_model"] = value
        self.save()

    @property
    def ollama_system_prompt(self) -> str:
        """Get Ollama system prompt from config or .env."""
        return (
            self._config.get("ollama_system_prompt")
            or os.getenv("OLLAMA_SYSTEM_PROMPT", "")
        )

    @ollama_system_prompt.setter
    def ollama_system_prompt(self, value: str):
        self._config["ollama_system_prompt"] = value
        self.save()

    # -- MCP settings --------------------------------------------------------

    @property
    def mcp_servers(self) -> list:
        """Get list of MCP server dicts: [{"name": "...", "url": "..."}].

        Merges config.json entries with any from .env (MCP_SERVERS comma-separated URLs).
        """
        servers = list(self._config.get("mcp_servers", []))
        # Also load from env: MCP_SERVERS=https://a.com/~gitbook/mcp,https://b.com/~gitbook/mcp
        env_val = os.getenv("MCP_SERVERS", "")
        if env_val:
            existing_urls = {s.get("url", "") for s in servers}
            for url in env_val.split(","):
                url = url.strip()
                if url and url not in existing_urls:
                    # Derive name from hostname
                    try:
                        from urllib.parse import urlparse
                        name = urlparse(url).hostname or url
                    except Exception:
                        name = url
                    servers.append({"name": name, "url": url})
        return servers

    @mcp_servers.setter
    def mcp_servers(self, value: list):
        self._config["mcp_servers"] = value
        self.save()

    def add_mcp_server(self, name: str, url: str):
        """Add an MCP server to the list (deduplicates by URL)."""
        servers = list(self._config.get("mcp_servers", []))
        if not any(s.get("url") == url for s in servers):
            servers.append({"name": name, "url": url})
            self._config["mcp_servers"] = servers
            self.save()

    def remove_mcp_server(self, url: str):
        """Remove an MCP server by URL."""
        servers = [s for s in self._config.get("mcp_servers", []) if s.get("url") != url]
        self._config["mcp_servers"] = servers
        self.save()

    @property
    def mcp_enabled(self) -> bool:
        """Whether MCP doc context is enabled for AI features."""
        return self._config.get("mcp_enabled", False)

    @mcp_enabled.setter
    def mcp_enabled(self, value: bool):
        self._config["mcp_enabled"] = value
        self.save()

    def clear_voice_cache(self):
        """Clear the cached voices."""
        self._config.pop("cached_voices", None)
        self._config.pop("voices_cached_at", None)
        self.save()

    # -- Preset management ---------------------------------------------------

    @property
    def presets(self) -> dict:
        """Get saved presets dict: {name: {voice, speed, stability, ...}}."""
        return self._config.get("presets", {})

    def save_preset(self, name: str, settings: dict):
        """Save a named preset."""
        presets = self._config.get("presets", {})
        presets[name] = settings
        self._config["presets"] = presets
        self.save()

    def delete_preset(self, name: str):
        """Delete a named preset."""
        presets = self._config.get("presets", {})
        presets.pop(name, None)
        self._config["presets"] = presets
        self.save()

    # -- Watermark settings --------------------------------------------------

    @property
    def watermark_text(self) -> str:
        return self._config.get("watermark_text", "")

    @watermark_text.setter
    def watermark_text(self, value: str):
        self._config["watermark_text"] = value
        self.save()

    @property
    def watermark_image(self) -> str:
        return self._config.get("watermark_image", "")

    @watermark_image.setter
    def watermark_image(self, value: str):
        self._config["watermark_image"] = value
        self.save()

    @property
    def watermark_position(self) -> str:
        return self._config.get("watermark_position", "bottom-right")

    @watermark_position.setter
    def watermark_position(self, value: str):
        self._config["watermark_position"] = value
        self.save()

    @property
    def watermark_opacity(self) -> float:
        return self._config.get("watermark_opacity", 0.5)

    @watermark_opacity.setter
    def watermark_opacity(self, value: float):
        self._config["watermark_opacity"] = value
        self.save()

    # -- Slide transitions ---------------------------------------------------

    @property
    def slide_transition(self) -> str:
        """Visual transition between slides: none, crossfade, fade-to-black."""
        return self._config.get("slide_transition", "none")

    @slide_transition.setter
    def slide_transition(self, value: str):
        self._config["slide_transition"] = value
        self.save()

    @property
    def transition_duration(self) -> float:
        """Duration of visual slide transition in seconds."""
        return self._config.get("transition_duration", 0.5)

    @transition_duration.setter
    def transition_duration(self, value: float):
        self._config["transition_duration"] = value
        self.save()

    # -- Intro / Outro -------------------------------------------------------

    @property
    def intro_text(self) -> str:
        """Title text shown on the intro card."""
        return self._config.get("intro_text", "")

    @intro_text.setter
    def intro_text(self, value: str):
        self._config["intro_text"] = value
        self.save()

    @property
    def intro_subtitle(self) -> str:
        """Subtitle text shown on the intro card."""
        return self._config.get("intro_subtitle", "")

    @intro_subtitle.setter
    def intro_subtitle(self, value: str):
        self._config["intro_subtitle"] = value
        self.save()

    @property
    def intro_duration(self) -> float:
        """Duration of the intro title card in seconds."""
        return self._config.get("intro_duration", 3.0)

    @intro_duration.setter
    def intro_duration(self, value: float):
        self._config["intro_duration"] = value
        self.save()

    @property
    def outro_text(self) -> str:
        """Text shown on the outro/closing card."""
        return self._config.get("outro_text", "")

    @outro_text.setter
    def outro_text(self, value: str):
        self._config["outro_text"] = value
        self.save()

    @property
    def outro_duration(self) -> float:
        """Duration of the outro closing card in seconds."""
        return self._config.get("outro_duration", 3.0)

    @outro_duration.setter
    def outro_duration(self, value: float):
        self._config["outro_duration"] = value
        self.save()

    # -- Voice start delay ----------------------------------------------------

    @property
    def voice_start_delay(self) -> float:
        """Seconds of video to play before narration audio begins."""
        return self._config.get("voice_start_delay", 1.0)

    @voice_start_delay.setter
    def voice_start_delay(self, value: float):
        self._config["voice_start_delay"] = value
        self.save()

    # -- SSML / markup -------------------------------------------------------

    @property
    def ssml_enabled(self) -> bool:
        """Whether to parse SSML-like tags in speaker notes."""
        return self._config.get("ssml_enabled", False)

    @ssml_enabled.setter
    def ssml_enabled(self, value: bool):
        self._config["ssml_enabled"] = value
        self.save()

    # -- TTS language --------------------------------------------------------

    @property
    def tts_language(self) -> str:
        """Language code for TTS (empty = auto-detect)."""
        return self._config.get("tts_language", "")

    @tts_language.setter
    def tts_language(self, value: str):
        self._config["tts_language"] = value
        self.save()

    # -- Template management -------------------------------------------------

    @property
    def templates(self) -> dict:
        """Get saved project templates: {name: {all settings}}."""
        return self._config.get("templates", {})

    def save_template(self, name: str, settings: dict):
        """Save a named project template."""
        templates = self._config.get("templates", {})
        templates[name] = settings
        self._config["templates"] = templates
        self.save()

    def delete_template(self, name: str):
        """Delete a named project template."""
        templates = self._config.get("templates", {})
        templates.pop(name, None)
        self._config["templates"] = templates
        self.save()


# Singleton config instance — imported by all modules that need settings.
# Created at import time so directories are guaranteed to exist.
config = Config()
