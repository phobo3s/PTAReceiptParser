# PTA Receipt Parser

**Plain Text Accounting Receipt Parser** — OCR-based tool that reads thermal receipt images and writes itemized entries into an [hledger](https://hledger.org/) journal and/or an Excel double-entry ledger.

> Optimized for Turkish receipts but designed to be extensible to any locale — see [Adapting for Other Languages](#adapting-for-other-languages).

---

## Pipeline Overview

```
Receipt Image (jpg/png)
       │
       ▼
  [preProcess.py]          ← optional: deskew, contrast, crop
       │
       ▼
  [batch.py]               ← main entry point
       │
       ├─► [ocr_engine.py] ← PaddleOCR / Tesseract / Windows OCR
       │         │
       │         ▼
       │    .ocr_cache/         ← raw OCR results (JSON, reused on re-runs)
       │
       ├─► [parser.py]     ← coordinate-based receipt parser
       │         │
       │         ├── corrections.toml   ← OCR error corrections (exact match)
       │         └── stores.toml       ← store profiles + universal rules
       │
       ├─► [rules.py]      ← item categorization engine
       │         │
       │         ├── rules.toml         ← hand-written categorization rules
       │         └── rules_learned.toml ← rules learned interactively via Claude
       │
       ├─► [update_journal.py]  ← hledger journal output
       │         └── *.hledger
       │
       └─► [update_excel.py]   ← Excel double-entry ledger output
```

---

## Files

### Core Pipeline

| File | Description |
|---|---|
| `batch.py` | **Main entry point.** Scans a folder of receipt images: OCR → parse → categorize → update journal. |
| `ocr_engine.py` | OCR engine adapter. Unified interface for PaddleOCR, Tesseract, and Windows OCR. Caches results under `.ocr_cache/`. |
| `parser.py` | Coordinate-based receipt parser. Extracts store name, date, item list, and total from OCR JSON output. |
| `rules.py` | Maps item names to hledger accounts using `rules.toml` and `rules_learned.toml`. |
| `update_journal.py` | Converts parse output to hledger journal format and appends to an existing `.hledger` file. |
| `update_excel.py` | Writes parse output line-by-line into a double-entry Excel ledger. |
| `preProcess.py` | Image pre-processing: deskew, perspective correction, contrast normalization, sharpening, crop. Improves OCR quality before scanning. |

### Configuration Files

| File | Description |
|---|---|
| `stores.toml` | Store profiles (BIM, Migros, Metro, ...) and universal rules (skip patterns, date formats, name cleanup). |
| `rules.toml` | Hand-written categorization rules. Assigns hledger accounts by item name, store, or amount range. |
| `rules_learned.toml` | Categories learned interactively through Claude. Auto-updated by `rules.py`. |
| `corrections.toml` | OCR correction dictionary. Wrong OCR text → correct text. Updated via `build_corrections.py`. |

### PPOCRLabel / Calibration

| File | Description |
|---|---|
| `import_labels.py` | Converts a PPOCRLabel `Label.txt` export into `.ocr_cache` format, so manually corrected OCR data is used as ground truth. |
| `build_corrections.py` | Diffs PPOCRLabel `Cache.cach` (raw auto-OCR) against `Label.txt` (user-corrected) and appends new correction pairs to `corrections.toml`. |
| `correct_labels.py` | Uses Claude Haiku Vision to automatically correct `Label.txt` transcriptions from crop images. |

### Support Tools

| File | Description |
|---|---|
| `snapshots.py` | Saves successful parse results. Regression test: after a code or regex change, verify old receipts still parse correctly. |
| `llm_parser.py` | LLM-based alternative parser using Claude API instead of regex. Useful for receipts that defeat the rule-based parser. |
| `generate_tesseract_cache.py` | Generates Tesseract cache files for all receipts (for engine comparison). |

### Tests

| File | Description |
|---|---|
| `test_parser.py` | Unit tests for `parser.py`. |
| `test_new_files.py` | Test-parses newly added receipt images and prints results. |
| `test_compare_engines.py` | Compares OCR engine outputs on the same receipt. |
| `test_preprocess_compare.py` | Measures the effect of pre-processing steps on OCR quality. |

---

## Quick Start

### 1. Install Dependencies

```bash
pip install paddleocr anthropic openpyxl pillow opencv-python numpy shapely
```

### 2. Process Receipts

```bash
# Process all receipts in a folder and update the journal
py batch.py Receipts/ ledger.hledger

# With Claude API key (for interactive categorization questions)
py batch.py Receipts/ ledger.hledger --api-key sk-ant-...

# Pre-process images first for better OCR accuracy
py preProcess.py Receipts/
py batch.py .processedReceipts/ ledger.hledger
```

### 3. Parse a Single Receipt

```bash
# Parse from cached OCR output
py parser.py .ocr_cache/receipt.json

# Output in hledger format
py parser.py .ocr_cache/receipt.json --hledger
```

### 4. OCR Correction Workflow

```
Open receipts in PPOCRLabel
→ Correct any wrong transcriptions
→ Save (Label.txt is updated)
→ py build_corrections.py     ← new corrections appended to corrections.toml
→ py import_labels.py         ← write corrected cache to .ocr_cache/
```

### 5. Regression Test

```bash
py snapshots.py --regression
```

---

## Decision Tree: Where to Fix an Error

```
Receipt produced wrong output
        │
        ├─ OCR misread a character  (e.g. TARIH → TARİH, UISA → VISA)
        │         └─► Correct in PPOCRLabel → py build_corrections.py
        │
        ├─ Characters correct but parser split rows/prices wrong
        │         └─► Edit stores.toml  (price_pattern, skip_patterns, y_tolerance, ...)
        │
        └─ Parsed correctly but wrong spending category
                  └─► Edit rules.toml  or let Claude learn it  (rules_learned.toml)
```

---

## Supported Stores

Defined in `stores.toml`:

| Store | Parse Mode |
|---|---|
| BIM | normal |
| Migros / Market | normal |
| Tankar (fuel station) | normal |
| METRO / ETRD GrosMarket | two-line (product name and price on separate lines) |
| FSREF CAN GIDA | normal |
| BUENAS / Restaurant | normal |
| CAFEGURUP / Restaurant | normal |

To add a new store, add a `[store.xxx]` block to `stores.toml`. Each profile defines identifiers (header regex), layout bounds, price/total patterns, and optional skip/cleanup rules.

---

## OCR Engines

`ocr_engine.py` supports three engines behind a unified interface:

| Engine | Install | Notes |
|---|---|---|
| **PaddleOCR** (default) | `pip install paddleocr` | Best `*` detection and character accuracy |
| **Tesseract** | `winget install UB-Mannheim.TesseractOCR` + `pip install pytesseract` | Requires language pack for your locale |
| **Windows OCR** | `pip install winocr` | Built-in on Windows 10/11, no extra install |

**Recommended model:** `PP-OCRv5_mobile_rec` — better `*` character recognition than the server model and fewer character mutations on Turkish text.

---

## Adapting for Other Languages

The parser is locale-agnostic at its core. Turkish-specific behavior is isolated to configuration files — swapping them out is enough to support another language:

| What to change | Where |
|---|---|
| Store identifiers and price/total regex | `stores.toml` — add `[store.xxx]` blocks for local chains |
| Skip patterns and date formats | `stores.toml` `[common]` section |
| Spending categories | `rules.toml` |
| OCR character corrections | `corrections.toml` (built automatically from PPOCRLabel ground truth) |
| OCR language | `ocr_engine.py` — change `lang=` in `load_paddle()` or Tesseract config |

The coordinate-based row grouping, price extraction, and two-line parse mode work independently of language. Any receipt where prices are right-aligned and items are left-aligned will parse correctly with the right store profile.
