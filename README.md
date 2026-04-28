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
| **PaddleOCR** (default-recommended) | `pip install paddleocr` | Best `*` detection and character accuracy |
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

---

## CLI Reference

### `batch.py` — Main batch processor

```
py batch.py <receipt_folder> [options]
```

| Argument | Description |
|---|---|
| `receipt_folder` | Folder containing `.jpg` / `.jpeg` / `.png` receipt images |
| `--hledger <file.hledger>` | Update this hledger journal file |
| `--excel <file.xlsx>` | Update this Excel ledger file |
| `--sheet <SheetName>` | Excel sheet name (default: first sheet) |
| `--api-key sk-ant-...` | Anthropic API key for automatic item categorization. Also reads `ANTHROPIC_API_KEY` env var. |
| `--engine paddleocr\|easyocr` | OCR engine (default: `paddleocr`) |
| `--preprocess` | Run `preProcess.py` on images before OCR |

**Examples:**
```bash
py batch.py Receipts/ --hledger budget.hledger
py batch.py Receipts/ --excel budget.xlsx --sheet Expenses
py batch.py Receipts/ --hledger budget.hledger --excel budget.xlsx --api-key sk-ant-...
py batch.py Receipts/ --engine easyocr --hledger budget.hledger
```

**Categorization priority:** `rules_learned.toml` → `rules.toml` → Claude API (if `--api-key` given) → manual prompt.

---

### `parser.py` — Parse a single receipt or folder

```
py parser.py <ocr_cache.json|folder> [options]
```

| Argument | Description |
|---|---|
| `<path>` | A single `.ocr_cache/*.json` file, or a folder to parse all JSON files in it |
| `--debug` | Print detailed row-by-row parse trace: layout bounds, detection grouping, skip reasons, price matches |
| `--mismatch-only` | Suppress output for receipts where calculated total matches receipt total — only show failures |

**Examples:**
```bash
# Parse one file with full debug trace
py parser.py .ocr_cache/"Belge 4_1.json" --debug

# Batch parse entire cache, show only broken receipts
py parser.py .ocr_cache/ --mismatch-only

# Quick sanity check on all cached receipts
py parser.py .ocr_cache/
```

---

### `preProcess.py` — Image pre-processing

```
py preProcess.py <folder|image> [options]
```

| Argument | Description |
|---|---|
| `<target>` | Folder of images or a single image file |
| `--engine paddle\|tesseract` | Target OCR engine — affects binarization step (default: `paddle`) |
| `--gamma <float>` | Gamma correction, e.g. `0.7` to brighten dark receipts (default: off) |
| `--sharpen` | Apply unsharp masking for sharper text edges |
| `--output <dir>` | Output directory (default: `.processedReceipts/`) |
| `--no-debug` | Skip saving per-step debug images |

**Examples:**
```bash
py preProcess.py Receipts/
py preProcess.py Receipts/ --gamma 0.7 --sharpen
py preProcess.py Receipts/ --engine tesseract --output .processed_tess
```

---

### `snapshots.py` — Regression tests

```
py snapshots.py --regression [options]
```

| Argument | Description |
|---|---|
| `--regression` | Re-parse all snapshots and compare against saved results |
| `--cache-dir <dir>` | OCR cache directory to read from (default: `.ocr_cache/`) |

Snapshots are saved automatically during `batch.py` and `parser.py` runs whenever the calculated total matches the receipt total. After changing regex patterns or parser logic, run regression to catch unintended breakage.

```bash
py snapshots.py --regression
py snapshots.py --regression --cache-dir .ocr_cache
```

---

### `build_corrections.py` — Build OCR correction dictionary

```
py build_corrections.py [Cache.cach] [Label.txt] [corrections.toml]
```

