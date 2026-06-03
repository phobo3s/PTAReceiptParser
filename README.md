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
       │   [batch.py]      ← also supports EasyOCR and TrOCR (hybrid)
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
| `batch.py` | **Main entry point.** Scans a folder of receipt images: OCR → parse → categorize → update journal/Excel. Supports PaddleOCR, EasyOCR, and TrOCR engines. |
| `ocr_engine.py` | OCR engine adapter. Unified interface for PaddleOCR, Tesseract, and Windows OCR. Handles caching, guided receipt overlays, and confidence coloring. |
| `parser.py` | Coordinate-based receipt parser. Extracts store name, date, item list, and total from OCR JSON. Handles weight-based items, two-line formats, and orphan rows. |
| `rules.py` | Maps item names to hledger accounts using `rules.toml` and `rules_learned.toml`. Rule matching uses AND logic across item regex, store, and amount range. |
| `update_journal.py` | Converts parse output to hledger journal format. Matches existing transactions by date + amount, replaces postings with categorized items. |
| `update_excel.py` | Writes categorized items into a double-entry Excel ledger. Matches by date + amount, replaces to-account rows in-place. |
| `preProcess.py` | Image pre-processing pipeline (9 steps): upscale → deskew → perspective → bg normalization → gamma → CLAHE → denoise → sharpen → crop. Significantly improves OCR accuracy on dark or skewed photos. |
| `llm_parser.py` | LLM-based alternative parser using Claude API instead of regex. Useful for receipts that defeat the rule-based parser. Includes regex vs. LLM comparison mode. |

### Configuration Files

| File | Description |
|---|---|
| `config.toml` | Central configuration: paths for OCR cache, guided receipts, rules, snapshots, TrOCR adapter, etc. **Machine-specific, git-ignored.** Create from the template below. |
| `stores.toml` | Store profiles (BIM, Migros, Metro, ...) and universal rules (skip patterns, date formats, name cleanup regexes). |
| `rules.toml` | Hand-written categorization rules. Assigns hledger accounts by item name regex, store regex, or amount range. |
| `rules_learned.toml` | Categories learned interactively through Claude or manual input. Auto-appended by `rules.py`. |
| `corrections.toml` | OCR correction dictionary (wrong → right, exact match). Applied during `load_detections()` before parsing. Updated via `build_corrections.py`. |

#### config.toml Template

`config.toml` is machine-specific and **not committed to git**. Create it once per machine:

```toml
# PTA Receipt Parser — machine-specific paths
# Adjust all paths to match your local setup.

[paths]
receipts            = "C:/path/to/Receipts"           # source receipt images
ocr_cache           = "C:/path/to/.ocr_cache"         # PaddleOCR JSON results
ocr_cache_trocr     = "C:/path/to/.ocr_cache_trocr"   # TrOCR JSON results
ocr_cache_easyocr   = "C:/path/to/.ocr_cache_easyocr" # EasyOCR JSON results
rules               = "C:/path/to/rules.toml"
rules_learned       = "C:/path/to/rules_learned.toml"
ppocr_data          = "C:/path/to/PPOCRLabel_Data/Receipts"
guided_receipts     = "C:/path/to/.guidedReceipts"
processed_receipts  = "C:/path/to/.processedReceipts"
trocr_adapter       = "C:/path/to/.trocr_adapter"
parse_snapshots     = "C:/path/to/.parse_snapshots"
parse_llm_cache     = "C:/path/to/.parse_llm_cache"

# Default hledger account when no rule matches
default_account = "Gider:Bilinmeyen"
```

All keys are optional — omitting any key falls back to the default in `config.py`.

### TrOCR Fine-tuning

| File | Description |
|---|---|
| `train_trocr.py` | Fine-tunes `microsoft/trocr-base-printed` on Turkish receipt data using LoRA (PEFT). Reads `PPOCRLabel_Data/Receipts/rec_gt.txt`. Saves adapter to `.trocr_adapter/` and incrementally improves with each run. |

### PPOCRLabel / Calibration

| File | Description |
|---|---|
| `import_labels.py` | Converts a PPOCRLabel `Label.txt` export into `.ocr_cache` format so manually corrected OCR data is used as ground truth. |
| `build_corrections.py` | Diffs PPOCRLabel `Cache.cach` (raw auto-OCR) against `Label.txt` (user-corrected) and appends new correction pairs to `corrections.toml`. |
| `correct_labels.py` | Uses Claude Haiku Vision to automatically correct `Label.txt` transcriptions by re-reading each crop image. |

