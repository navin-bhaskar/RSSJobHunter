"""
File-hash based caching module for parsed resume outputs.
Prevents unnecessary LLM API calls when the target PDF resume content has not changed.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional


class ResumeCache:
    """Cache manager for resume parse results keyed by SHA-256 hash of PDF files."""

    def __init__(self, cache_file: str | Path = ".cache/resume_cache.json"):
        self.cache_file = Path(cache_file)
        self._ensure_cache_dir()

    def _ensure_cache_dir(self) -> None:
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def calculate_file_hash(file_path: str | Path) -> str:
        """Calculates SHA-256 hash of a file's binary content."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found for hash calculation: {path}")

        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _load_cache_data(self) -> Dict[str, Any]:
        if not self.cache_file.exists():
            return {}
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_cache_data(self, data: Dict[str, Any]) -> None:
        self._ensure_cache_dir()
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get(self, file_hash: str) -> Optional[Dict[str, Any]]:
        """Retrieves cached parsing result if hash matches."""
        cache_data = self._load_cache_data()
        entry = cache_data.get(file_hash)
        if entry:
            return entry.get("data")
        return None

    def set(self, file_hash: str, file_name: str, data: Dict[str, Any]) -> None:
        """Saves parsed result under the given file hash."""
        cache_data = self._load_cache_data()
        cache_data[file_hash] = {
            "file_name": file_name,
            "data": data
        }
        self._save_cache_data(cache_data)

    def clear(self) -> None:
        """Clears the cache file."""
        if self.cache_file.exists():
            self.cache_file.unlink()
