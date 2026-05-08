---
component_id: 4
component_name: Accounting Exporter
---

# Accounting Exporter

## Component Description

The "Load" phase of the ETL. It translates structured data into final financial artifacts, specifically Excel spreadsheets and hledger journals. It includes reconciliation logic to match new receipts against existing bank or ledger entries.

---

## Key References:

### c:\PTAReceiptParser\update_excel.py (lines 335-342)
```
    excel_path: Path,
    receipt: Receipt,
    categorized: list[tuple[ReceiptItem, str]],
    sheet_name: Optional[str] = None,
) -> bool:
    """Tek fiş için: aç → yaz → kaydet."""
    results = update_excel_batch(excel_path, [(receipt, categorized)], sheet_name)
    return results[0] is not None
```

### c:\PTAReceiptParser\update_excel.py (lines 184-221)
```
    excel_path: Path,
    receipt: Receipt,
    sheet_name: Optional[str] = None,
) -> tuple[Optional[int], Optional[str]]:
    """
    Excel dosyasında eşleşen from-account satırını bul.
    Döndürür: (row_number, account_name) — bulunamazsa (None, None).
    """
    try:
        from openpyxl import load_workbook
    except ImportError:
        print("  ❌ openpyxl kurulu değil. Kurmak için: pip install openpyxl")
        return None, None

    if not excel_path.exists():
        print(f"  ❌ Excel dosyası bulunamadı: {excel_path}")
        return None, None

    keep_vba = str(excel_path).lower().endswith(".xlsm")
    wb = load_workbook(str(excel_path), keep_vba=keep_vba)
    if sheet_name:
        if sheet_name not in wb.sheetnames:
            print(f"  ❌ Sheet bulunamadı: '{sheet_name}'")
            return None, None
        ws = wb[sheet_name]
    else:
        ws = wb.active

    if ws is None:
        return None, None
    
    from_row = find_excel_transaction(ws, receipt)
    if from_row is None:
        return None, None

    account = ws.cell(row=from_row, column=8).value  # H sütunu
    return from_row, str(account) if account else ""

```

### c:\PTAReceiptParser\update_journal.py (lines 206-274)
```
def main():
    if len(sys.argv) < 3:
        print("Kullanım: python update_journal.py <ocr.json> <journal.hledger>")
        sys.exit(1)

    ocr_path     = Path(sys.argv[1])
    journal_path = Path(sys.argv[2])

    if not ocr_path.exists():
        print(f"❌ OCR dosyası bulunamadı: {ocr_path}")
        sys.exit(1)
    if not journal_path.exists():
        print(f"❌ Journal dosyası bulunamadı: {journal_path}")
        sys.exit(1)

    import json
    ocr_json = json.loads(ocr_path.read_text(encoding="utf-8"))

    # 1. Fişi parse et
    print("\n── Fiş parse ediliyor ──────────────────────────────")
    receipt = parse_receipt(ocr_json)

    from parser import print_summary
    print_summary(receipt)

    # 2. Journal'da eşleşen transaction'ı bul
    print("── Journal taranıyor ───────────────────────────────")
    transactions = parse_journal(journal_path)
    tx = find_matching_transaction(receipt, transactions)

    if tx is None:
        print(f"❌ Eşleşen transaction bulunamadı!")
        print(f"   Aranan: {receipt.date}  {receipt.total:.2f} TL  ({receipt.store})")
        print(f"   Journal'ı kontrol edin veya tarihi/tutarı doğrulayın.")
        sys.exit(1)

    print(f"✓ Eşleşen transaction bulundu → satır {tx.start_line + 1}:")
    print(f"  {tx.raw_lines[0].strip()}")

    # 3. Kategorileri tespit et
    print("\n── Kategoriler tespit ediliyor ─────────────────────")
    rules = load_rules(RULES_FILE)
    # Öğrenilmiş kurallar varsa onları da ekle (önce uygulansın)
    learned_file = Path("rules_learned.toml")
    if learned_file.exists():
        learned = load_rules(learned_file)
        rules = learned + rules  # öğrenilmiş kurallar önce

    categorized = categorize_items(receipt, rules)

    print("\n  Sonuç:")
    for item, account in categorized:
        print(f"  {item.name:<40} → {account}")

    # 4. Önizleme + onay
    new_lines = build_new_transaction(tx, categorized, receipt)
    preview(new_lines)

    print("\nJournal güncellensin mi? [e/H] ", end="")
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "h"

    if answer == "e":
        update_journal(journal_path, tx, new_lines)
        print(f"✓ Journal güncellendi: {journal_path}")
    else:
        print("İptal edildi, journal değiştirilmedi.")
```


## Source Files:

- `update_excel.py`

