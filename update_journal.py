"""
hledger Journal Güncelleyici
Kullanım: python update_journal.py <ocr_output.json> <journal.hledger>
"""

import re
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from parser import parse_receipt, Receipt, ReceiptItem
from rules import load_rules, find_account, append_learned_rule, DEFAULT_ACCOUNT


RULES_FILE = Path("rules.toml")
AMOUNT_TOLERANCE = 0.02  # TL cinsinden eşleşme toleransı


# ── hledger journal parse ──────────────────────────────────────────────────────

@dataclass
class Transaction:
    start_line: int       # journal'daki başlangıç satırı (0-indexed)
    end_line: int         # bitiş satırı (dahil)
    date: str
    description: str
    raw_lines: list[str]
    total: Optional[float]


def parse_journal(journal_path: Path) -> list[Transaction]:
    lines = journal_path.read_text(encoding="utf-8").splitlines()
    transactions = []
    i = 0

    while i < len(lines):
        line = lines[i]
        # Transaction başlangıcı: YYYY-MM-DD ile başlayan satır
        m = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(.+)", line)
        if m:
            date = m.group(1)
            desc = m.group(2).strip()
            start = i
            tx_lines = [line]
            i += 1
            # Sonraki girintili satırları topla
            while i < len(lines) and (lines[i].startswith(" ") or lines[i].startswith("\t")):
                tx_lines.append(lines[i])
                i += 1
            end = i - 1

            # Transaction'ın toplam tutarını bul (negatif olan liabilities satırı)
            total = None
            for tl in tx_lines:
                m2 = re.search(r"([\d,]+\.?\d*)\s+TRY", tl)
                if m2:
                    val = float(m2.group(1).replace(".","").replace(",", "."))
                    if total is None or val > total:
                        total = val

            transactions.append(Transaction(
                start_line=start,
                end_line=end,
                date=date,
                description=desc,
                raw_lines=tx_lines,
                total=total,
            ))
        else:
            i += 1

    return transactions


# ── Eşleştirme ────────────────────────────────────────────────────────────────

def find_matching_transaction(
    receipt: Receipt,
    transactions: list[Transaction],
) -> Optional[Transaction]:
    """Fişe uyan journal transaction'ını bul (tarih + tutar)."""
    if not receipt.date or not receipt.total:
        return None

    candidates = []
    for tx in transactions:
        if tx.date != receipt.date:
            continue
        if tx.total is None:
            continue
        if abs(tx.total - receipt.total) <= AMOUNT_TOLERANCE:
            candidates.append(tx)

    if len(candidates) == 1:
        return candidates[0]

    if len(candidates) > 1:
        # Birden fazla eşleşme → market adına da bak
        for tx in candidates:
            if re.search(receipt.store, tx.description, re.IGNORECASE):
                return tx
        return candidates[0]  # yine de ilkini seç

    return None


# ── Kategori tespiti & interaktif onay ───────────────────────────────────────

def categorize_items(
    receipt: Receipt,
    rules: list,
) -> list[tuple[ReceiptItem, str]]:
    """Her item için account tespit et. Bilinmeyenleri kullanıcıya sor."""
    results = []
    unknown_cache = {}  # aynı ismi tekrar sorma

    for item in receipt.items:
        account = find_account(item.name, receipt.store, item.amount, rules)

        if account:
            results.append((item, account))
            continue

        # Cache'de var mı?
        if item.name in unknown_cache:
            results.append((item, unknown_cache[item.name]))
            continue

        # Kullanıcıya sor
        print(f"\n  ❓ Tanınmayan ürün: \033[1m{item.name}\033[0m  ({item.amount:.2f} TL)")
        print(f"     Hangi hesaba gidecek? (boş bırakırsan '{DEFAULT_ACCOUNT}')")
        print(f"     Örnek: gider:market:gida:meyve")
        try:
            answer = input("     > ").strip()
        except (EOFError, KeyboardInterrupt):
            answer = ""

        chosen = answer if answer else DEFAULT_ACCOUNT
        unknown_cache[item.name] = chosen
        append_learned_rule(item.name, chosen)
        print(f"     ✓ '{chosen}' kaydedildi → rules_learned.toml'a eklendi")
        results.append((item, chosen))

    return results


# ── Journal güncelleme ────────────────────────────────────────────────────────

def build_new_transaction(
    tx: Transaction,
    categorized: list[tuple[ReceiptItem, str]],
    receipt: Receipt,
) -> list[str]:
    """Mevcut transaction'ı kalemli hale getir."""
    # İlk satır (tarih + açıklama) korunur
    new_lines = [tx.raw_lines[0]]

    # liabilities satırını bul (negatif veya liabilities içeren)
    liabilities_line = None
    for line in tx.raw_lines[1:]:
        if "borçlar" in line.lower() or "liabilit" in line.lower(): #TODO: Buraya negatif çıkan satırlar liability satırıdır diyebiliriz belki? ama birden çok satır olursa patlar.
            liabilities_line = line
            break

    # Eğer bulamazsa elle oluştur
    if not liabilities_line:
        liabilities_line = f"    Borçlar:Kart                          -{receipt.total:.2f} TRY"

    # Kalemler
    new_lines.append(liabilities_line)
    for item, account in categorized:
        comment = item.name
        new_lines.append(f"    {account:<45}  {item.amount:>8.2f} TRY  ; {comment}")
    
    return new_lines


def update_journal(journal_path: Path, tx: Transaction, new_lines: list[str]):
    """Journal dosyasında ilgili transaction'ı yeni satırlarla değiştir."""
    lines = journal_path.read_text(encoding="utf-8").splitlines()
    updated = (
        lines[:tx.start_line]
        + new_lines
        + lines[tx.end_line + 1:]
    )
    journal_path.write_text("\n".join(updated) + "\n", encoding="utf-8")


# ── Önizleme ──────────────────────────────────────────────────────────────────

def preview(new_lines: list[str]):
    print("\n" + "═" * 60)
    print("  Önizleme — journal'a yazılacak:")
    print("═" * 60)
    for line in new_lines:
        print(line)
    print("═" * 60)


# ── Ana akış ──────────────────────────────────────────────────────────────────

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


if __name__ == "__main__":
    main()