### Support Tools

| File | Description |
|---|---|
| `menu.py` | **Interactive TUI menu.** Launches all tools from a single numbered menu. No arguments needed — guided parameter input with validation. Run with `py menu.py`. |
| `snapshots.py` | Saves successful parse results to `.parse_snapshots/`. Regression test: after a code or regex change, verify old receipts still parse correctly with `--regression`. |
| `processed.py` | Tracks which receipts have already been written to hledger or Excel (stored in `.ocr_cache/processed.json`). Prevents duplicate updates. |
| `config.py` | Loads `config.toml` and exposes all paths as typed `Path` objects with sane defaults. |
| `generate_tesseract_cache.py` | Generates Tesseract cache files for all receipts that already have a PaddleOCR cache — useful for engine comparison. |

### Tests

| File | Description |
|---|---|
| `test_parser.py` | Unit tests for `parser.py`. |
| `test_new_files.py` | Test-parses newly added receipt images and prints results. |
| `test_compare_engines.py` | Compares OCR engine outputs side-by-side on the same receipt. |
| `test_preprocess_compare.py` | Measures the effect of pre-processing steps on OCR quality. |

---

## Quick Start

### 0. Install Dependencies

> **Python 3.12 required.** PaddleOCR does not yet support Python 3.13+.

```bash
py -3.12 -m pip install -r requirements.txt
```

For TrOCR fine-tuning (optional), install PyTorch matching your CUDA version:
```bash
# CUDA 11.8
py -3.12 -m pip install torch --index-url https://download.pytorch.org/whl/cu118
py -3.12 -m pip install transformers peft accelerate

# CPU only
py -3.12 -m pip install torch transformers peft accelerate
```

For optional OCR engines:
```bash
py -3.12 -m pip install easyocr          # EasyOCR
winget install UB-Mannheim.TesseractOCR  # Tesseract (+ pip install pytesseract)
py -3.12 -m pip install winocr           # Windows built-in OCR
```

### 1. Interactive Menu (Recommended)

```bash
py menu.py
```

Tüm araçları menü üzerinden çalıştırabilirsiniz — parametre ezberlemeye gerek yok.

### 2. Process Receipts

```bash
# Process all receipts in a folder and update the journal
py batch.py Receipts/ --hledger ledger.hledger

# With Claude API key (automatic categorization of unknown items)
py batch.py Receipts/ --hledger ledger.hledger --api-key sk-ant-...

# Update an Excel ledger instead (or both)
py batch.py Receipts/ --excel budget.xlsx --sheet Expenses
py batch.py Receipts/ --hledger ledger.hledger --excel budget.xlsx

# Pre-process images first for better OCR accuracy
py preProcess.py Receipts/
py batch.py .processedReceipts/ --hledger ledger.hledger
```

### 3. Parse a Single Receipt

```bash
# Parse from cached OCR output and print summary
py parser.py .ocr_cache/receipt.json

# Show full debug trace (row grouping, skip reasons, price matches)
py parser.py .ocr_cache/receipt.json --debug

# Batch parse all cached receipts, show only mismatched totals
py parser.py .ocr_cache/ --mismatch-only

# Parse and write to hledger or Excel in one step
py parser.py .ocr_cache/receipt.json --hledger ledger.hledger
py parser.py .ocr_cache/receipt.json --excel budget.xlsx --sheet Expenses
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

### 6. Fine-tune TrOCR (optional)

```bash
# After labeling receipts in PPOCRLabel and exporting rec_gt.txt:
py train_trocr.py                        # 3 epochs, full dataset
py train_trocr.py --epochs 1             # quick daily incremental run
py train_trocr.py --val-split 0.1        # with 10% validation split

# Then use the fine-tuned model:
py batch.py Receipts/ --engine trocr --hledger ledger.hledger
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
        ├─ Parsed correctly but wrong spending category
        │         └─► Edit rules.toml  or let Claude learn it  (rules_learned.toml)
        │
        └─ Rule-based parser fails entirely for an unusual receipt format
                  └─► py llm_parser.py --compare  (LLM fallback)
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

### `ocr_engine.py` engines (used via `run_ocr` / `load_engine`)

