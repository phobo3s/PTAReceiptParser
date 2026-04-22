# PTAReceiptParser

**Plain Text Accounting Receipt Parser** — OCR-based tool that reads Turkish market receipts and writes itemized entries into an hledger journal and/or an Excel double-entry ledger.

```
Photo → PaddleOCR → Parse → Categorize → Update hledger and/or Excel
```

---

## Features

- **Local-first** — no cloud OCR, no subscriptions; models are downloaded on first run
- **Multi-store** — BİM, Migros, Tankar, METRO, FSREF/Can Gıda, Buenas, CafeGrubu
- **Dual output** — the same receipt can update both an hledger journal and an Excel ledger
- **Rule engine** — regex-based; learned rules are applied before `rules.toml`
- **Auto-learning** — answers for unknown items are saved to `rules_learned.toml` and never asked again
- **Claude API fallback** — unknown items can be sent to Claude for automatic categorization (optional)
- **LLM parser** — alternative to the regex parser; uses Claude to parse the full receipt (optional)
- **Weight-aware** — correctly parses `0.74kg × 19.75` style produce lines
- **OCR cache** — results stored in `.ocr_cache/`; the same receipt is never re-scanned
- **Snapshot testing** — parse results are saved; a warning is shown if output changes on re-run

---

## How It Works

### hledger — Before / After

**Before:**
```
2026-03-26 BİM
    gider:market                              333.07 TRY
    Borçlar:kart                             -333.07 TRY
```

**After:**
```
2026-03-26 BİM
    gider:market:gida:atistirmalik            26.00 TRY  ; KEKÇİK PİNGUİ
    gider:market:gida:kuru-gida               29.00 TRY  ; KABARTMA TOZU
    gider:market:gida:atistirmalik            21.50 TRY  ; ŞEKERLİ VANİLİN
    gider:kitap                               65.00 TRY  ; HİKAYE KİTAPLARI
    gider:market:poset                         1.00 TRY  ; ALIŞVERİŞ POŞETİ
    gider:market:gida:sebze                   14.62 TRY  ; PATATES (0.74kg × 19.75)
    gider:market:gida:sebze                   47.76 TRY  ; BİBER KAPYA (0.24kg × 199.00)
    gider:market:gida:meyve                   23.14 TRY  ; ELMA GOLDEN (0.26kg × 89.00)
    gider:market:gida:meyve                   40.05 TRY  ; ELMA STARKİNG (0.45kg × 89.00)
    Borçlar:kart                            -333.07 TRY
```

### Excel — Double-Entry Ledger

Each transaction in Excel consists of two parts:

| Row | A (Date) | G (Tag/Note) | H (Account) | I (Amount) |
|-----|----------|--------------|-------------|------------|
| 1st (from) | 26.03.2026 | — | `Borçlar:kart` | `-333,07` |
| 2nd (to) | — | ELMA GOLDEN | `gider:market:gida:meyve` | `23,14` |
| 3rd (to) | — | PATATES | `gider:market:gida:sebze` | `14,62` |
| … | — | … | … | … |

Matching is done by date + amount (±0.02 TRY tolerance). Existing to-account rows are replaced with itemized lines; the from-account row is left untouched.

---

## Installation

```bash
pip install paddleocr pillow numpy anthropic openpyxl shapely
```

> PaddleOCR downloads model files (~300 MB) on first run.

Requirements:
- Python 3.12+
- Windows 11 / Linux

---

## File Structure

```
PTAReceiptParser/
├── batch.py            # Main entry point — processes a folder of receipt photos
├── parser.py           # OCR JSON → Receipt object (store profiles live here)
├── llm_parser.py       # Alternative parser — uses Claude API instead of regex
├── ocr_engine.py       # OCR adapter — PaddleOCR / Tesseract / Windows OCR
├── rules.py            # Rule engine + auto-learning
├── update_journal.py   # hledger journal matching and in-place update
├── update_excel.py     # Excel double-entry ledger matching and update
├── snapshots.py        # Regression testing — save and compare parse results
├── rules.toml          # Category rules (hand-edited)
└── rules_learned.toml  # Auto-generated from Claude/manual answers
```

---

## Usage

### Basic — OCR + categorize (no file updates)
```bash
python batch.py receipts/
```

### Update hledger
```bash
python batch.py receipts/ --hledger budget.hledger
```

### Update Excel
```bash
python batch.py receipts/ --excel budget.xlsx
python batch.py receipts/ --excel budget.xlsx --sheet Expenses
```

### Update both hledger and Excel
```bash
python batch.py receipts/ --hledger budget.hledger --excel budget.xlsx
```

### With Claude API — auto-categorize unknown items
```bash
export ANTHROPIC_API_KEY=sk-ant-...
python batch.py receipts/ --hledger budget.hledger
# or pass directly:
python batch.py receipts/ --hledger budget.hledger --api-key sk-ant-...
```