| Argument | Default | Description |
|---|---|---|
| `Cache.cach` | `PPOCRLabel_Data/Receipts/Cache.cach` | PPOCRLabel auto-OCR output (the "wrong" side) |
| `Label.txt` | `PPOCRLabel_Data/Receipts/Label.txt` | User-corrected transcriptions (the "right" side) |
| `corrections.toml` | `corrections.toml` | Output file — new pairs are appended, existing ones kept |

```bash
py build_corrections.py
py build_corrections.py PPOCRLabel_Data/Receipts/Cache.cach PPOCRLabel_Data/Receipts/Label.txt corrections.toml
```

---

### `import_labels.py` — Import PPOCRLabel ground truth into cache

```
py import_labels.py [Label.txt] [output_dir]
```

| Argument | Default | Description |
|---|---|---|
| `Label.txt` | `PPOCRLabel_Data/Receipts/Label.txt` | Corrected label file |
| `output_dir` | `.ocr_cache/` | Where to write `{stem}.json` cache files |

Existing cache files are **not overwritten** — delete the relevant `.json` first if you want to re-import.

---

## PPOCRLabel Workflow

PPOCRLabel is a GUI annotation tool that lets you visually inspect and correct OCR output on receipt images. Use it to build ground truth data that feeds `corrections.toml` and `.ocr_cache/`.

### Setup

1. Install: `pip install PPOCRLabel`
2. Launch: `PPOCRLabel --lang en` (or `--lang ch` for the full UI)
3. Open folder: **File → Open Dir** → select `PPOCRLabel_Data/Receipts/`

### Typical workflow for a new batch of receipts

```
1. Copy new receipt images into PPOCRLabel_Data/Receipts/

2. Open folder in PPOCRLabel
   File → Open Dir → PPOCRLabel_Data/Receipts/

3. Auto-detect all images
   View → Auto Recognition

   PPOCRLabel runs its built-in OCR on every image and fills
   transcriptions automatically. Raw results saved to Cache.cach.

4. Correct transcription errors
   Click each detection box on screen.
   Edit the transcription text in the right panel.
   Save: Ctrl+S  (updates Label.txt, not Cache.cach)

   Focus on items and totals — header/footer errors don't affect parsing.
   Rule of thumb:
     - OCR character error (TARIH, UISA) → fix here → build_corrections.py
     - Layout/price issue                 → fix in stores.toml instead

5. Export corrected data
   File → Export Recognition Results  (writes rec_gt.txt, optional)

6. Build correction dictionary
   py build_corrections.py
   → Diffs Cache.cach (auto) vs Label.txt (corrected)
   → Appends new pairs to corrections.toml

7. Import corrected cache (optional — use corrected data as OCR cache)
   Delete existing cache files for affected images:
     del .ocr_cache\Belge4_1.json
   Then:
     py import_labels.py
   → Writes Label.txt data into .ocr_cache/ with confidence=1.0

8. Run regression test to verify nothing broke
   py snapshots.py --regression
```

### Key files in PPOCRLabel_Data/Receipts/

| File | Written by | Content |
|---|---|---|
| `Cache.cach` | PPOCRLabel auto-OCR | Raw (uncorrected) detections — same format as Label.txt |
| `Label.txt` | User corrections | Corrected transcriptions — one line per image |
| `rec_gt.txt` | PPOCRLabel export | Crop + text pairs for model training (optional) |
| `crop_img/` | PPOCRLabel | Individual word/phrase crops |

### What to correct vs what to leave

| Situation | Action |
|---|---|
| OCR misread a character (`TARIH`, `UISA`, `SOHA`) | Correct in PPOCRLabel → `build_corrections.py` |
| Price not detected (missing `*`) | Leave — fix `price_pattern` in `stores.toml` |
| Wrong row grouping | Leave — adjust `y_tolerance` in `stores.toml` |
| Footer/header noise | Leave — handled by `skip_patterns` in `stores.toml` |
| Product name has garbage suffix | Leave — handled by `name_cleanup` in `stores.toml` |