| Engine | Install | Notes |
|---|---|---|
| **PaddleOCR** (recommended) | `pip install paddleocr` | Best `*` detection and character accuracy. Uses `PP-OCRv5_mobile_det` + `PP-OCRv5_mobile_rec`. |
| **Tesseract** | `winget install UB-Mannheim.TesseractOCR` + `pip install pytesseract` | Requires `tur` language pack. Slower but good for comparison. |
| **Windows OCR** | `pip install winocr` | Built-in on Windows 10/11, no extra install. Confidence always 1.0. |

### `batch.py` additional engines (`--engine` flag)

| Engine | Install | Notes |
|---|---|---|
| **EasyOCR** | `pip install easyocr` | Native Turkish character support (İ, Ğ, Ş, Ö, Ü, Ç). |
| **TrOCR** (hybrid) | `pip install transformers torch peft` | PaddleOCR detection + TrOCR recognition. Fine-tunable with `train_trocr.py`. |

**Critical PaddleOCR settings** — always keep these `False`:

```python
use_doc_unwarping=False        # True dramatically degrades thermal receipt OCR
use_textline_orientation=False # True adds noise on straight thermal receipts
```

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
| `--engine paddleocr\|easyocr\|trocr` | OCR engine (default: `paddleocr`) |
| `--preprocess` | Run `preProcess.py` on images before OCR |

**Examples:**
```bash
py batch.py Receipts/ --hledger budget.hledger
py batch.py Receipts/ --excel budget.xlsx --sheet Expenses
py batch.py Receipts/ --hledger budget.hledger --excel budget.xlsx --api-key sk-ant-...
py batch.py Receipts/ --engine easyocr --hledger budget.hledger
py batch.py Receipts/ --engine trocr --hledger budget.hledger
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
| `--hledger <file>` | Update this hledger journal file after parsing |
| `--excel <file>` | Update this Excel ledger file after parsing |
| `--sheet <name>` | Excel sheet name (used with `--excel`) |
| `--debug` | Print detailed row-by-row parse trace: layout bounds, detection grouping, skip reasons, price matches |
| `--mismatch-only` | Suppress output for receipts where calculated total matches receipt total — only show failures |
| `--force` | Re-process receipts that have already been marked as done |

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
| `--engine paddle\|tesseract` | Target OCR engine — enables binarization step for Tesseract (default: `paddle`) |
| `--gamma <float>` | Gamma correction, e.g. `0.7` to brighten dark receipts (default: off) |
| `--sharpen` | Apply unsharp masking for sharper text edges |
| `--output <dir>` | Output directory (default: `.processedReceipts/`) |
| `--no-debug` | Skip saving per-step debug images |

**Pipeline steps:**
```
[0] Upscale          — enforce minimum width (800px)
[1] Deskew           — Hough line rotation correction
[2] Perspective      — 4-corner warpPerspective
[3] BG normalize     — large Gaussian divide (removes shadows/uneven lighting)
[4] Gamma            — midtone brightness (--gamma, optional)
[5] CLAHE            — adaptive local contrast
[6] Denoise          — bilateral filter
[7] Sharpen          — unsharp masking (--sharpen, optional)
[8] Binary           — adaptive threshold (Tesseract only)
[9] Crop             — trim empty borders
```

**Examples:**
```bash
py preProcess.py Receipts/
py preProcess.py Receipts/ --gamma 0.7 --sharpen
py preProcess.py Receipts/ --engine tesseract --output .processed_tess
```

---

### `train_trocr.py` — Fine-tune TrOCR on receipt data

```
py train_trocr.py [options]
```

| Argument | Default | Description |
|---|---|---|
| `--labels <path>` | `PPOCRLabel_Data/Receipts/rec_gt.txt` | Training data: crop path + label pairs |
| `--epochs <n>` | `3` | Number of training epochs |
| `--batch-size <n>` | `4` | Batch size |
| `--lr <float>` | `5e-4` | Learning rate |
| `--val-split <float>` | `0.0` | Fraction for validation (e.g. `0.1` = 10%) |
| `--adapter-dir <path>` | `.trocr_adapter/` | Where to save/load the LoRA adapter |
| `--no-continue` | — | Ignore existing adapter, start from scratch |
| `--max-label-len <n>` | `128` | Maximum token length per label |

The adapter is saved after every epoch. If `.trocr_adapter/adapter_config.json` exists, training resumes from the existing adapter (incremental fine-tuning). Use `batch.py --engine trocr` to use the fine-tuned model.

```bash
py train_trocr.py --epochs 1 --batch-size 4   # fast daily run
py train_trocr.py --val-split 0.1              # with validation
py train_trocr.py --no-continue                # start fresh
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

