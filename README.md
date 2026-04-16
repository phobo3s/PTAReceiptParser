# PTAReceiptParser

**Plain Text Accounting Receipt Parser** — OCR-based tool that reads Turkish market receipts and writes itemized entries into an hledger journal and/or an Excel double-entry ledger.

```
Photo → PaddleOCR → Parse → Categorize → Update hledger and/or Excel
```

---

## Features

- **Local-first** — no cloud OCR, no subscriptions; models are downloaded on first run
- **Dual output** — the same receipt can update both an hledger journal and an Excel ledger independently
- **Rule engine** — regex-based, learned rules are applied before `rules.toml`
- **Auto-learning** — answers for unknown items are saved to `rules_learned.toml` and never asked again
- **Claude API fallback** — unknown items can be sent to Claude for automatic categorization (optional)
- **Weight-aware** — correctly parses `0.74kg × 19.75` style produce lines
- **OCR cache** — results are stored in `.ocr_cache/`; the same receipt is never re-scanned
- **Snapshot** — parse results are saved; a warning is shown if the output changes on a re-run

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

Matching is done by date + amount (±0.02 TRY tolerance). Existing to-account rows are deleted and replaced with the itemized lines; the from-account row is left untouched.

---

## Installation

```bash
pip install paddleocr pillow numpy anthropic openpyxl
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
├── rules.py            # Rule engine + auto-learning
├── update_journal.py   # hledger journal matching and in-place update
├── update_excel.py     # Excel double-entry ledger matching and update
├── snapshots.py        # OCR snapshot save/compare
├── rules.toml          # Category rules (hand-edited)
└── rules_learned.toml  # Auto-generated from Claude/manual answers
```

---

## Usage

### OCR + categorize only (no updates)
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

### Update both
```bash
python batch.py receipts/ --hledger budget.hledger --excel budget.xlsx
```

### With Claude API (auto-categorizes unknown items)
```bash
python batch.py receipts/ --hledger budget.hledger --api-key sk-ant-...
# or via environment variable
export ANTHROPIC_API_KEY=sk-ant-...
python batch.py receipts/ --hledger budget.hledger
```

### Test a single receipt
```bash
python parser.py receipts/bim_20260326.jpg --debug
```

All flags:

| Flag | Description |
|------|-------------|
| `--hledger <file>` | hledger journal file |
| `--excel <file>` | Excel file (`.xlsx` / `.xlsm`) |
| `--sheet <name>` | Excel sheet name (default: first sheet) |
| `--api-key <key>` | Anthropic API key |

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

## Adding a New Store

Add a profile to `STORE_PROFILES` in `parser.py`:

```python
"mystore": {
    "name": "MyStore",
    "identifiers": [r"MYSTORE A\.S"],
    "layout": {
        "price_x_min": 450,   # x threshold separating the name and price columns
        "y_tolerance": 18,    # pixel tolerance for same-row grouping
        "header_y_max": 640,  # y below which products start
        "footer_y_min": 1150, # y above which totals/bank info starts
    },
    "price_pattern": r"^\*?(\d+[\.,]\d{2})$",
    "skip_patterns": [r"^KDV", r"^TOPLAM KDV"],
    "total_pattern": r"^TOPLAM",
    "date_pattern":  r"(\d{2}\.\d{2}\.\d{4})\s*\d{2}:\d{2}",
    "name_cleanup":  [(r"\s+%\d+\.?\s*$", "")],
},
```

The easiest way to calibrate a new profile is to run OCR on a sample receipt and inspect the JSON in `.ocr_cache/`.

---

## Supported Stores

| Store | Type | Status |
|-------|------|--------|
| BİM | Grocery | ✅ |
| Migros | Grocery | ✅ |
| TANKAR | Fuel / Car wash | ✅ |

PRs with new store profiles are welcome.

---

## License

MIT
