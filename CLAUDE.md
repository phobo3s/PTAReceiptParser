# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**PTA Receipt Parser** — A Turkish receipt parsing system that extracts items and prices from supermarket receipts using PaddleOCR, categorizes them via rule engine or Claude API, and updates hledger journal files.

The system is designed to integrate personal expense tracking with automatic item categorization and journal management.

## Architecture

### Core Components

**parser.py** — Receipt extraction engine
- `load_detections()`: Converts PaddleOCR JSON output to `Detection` objects (text with bounding box coordinates)
- `detect_store()`: Identifies market chain from header text using store profiles (BIM, Migros, Tankar)
- `group_into_rows()`: Groups OCR detections into horizontal rows using Y-coordinate tolerance
- `extract_date()`: Extracts transaction date from header region
- `parse_receipt()`: Main orchestration function that:
  - Detects store profile
  - Filters product region (between header/footer Y bounds)
  - Groups detections into rows, merges weight-based items (e.g., "0.74kg × 19.75")
  - Extracts items and prices using X-coordinate thresholds per market
  - Applies skip patterns (e.g., barcodes, KDV lines) and name cleanup regexes
  - Returns `Receipt` object with store, date, items, total, raw detections
- **Store profiles** in `STORE_PROFILES` dict define layout rules (price_x_min, y_tolerance, header/footer bounds), regex patterns, and item cleanup rules per market

**batch.py** — Batch processing pipeline
- `get_ocr_engine()`: Loads PaddleOCR once (CPU mode, mobile models)
- `ocr_with_cache()`: Runs OCR on images, caches JSON results in `.ocr_cache/` to avoid re-processing
- `categorize_items()`: Fallback chain:
  1. Rule engine (rules.py)
  2. Cache of previous answers
  3. Claude API (haiku model) if api-key provided
  4. Manual stdin entry
- `ask_claude()`: Queries Claude API for item category, returns only account name (secure, brief)
- Main flow: image → PaddleOCR → parse → categorize → update journal

**rules.py** — Rule engine for item categorization
- `Rule` dataclass: account (required) + optional filters (item regex, store, amount_min/max)
- `matches()`: AND logic—all specified criteria must match
- `load_rules()`: Reads TOML files, returns `Rule` list
- `find_account()`: Returns first matching rule's account or None
- `append_learned_rule()`: Logs new categories to `rules_learned.toml` for future runs

**update_journal.py** — hledger journal integration
- `parse_journal()`: Reads hledger file, groups lines by transaction (date + indented posting lines)
- `find_matching_transaction()`: Matches receipt to existing journal entry by date + amount (0.02 TL tolerance)
- `build_new_transaction()`: Constructs new transaction with categorized items
- `update_journal()`: Inserts or replaces transactions in journal file

### Configuration

**rules.toml** — Hand-written categorization rules
- `[[rule]]` sections with AND conditions
- Fields: `account` (required), `item` (product regex), `store` (market regex), `amount_min`, `amount_max`, `comment`
- Example: Fuel purchases >500 TL → `Gider:ulasim:yakit`; items matching "PATATES" → `Gider:Market:Sebze`

**rules_learned.toml** — Auto-generated from Claude categorizations (append-only)

