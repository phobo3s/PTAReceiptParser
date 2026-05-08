---
component_id: 3
component_name: Pipeline Controller
---

# Pipeline Controller

## Component Description

Orchestrates the end-to-end execution flow. It manages the lifecycle of a processing job, including loading business rules (TOML), triggering the parser, and applying categorization logic to map receipt items to specific accounts.

---

## Key References:

### c:\PTAReceiptParser\batch.py (lines 413-527)
```

def process_receipt(
    image_path: Path,
    ocr_engine,
    rules: list,
    api_key: Optional[str],
    journal_path: Optional[Path] = None,
    excel_path: Optional[Path] = None,
    excel_sheet: Optional[str] = None,
    engine_name: str = "paddleocr",
) -> bool:
    """Bir fişi işle. En az bir kanal güncellendiyse True döndür."""
    print(f"\n{'═' * 60}")
    print(f"  📄 {image_path.name}")
    print(f"{'═' * 60}")

    # OCR
    try:
        ocr_json = ocr_with_cache(ocr_engine, image_path, engine_name)
    except Exception as e:
        print(f"  ❌ OCR hatası: {e}")
        return False

    # Parse
    try:
        receipt = parse_receipt(ocr_json)
        print_summary(receipt)
    except ValueError as e:
        print(f"  ❌ Parse hatası: {e}")
        return False

    # Snapshot kontrol
    ocr_path = OCR_CACHE_DIR / (image_path.stem + ".json")
    snap_diffs = check_snapshot(ocr_path, receipt)
    if snap_diffs:
        print(f"  [!] SNAPSHOT FARKI TESPIT EDILDI:")
        for diff in snap_diffs:
            print(f"      - {diff}")
        print(f"  Snapshot guncellensin mi? [e/H] ", end="", flush=True)
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "h"
        if answer == "e":
            save_snapshot(ocr_path, receipt)
            print(f"  [snapshot] Guncellendi: {ocr_path.name}")
        else:
            print(f"  [snapshot] Korundu -- fis atlandi.")
            return False
    else:
        saved = save_snapshot(ocr_path, receipt)
        if saved:
            print(f"  [snapshot] Kaydedildi: {ocr_path.name}")

    # Kategorile — her iki kanal için de gerekli
    categorized = categorize_items(receipt, rules, api_key)

    print("\n  Kategoriler:")
    for item, account in categorized:
        print(f"    {item.name:<40} → {account}")

    any_updated = False

    # ── hledger güncelleme ─────────────────────────────────────────────────────
    if journal_path:
        transactions = parse_journal(journal_path)
        tx = find_matching_transaction(receipt, transactions)

        if tx is None:
            total_str = f"{receipt.total:.2f}" if receipt.total is not None else "bilinmiyor"
            print(f"\n  ❌ hledger: eşleşme bulunamadı!")
            print(f"     Aranan: {receipt.date}  {total_str} TL  ({receipt.store})")
        else:
            print(f"  ✓ hledger: satır {tx.start_line + 1} → {tx.raw_lines[0].strip()}")
            new_lines = build_new_transaction(tx, categorized, receipt)
            preview(new_lines)
            print("  hledger güncellensin mi? [e/H] ", end="", flush=True)
            try:
                answer = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "h"
            if answer == "e":
                update_journal(journal_path, tx, new_lines)
                print(f"  ✓ hledger güncellendi")
                any_updated = True
            else:
                print("  hledger atlandı.")

    # ── Excel güncelleme ───────────────────────────────────────────────────────
    if excel_path:
        from update_excel import find_excel_match, update_excel, preview_excel
        from_row, account = find_excel_match(excel_path, receipt, excel_sheet)

        if from_row is None:
            total_str = f"{receipt.total:.2f}" if receipt.total is not None else "bilinmiyor"
            print(f"\n  ❌ Excel: eşleşme bulunamadı!")
            print(f"     Aranan: {receipt.date}  {total_str} TL")
        else:
            print(f"  ✓ Excel: satır {from_row} → {account}")
            preview_excel(categorized, receipt)
            print("  Excel güncellensin mi? [e/H] ", end="", flush=True)
            try:
                answer = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "h"
            if answer == "e":
                ok = update_excel(excel_path, receipt, categorized, excel_sheet)
                if ok:
                    print(f"  ✓ Excel güncellendi: {excel_path.name}")
                    any_updated = True
                else:
                    print(f"  ❌ Excel güncellenemedi")
            else:
                print("  Excel atlandı.")

```

### c:\PTAReceiptParser\rules.py (lines 43-48)
```
def load_rules(path: Path) -> list[Rule]:
    if not path.exists():
        return []
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return [_parse_rule(r) for r in data.get("rule", [])]
```

### c:\PTAReceiptParser\batch.py (lines 365-408)
```

def categorize_items(
    receipt: Receipt,
    rules: list,
    api_key: Optional[str],
) -> list[tuple[ReceiptItem, str]]:
    results = []
    unknown_cache = {}  # aynı ürünü tekrar sorma

    for item in receipt.items:
        # 1. Rule engine
        account = find_account(item.name, receipt.store, item.amount, rules)
        if account:
            results.append((item, account))
            continue

        # 2. Cache
        if item.name in unknown_cache:
            results.append((item, unknown_cache[item.name]))
            continue

        # 3. Claude API fallback
        if api_key:
            print(f"  🤖 Claude'a soruluyor: {item.name} ({item.amount:.2f} TL)")
            account = ask_claude(item.name, receipt.store, item.amount, api_key)
            if account:
                print(f"     → {account}")
                unknown_cache[item.name] = account
                append_learned_rule(item.name, account, LEARNED_RULES_FILE)
                results.append((item, account))
                continue

        # 4. Manuel giriş (API yoksa veya başarısızsa)
        print(f"\n  ❓ Tanınmayan ürün: \033[1m{item.name}\033[0m  ({item.amount:.2f} TL)")
        print(f"     Hangi hesaba? (boş → '{DEFAULT_ACCOUNT}')")
        try:
            answer = input("     > ").strip()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        chosen = answer if answer else DEFAULT_ACCOUNT
        unknown_cache[item.name] = chosen
        append_learned_rule(item.name, chosen, LEARNED_RULES_FILE)
        results.append((item, chosen))

```


## Source Files:

- `batch.py`
- `parser.py`
- `rules.py`
- `update_excel.py`
- `update_journal.py`

