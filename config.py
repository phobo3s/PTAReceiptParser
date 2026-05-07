"""
Merkezi config yükleyici — config.toml okur, Path nesneleri döndürür.
Dosya yoksa veya bir key eksikse varsayılan değerler kullanılır.
"""

import tomllib
from pathlib import Path

_CONFIG_FILE = Path(__file__).parent / "config.toml"

_DEFAULTS = {
    "paths": {
        "ocr_cache":          ".ocr_cache",
        "ocr_cache_trocr":    ".ocr_cache_trocr",
        "ocr_cache_easyocr":  ".ocr_cache_easyocr",
        "guided_receipts":    ".guidedReceipts",
        "processed_receipts": ".processedReceipts",
        "trocr_adapter":      ".trocr_adapter",
        "parse_snapshots":    ".parse_snapshots",
        "parse_llm_cache":    ".parse_llm_cache",
        "rules":              "rules.toml",
        "rules_learned":      "rules_learned.toml",
        "default_account":    "Gider:Bilinmeyen",
        "ppocr_data":         "PPOCRLabel_Data/Receipts",
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

OCR_CACHE_DIR         = Path(_cfg["paths"]["ocr_cache"])
OCR_CACHE_DIR_TROCR   = Path(_cfg["paths"]["ocr_cache_trocr"])
OCR_CACHE_DIR_EASY    = Path(_cfg["paths"]["ocr_cache_easyocr"])
GUIDED_RECEIPTS_DIR   = Path(_cfg["paths"]["guided_receipts"])
PROCESSED_RECEIPTS_DIR= Path(_cfg["paths"]["processed_receipts"])
TROCR_ADAPTER_DIR     = Path(_cfg["paths"]["trocr_adapter"])
PARSE_SNAPSHOTS_DIR   = Path(_cfg["paths"]["parse_snapshots"])
PARSE_LLM_CACHE_DIR   = Path(_cfg["paths"]["parse_llm_cache"])
RULES_FILE            = Path(_cfg["paths"]["rules"])
RULES_LEARNED         = Path(_cfg["paths"]["rules_learned"])
DEFAULT_ACCOUNT       = _cfg["paths"]["default_account"]
PPOCR_DATA_DIR        = Path(_cfg["paths"]["ppocr_data"])
PROCESSED_FILE        = OCR_CACHE_DIR / "processed.json"
