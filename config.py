"""
Merkezi config yükleyici — config.toml okur, Path nesneleri döndürür.
Dosya yoksa veya bir key eksikse varsayılan değerler kullanılır.
"""

import tomllib
from pathlib import Path

_CONFIG_FILE = Path(__file__).parent / "config.toml"

_DEFAULTS = {
    "paths": {
        "ocr_cache":       ".ocr_cache",
        "rules":           "rules.toml",
        "rules_learned":   "rules_learned.toml",
        "default_account": "Gider:Bilinmeyen",
    }
}


def _load() -> dict:
    if not _CONFIG_FILE.exists():
        return _DEFAULTS
    try:
        with open(_CONFIG_FILE, "rb") as f:
            data = tomllib.load(f)
        # Eksik key'leri default ile doldur
        for section, values in _DEFAULTS.items():
            data.setdefault(section, {})
            for k, v in values.items():
                data[section].setdefault(k, v)
        return data
    except Exception:
        return _DEFAULTS


_cfg = _load()

OCR_CACHE_DIR    = Path(_cfg["paths"]["ocr_cache"])
RULES_FILE       = Path(_cfg["paths"]["rules"])
RULES_LEARNED    = Path(_cfg["paths"]["rules_learned"])
DEFAULT_ACCOUNT  = _cfg["paths"]["default_account"]
PROCESSED_FILE   = OCR_CACHE_DIR / "processed.json"