### `llm_parser.py` — LLM-based parser

```
py llm_parser.py [files...] [options]
```

| Argument | Description |
|---|---|
| `--api-key` | Anthropic API key (or set `ANTHROPIC_API_KEY` env var) |
| `--compare` | Compare regex parser vs. LLM parser on all cached receipts |
| `--force` | Bypass LLM response cache, re-call the API |
| `--dry-run` | Print the text sent to the LLM without calling the API |

```bash
py llm_parser.py --dry-run                         # preview text sent to Claude
py llm_parser.py --compare --api-key sk-ant-...    # regex vs LLM side-by-side
py llm_parser.py .ocr_cache/receipt.json --api-key sk-ant-...
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
```

---

### `import_labels.py` — Import PPOCRLabel ground truth into cache

```
py import_labels.py [Label.txt] [output_dir]
py import_labels.py --all-caches
```

| Argument | Default | Description |
|---|---|---|
| `Label.txt` | `PPOCRLabel_Data/Receipts/Label.txt` | Corrected label file |
| `output_dir` | `.ocr_cache/` | Where to write `{stem}.json` cache files |
| `--all-caches` | — | Write to both `.ocr_cache/` and `.ocr_cache_trocr/` |

Existing cache files are **not overwritten** — delete the relevant `.json` first if you want to re-import.

---

## PPOCRLabel Workflow

PPOCRLabel is a GUI annotation tool that lets you visually inspect and correct OCR output on receipt images. Use it to build ground truth data that feeds `corrections.toml` and `.ocr_cache/`.

### Setup

1. Install: `pip install PPOCRLabel`
2. Launch: `PPOCRLabel --lang en`
3. Open folder: **File → Open Dir** → select `PPOCRLabel_Data/Receipts/`

### Typical workflow for a new batch of receipts

```
1. Copy new receipt images into PPOCRLabel_Data/Receipts/

2. Open folder in PPOCRLabel
   File → Open Dir → PPOCRLabel_Data/Receipts/

3. Auto-detect all images
   View → Auto Recognition
   (Raw results saved to Cache.cach)

4. Correct transcription errors
   Click each detection box → edit text in right panel → Ctrl+S
   Focus on items and totals — header/footer errors don't affect parsing.

5. Build correction dictionary
   py build_corrections.py
   → Diffs Cache.cach (auto) vs Label.txt (corrected)
   → Appends new pairs to corrections.toml

6. Import corrected cache (optional)
   del .ocr_cache\Belge4_1.json  ← delete old cache first
   py import_labels.py
   → Writes Label.txt data into .ocr_cache/ with confidence=1.0

7. Run regression test to verify nothing broke
   py snapshots.py --regression

8. Export for TrOCR fine-tuning (optional)
   File → Export Recognition Results  → rec_gt.txt
   py train_trocr.py --epochs 1
```

### Key files in PPOCRLabel_Data/Receipts/

| File | Written by | Content |
|---|---|---|
| `Cache.cach` | PPOCRLabel auto-OCR | Raw (uncorrected) detections |
| `Label.txt` | User corrections | Corrected transcriptions — one line per image |
| `rec_gt.txt` | PPOCRLabel export | Crop + text pairs for TrOCR fine-tuning |
| `crop_img/` | PPOCRLabel | Individual word/phrase crop images |

### What to correct vs what to leave

| Situation | Action |
|---|---|
| OCR misread a character (`TARIH`, `UISA`, `SOHA`) | Correct in PPOCRLabel → `build_corrections.py` |
| Price not detected (missing `*`) | Leave — fix `price_pattern` in `stores.toml` |
| Wrong row grouping | Leave — adjust `y_tolerance` in `stores.toml` |
| Footer/header noise | Leave — handled by `skip_patterns` in `stores.toml` |
| Product name has garbage suffix | Leave — handled by `name_cleanup` in `stores.toml` |
| Recurring OCR error on many receipts | Fix via PPOCRLabel → TrOCR fine-tuning |