**.ocr_cache/** — Stores processed OCR JSON to avoid re-running expensive detection

## Common Development Tasks

### Running the Receipt Parser

**Single receipt from OCR JSON:**
```bash
python parser.py Receipts/bim_20260326.json
```

**With hledger output:**
```bash
python parser.py Receipts/bim_20260326.json --hledger
```

**Batch processing (images to journal):**
```bash
python batch.py Receipts/ journal.hledger --api-key sk-ant-...
```
- Skips images with cached OCR results
- Categorizes via rules → Claude API → manual input
- Updates journal in-place or creates new transactions

### Testing & Debugging

**Check OCR cache status:**
```bash
ls -la .ocr_cache/
```

**Clear OCR cache (force re-run):**
```bash
rm -rf .ocr_cache/
```

**Trace rule matching for an item:**
- Add debug print in `find_account()` or `categorize_items()` to see which rule matched
- Use `rules_learned.toml` to check what Claude previously categorized

**Test journal parsing:**
```python
from update_journal import parse_journal
txs = parse_journal(Path("journal.hledger"))
```

**Validate receipt totals:**
- Run parser on receipt, check "⚠️ Фар" warning if calculated ≠ total
- Indicates possible KDV, discount, or OCR error in items

## Key Design Decisions

### Coordinate-Based Layout Detection
Stores don't have consistent structure (especially mobile vs. server OCR). Solution: detect store first → apply store-specific layout rules (price column X position, header/footer Y bounds). Avoids fragile column alignment.

### Weight-Based Item Merging
BIM receipts split weight pricing across 2-3 lines:
- "0.74 kg X 19.75" | "PATATES" | "*14.62"

Parser detects weight pattern, merges into single item with formatted name: `PATATES (0.74kg × 19.75)`. See `merge_weight_rows()` and `parse_weight_line()`.

### OCR Caching
PaddleOCR is slow (~seconds per image). `.ocr_cache/` stores JSON results by image stem. Batch processing checks cache before OCR, enabling rapid re-runs when tweaking rules or journal updates.

### Cascading Categorization
No single categorizer is perfect:
1. **Rules** — Fast, deterministic, for known patterns (e.g., all "PATATES" → Sebze)
2. **Claude API** — Fallback for ambiguous items; uses cheap Haiku model, only returns account name
3. **Manual** — Last resort if API unavailable or user needs override

New categories learned by Claude are saved to `rules_learned.toml` for future batches.

### Journal Matching & Updating
`find_matching_transaction()` matches receipt to journal by date + amount (not by item names, which may vary). If match exists, items are categorized and inserted into that transaction (replacing old postings if provided). If no match, new transaction is appended.

## Common Gotchas

1. **Store detection fails** — Header region must contain store identifier. Check `STORE_PROFILES[store_key]["identifiers"]` regexes. Mobile OCR often misses text; may need to add more patterns or widen header_y_max.

2. **Wrong price column** — BIM has `price_x_min: 450`, Migros may differ. If items parse with wrong prices or no prices, adjust layout rules in `STORE_PROFILES`. Use raw detection's x_min values to diagnose.

3. **Weight items get skipped** — `merge_weight_rows()` only runs if `parse_weight_line()` matches "Xkg × Y" pattern. BIM uses "X" (multiplication symbol) but OCR may detect as "×" or "x". Check skip patterns if weight tag shows up in output.

4. **Claude API cost** — Haiku model is cheap (~$0.80/M input tokens) but adds latency. For frequently-seen items, rely on rules instead; use Claude only for unknowns.

5. **Journal parse failure** — hledger format is sensitive to indentation. Postings must be indented with space or tab, no blank lines within transaction. If `parse_journal()` misses a transaction, check source formatting.

## Dependencies

```
paddleocr     — OCR engine (requires CPU or GPU)
anthropic     — Claude API (optional, for categorization fallback)
PIL           — Image loading
numpy         — Array operations for OCR
tomllib       — TOML config parsing (Python 3.11+)
```

Install with: `pip install paddleocr anthropic pillow numpy`

## File Structure

```
parser.py              — Core receipt parser (store detection, OCR → Receipt)
batch.py              — Batch pipeline (images → parser → categorize → journal)
rules.py              — Rule engine (TOML rules, matching, learning)
update_journal.py     — hledger file integration
rules.toml            — Hand-written categorization rules
rules_learned.toml    — Auto-learned categories (if it exists)
.ocr_cache/           — Cached OCR JSON (git-ignored)
Receipts/             — Input images (jpg/png)
journal.hledger       — Target hledger file (updated in-place)
```
