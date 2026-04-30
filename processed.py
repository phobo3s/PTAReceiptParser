"""
İşlenmiş fiş takip sistemi
===========================
.ocr_cache/processed.json dosyasına yazar/okur.

Yapı:
{
  "excel": {
    "bim_20260326.json": {
      "updated_at": "2026-04-30T14:23:00",
      "store": "BIM", "date": "2026-03-26", "total": 245.80,
      "items": 7, "sheet": "İşlemler", "row": 42
    }
  },
  "hledger": {
    "bim_20260326.json": {
      "updated_at": "2026-04-30T14:23:00",
      "store": "BIM", "date": "2026-03-26", "total": 245.80,
      "tx_line": 198
    }
  }
}
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import PROCESSED_FILE


def _load() -> dict:
    if not PROCESSED_FILE.exists():
        return {}
    try:
        return json.loads(PROCESSED_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    PROCESSED_FILE.parent.mkdir(exist_ok=True)
    PROCESSED_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def is_processed(ocr_name: str, system: str) -> Optional[dict]:
    """Daha önce işlendiyse bilgi dict'ini döner, değilse None."""
    return _load().get(system, {}).get(ocr_name)


def mark_processed(ocr_name: str, system: str, info: dict) -> None:
    """Fişi işlendi olarak işaretle."""
    data = _load()
    data.setdefault(system, {})[ocr_name] = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        **info,
    }
    _save(data)


def unmark_processed(ocr_name: str, system: str) -> None:
    """İşlendi işaretini kaldır (--force sonrası)."""
    data = _load()
    data.get(system, {}).pop(ocr_name, None)
    _save(data)
