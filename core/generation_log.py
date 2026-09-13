"""Tracks video generation history."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from utils.config import CONFIG_DIR

LOG_FILE = CONFIG_DIR / "generation_log.json"
MAX_ENTRIES = 100


@dataclass
class GenerationRecord:
    timestamp: str  # ISO format
    files: list[str]  # file names processed
    total_slides: int
    voice_id: str
    voice_name: str  # display name
    resolution: str  # e.g. "1920x1080"
    duration_seconds: float  # total generation time
    characters_used: int
    output_path: str
    settings: dict  # snapshot of relevant settings (speed, stability, etc.)
    status: str  # "completed", "cancelled", "error"
    error_message: str = ""


class GenerationLog:
    """Manages generation history stored in CONFIG_DIR/generation_log.json."""

    def __init__(self, log_dir: Path = CONFIG_DIR):
        self._log_file = log_dir / "generation_log.json"

    def _read(self) -> list[dict]:
        if not self._log_file.exists():
            return []
        try:
            data = json.loads(self._log_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError):
            pass
        return []

    def _write(self, records: list[dict]):
        self._log_file.parent.mkdir(parents=True, exist_ok=True)
        self._log_file.write_text(
            json.dumps(records, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def add_record(self, record: GenerationRecord):
        """Append a generation record. Keeps last 100 entries."""
        records = self._read()
        records.append(asdict(record))
        records = records[-MAX_ENTRIES:]
        self._write(records)

    def get_records(self, limit: int = 50) -> list[GenerationRecord]:
        """Get recent records, newest first."""
        records = self._read()
        records.reverse()
        return [GenerationRecord(**r) for r in records[:limit]]

    def clear(self):
        """Clear all history."""
        if self._log_file.exists():
            self._log_file.unlink()
