# PTAReceiptParser

**Plain Text Accounting Receipt Parser** — Turkish market receipts → hledger journal entries.

Photographs of receipts are parsed using OCR, categorized using a rule engine, and matched against existing hledger transactions — replacing single-line entries with itemized ones.

```
Photo → PaddleOCR → parse → categorize → match journal → update in-place
```

---

## Features

- **Local-first** — no cloud OCR, no subscriptions required
- **Deterministic** — parsing with regex-based rule engine
- **Multi-store** — pluggable store profiles (BİM, TANKAR, easily extensible)
- **Weight-aware** — handles `0.74kg × 19.75` style produce lines
- **Claude API fallback** — unknown items sent to Claude Haiku for categorization (optional, planned)
- **Auto-learning** — categorization decisions saved to `rules_learned.toml`, never asked twice
- **hledger native** — updates journal in-place, other transactions untouched

---

## How It Works

### Before
```
2026-03-26 BİM
    gider:market                              333.07 TRY
    Borçlar:kart                         -333.07 TRY
```

### After
```
2026-03-26 BİM
    gider:market:gida:atistirmalik            26.00 TRY  ; KEKÇİK PİNGUİ
    gider:market:gida:kuru-gida               29.00 TRY  ; KABARTMA TOZU
    gider:market:gida:atistirmalik            21.50 TRY  ; ŞEKERLİ VANİLİN
    gider:kitap                               65.00 TRY  ; HİKAYE KİTAPLARI
    gider:kitap                               65.00 TRY  ; HİKAYE KİTAPLARI
    gider:market:poset                         1.00 TRY  ; ALIŞVERİŞ POŞETİ
    gider:market:gida:sebze                   14.62 TRY  ; PATATES (0.74kg × 19.75)
    gider:market:gida:sebze                   47.76 TRY  ; BİBER KAPYA (0.24kg × 199.00)
    gider:market:gida:meyve                   23.14 TRY  ; ELMA GOLDEN (0.26kg × 89.00)
    gider:market:gida:meyve                   40.05 TRY  ; ELMA STARKİNG (0.45kg × 89.00)
    Borçlar:kart                        -333.07 TRY
```

---

## Requirements

```bash
pip install paddleocr pillow numpy
```

> PaddleOCR will download model files (~300MB) on first run.

Tested on:
- Python 3.12
- Windows 11
- PaddleOCR 3.x with `PP-OCRv5_mobile` models

---

## File Structure

```
PTAReceiptParser/
├── batch.py            # Main entry point — processes a folder of receipt photos
├── parser.py           # OCR JSON → Receipt object (store profiles live here)
├── rules.py            # Rule engine + auto-learning
├── update_journal.py   # hledger journal matching and in-place update
├── rules.toml          # Category rules (hand-edited)
└── rules_learned.toml  # Auto-generated from Claude/manual answers
```

---

## Usage

### Basic (manual categorization for unknown items)
```bash
python batch.py receipts/ budget.hledger
```

### Test (After OCR testing of output)
```bash
python parser.py Receipt_File_Path --debug
```

### With Claude API (auto-categorizes unknown items)
```bash
python batch.py receipts/ budget.hledger --api-key sk-ant-...
# or
export ANTHROPIC_API_KEY=sk-ant-...
python batch.py receipts/ budget.hledger
```

Place receipt photos (`.jpg` / `.png`) in the `receipts/` folder. Processed OCR results are cached in `.ocr_cache/` — receipts are never re-scanned.

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

Unknown items are asked interactively (or sent to Claude API if configured). Answers are saved to `rules_learned.toml` automatically — loaded before `rules.toml` so learned rules take priority.

---

## Adding a New Store

Add a profile to `STORE_PROFILES` in `parser.py`:

```python
"mystore": {
    "name": "MyStore",
    "identifiers": [r"MYSTORE A\.S"],
    "layout": {
        "price_x_min": 450,   # x threshold separating name vs price columns
        "y_tolerance": 18,    # px tolerance for same-row grouping
        "header_y_max": 640,  # y below which products start
        "footer_y_min": 1150, # y above which totals/bank info starts
    },
    "price_pattern": r"^\*?(\d+[\.,]\d{2})$",
    "skip_patterns": [r"^KDV", r"^TOPLAM KDV", ...],
    "total_pattern": r"^TOPLAM",
    "date_pattern":  r"(\d{2}\.\d{2}\.\d{4})\s*\d{2}:\d{2}",
    "name_cleanup":  [(r"\s+%\d+\.?\s*$", "")],
},
```

The easiest way to calibrate a new profile is to run OCR on a sample receipt and inspect the JSON in `.ocr_cache/`.

---

## Notes on OCR Setup

PaddleOCR is used directly (no Docker required). On Windows, disable oneDNN to avoid crashes:

```python
ocr = PaddleOCR(
    use_textline_orientation=False,
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    lang="en",
    device="cpu",
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="PP-OCRv5_mobile_rec",
    enable_mkldnn=False,
)
```

`lang="en"` works fine for Turkish receipts (Latin alphabet).

---

## Supported Stores

| Store  | Type            | Status          |
|--------|-----------------|-----------------|
| BİM    | Grocery         | ✅              |
| TANKAR | Fuel / Car wash | ✅              |
| Migros | Grocery         | 🚧 profile stub |

PRs with new store profiles are welcome.

---

## License

MIT
