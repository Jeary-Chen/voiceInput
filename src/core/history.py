"""History manager — stores transcription records.

Stores each transcription as a JSON entry + optional WAV audio file.
Supports: save, load, delete, search, get page.
"""
import json
import time
import wave
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

from config import Config
from core.log import logger
from core.recorder import VoiceRecorder

_TAG = "[History]"


class _SafeEncoder(json.JSONEncoder):
    """Convert numpy scalars to native Python types for JSON serialization."""

    def default(self, o):
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        return super().default(o)


@dataclass
class HistoryEntry:
    id: str
    text: str
    duration: float
    mode: str
    timestamp: float
    has_audio: bool = False
    raw_text: str = ""
    processing_info: dict | None = None
    failed: bool = False
    error_msg: str = ""

    @property
    def datetime_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))

    @property
    def short_text(self) -> str:
        if self.failed:
            return "[转录失败]"
        return self.text[:50] + ("..." if len(self.text) > 50 else "")


_ENTRY_FIELDS = {f.name for f in HistoryEntry.__dataclass_fields__.values()}


def _load_entry(data: dict) -> HistoryEntry:
    filtered = {k: v for k, v in data.items() if k in _ENTRY_FIELDS}
    return HistoryEntry(**filtered)


class HistoryManager:
    MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

    def __init__(self, config: Config):
        self._dir = Config.history_dir()
        self._enforce_size_limit()

    def save_entry(self, text: str, duration: float, mode: str,
                   audio_data: Optional[bytes] = None,
                   raw_text: str = "",
                   processing_info: Optional[dict] = None,
                   failed: bool = False,
                   error_msg: str = "") -> HistoryEntry:
        base_id = self._next_id()
        entry_id = f"{base_id}_failed" if failed else base_id
        entry = HistoryEntry(
            id=entry_id,
            text=text,
            duration=round(duration, 1),
            mode=mode,
            timestamp=time.time(),
            has_audio=audio_data is not None,
            raw_text=raw_text,
            processing_info=processing_info,
            failed=failed,
            error_msg=error_msg,
        )

        data = asdict(entry)
        if not data.get("raw_text"):
            del data["raw_text"]
        if data.get("processing_info") is None:
            del data["processing_info"]
        if not data.get("failed"):
            del data["failed"]
        if not data.get("error_msg"):
            del data["error_msg"]

        meta_path = self._dir / f"{entry_id}.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, cls=_SafeEncoder)

        if audio_data:
            wav_path = self._dir / f"{entry_id}.wav"
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(VoiceRecorder.TARGET_RATE)
                wf.writeframes(audio_data)

        logger.info(f"{_TAG} Saved entry {entry_id} "
                    f"({duration:.1f}s, mode={mode}"
                    f"{', failed' if failed else ''}"
                    f"{', +wav' if audio_data else ''})")
        self._enforce_size_limit()
        return entry

    def get_entries(self, limit: int = 50, offset: int = 0) -> list[HistoryEntry]:
        files = sorted(self._dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        entries = []
        for path in files[offset:offset + limit]:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                entries.append(_load_entry(data))
            except Exception:
                continue
        return entries

    def get_entry(self, entry_id: str) -> Optional[HistoryEntry]:
        path = self._dir / f"{entry_id}.json"
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return _load_entry(json.load(f))
        except Exception:
            return None

    def delete_entry(self, entry_id: str):
        for suffix in (".json", ".wav"):
            path = self._dir / f"{entry_id}{suffix}"
            if path.exists():
                path.unlink()

    def delete_all(self):
        for path in self._dir.glob("*"):
            path.unlink()

    def search(self, query: str, limit: int = 50) -> list[HistoryEntry]:
        q = query.lower()
        results = []
        for entry in self.get_entries(limit=500):
            if q in entry.text.lower():
                results.append(entry)
                if len(results) >= limit:
                    break
        return results

    def total_count(self) -> int:
        return len(list(self._dir.glob("*.json")))

    def folder_size_kb(self) -> int:
        total = sum(f.stat().st_size for f in self._dir.rglob("*") if f.is_file())
        return total // 1024

    def _next_id(self) -> str:
        base = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        if not self._id_taken(base):
            return base
        for seq in range(2, 100):
            candidate = f"{base}_{seq}"
            if not self._id_taken(candidate):
                return candidate
        return f"{base}_{int(time.time() * 1000) % 10000}"

    def _id_taken(self, base: str) -> bool:
        """Return True if base or base_failed entry files already exist."""
        return ((self._dir / f"{base}.json").exists()
                or (self._dir / f"{base}_failed.json").exists())

    def _enforce_size_limit(self):
        """Delete oldest entries until total size is under MAX_SIZE_BYTES."""
        files = sorted(self._dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        total = sum(f.stat().st_size for f in self._dir.rglob("*") if f.is_file())
        deleted = 0
        while total > self.MAX_SIZE_BYTES and files:
            oldest = files.pop(0)
            entry_id = oldest.stem
            entry_size = oldest.stat().st_size
            oldest.unlink()
            wav = self._dir / f"{entry_id}.wav"
            if wav.exists():
                entry_size += wav.stat().st_size
                wav.unlink()
            total -= entry_size
            deleted += 1
        if deleted:
            logger.info(f"{_TAG} Size limit: deleted {deleted} old entries, "
                        f"now {total // 1024} KB")