### Debug a single receipt
```bash
python parser.py .ocr_cache/bim_receipt.json --debug
```

All flags:

| Flag | Description |
|------|-------------|
| `--hledger <file>` | hledger journal file to update |
| `--excel <file>` | Excel file (`.xlsx` / `.xlsm`) to update |
| `--sheet <name>` | Excel sheet name (default: first sheet) |
| `--api-key <key>` | Anthropic API key for Claude categorization |

---

## LLM Parser (Alternative)

`llm_parser.py` uses Claude to parse the entire receipt instead of the regex-based parser. Useful for stores not yet in `STORE_PROFILES`.

```bash
# Dry-run — shows what text would be sent to Claude (no API call)
python llm_parser.py --dry-run

# Parse specific receipts
python llm_parser.py .ocr_cache/receipt.json --api-key sk-ant-...

# Compare regex parser vs LLM side by side
python llm_parser.py --compare --api-key sk-ant-...
```

Results are cached in `.parse_llm_cache/` so the same receipt is not re-parsed.

---

## OCR Engines

By default, `batch.py` uses PaddleOCR (PP-OCRv5 mobile). `ocr_engine.py` provides a unified adapter for three engines with a common JSON output format:

| Engine | Platform | Install |
|--------|----------|---------|
| PaddleOCR | Linux / Windows | `pip install paddleocr` |
| Tesseract | Linux / Windows | `apt install tesseract-ocr tesseract-ocr-tur` + `pip install pytesseract` |
| Windows OCR | Windows 10/11 only | `pip install winocr` (no extra install, uses built-in Windows API) |

```python
from ocr_engine import load_engine, run_ocr
from pathlib import Path

engine = load_engine("tesseract")  # or "paddle", "windows"
result = run_ocr(engine, "tesseract", Path("receipt.jpg"))
```

Each engine writes its cache as `{stem}_{engine}.json` so results from different engines don't overwrite each other.

---

## Regression Testing

Parse results are saved as snapshots in `.parse_snapshots/snapshots.json`. Run after any parser change to catch regressions:

```bash
python snapshots.py --regression
```

Sample output:
```
[OK] Market tespit edildi: BİM
  [OK]   WhatsApp Image 2026-04-07 at 08.45.16.json
...
Sonuc: 13 OK, 0 regresyon, 0 atlandi
```

---

## Category Rules

Rules are evaluated top-to-bottom; first match wins. All criteria are AND.

```toml
# rules.toml

[[rule]]
item    = "ELMA|ARMUT|MUZ"
account = "gider:market:gida:meyve"

[[rule]]
store      = "OPET|SHELL|BP"
amount_min = 500.0
account    = "gider:ulasim:yakit"

[[rule]]
store      = "OPET|SHELL|BP"
amount_max = 499.99
account    = "gider:market:diger"
```

Available criteria: `item`, `store`, `amount_min`, `amount_max`.

Unknown items are asked interactively (or sent to Claude if an API key is configured). Answers are saved to `rules_learned.toml` and loaded before `rules.toml` so learned rules always take priority.

---

## Supported Stores

| Store | Type | Notes |
|-------|------|-------|
| BİM | Grocery | Full support incl. weighted items |
| Migros / Hakan Karaca / Can Market | Grocery | Migros-format receipts |
| Tankar | Fuel / Car wash | |
| METRO / ETRD GrosMarket | Wholesale grocery | e-Invoice format |
| FSREF / Can Gıda | Grocery | |
| Buenas | Restaurant | |
| CafeGrubu / Gastronomi | Restaurant | |

---

## Adding a New Store

Add a profile to `STORE_PROFILES` in `parser.py`. The easiest way to calibrate is to look at an OCR result in `.ocr_cache/` with `python parser.py .ocr_cache/receipt.json --debug`.

```python
"mystore": {
    "name": "MyStore",
    "identifiers": [r"MYSTORE A\.S", r"MY STORE"],  # regex matched against header text
    "layout": {
        "y_tolerance": 18,    # pixel tolerance for grouping detections into rows
        "header_y_max": 500,  # products start below this Y coordinate
        "footer_y_min": 9999, # totals/bank info starts above this Y (9999 = no footer cutoff)
    },
    "price_pattern": r"^\*(-?[\d\.]+,\d{2})$",  # captures the amount group
    "skip_patterns": COMMON_SKIP_PATTERNS + [
        r"^MYSTORE\s+EXTRA",  # store-specific lines to ignore
    ],
    "total_pattern": r"^TOPLAM|^GENEL TOPLAM",
    "date_pattern":  COMMON_DATE_PATTERNS,
    "name_cleanup":  COMMON_NAME_CLEANUPS,
},
```

---

## License

MIT
