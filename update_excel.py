"""
Excel Muhasebe Defteri Güncelleyici
====================================
Fiş parser çıktısını (Receipt + kategorize edilmiş kalemler) Excel double-entry
defterine yazar. Tarih + tutar ile eşleşen transaction'ın to-account satırlarını
kalem kalem günceller.

Excel sütun yapısı:
    A  Tarih           (D.MM.YYYY — örn: 4.12.2025)
    B  Transaction Code
    C  Payee/Note
    D  Notes (kullanılmıyor)
    E  CURRENCY::TRY
    F  Operasyon (BUY/SELL/Interest/Dividend)
    G  Tag/Note
    H  Full Account Name  ← from-account (1. satır) ve to-accounts (sonraki satırlar)
    I  Tutar              ← Türk formatı: nokta=binlik, virgül=ondalık
    J  Rate/Price         (1,0000000)
    K  Reconciliation
    L  Not
    M  Bank Desc.

Transaction yapısı:
    1. satır  → A=tarih, B=txcode, C=payee, E=currency, H=from_hesap, I=tutar(neg)
    n. satır  → A=boş, H=to_hesap, I=tutar(pos), G=ürün_adı
    (sonraki A dolu satıra kadar devam eder)
"""

import re
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from parser import Receipt, ReceiptItem

AMOUNT_TOLERANCE = 0.02  # TL eşleşme toleransı


# ── Yardımcı: Tutar dönüşümleri ───────────────────────────────────────────────

def parse_excel_amount(value) -> Optional[float]:
    """
    Türk formatındaki tutar stringini float'a çevirir.
    '2.194,32' → 2194.32
    '-194,32'  → -194.32
    Sayısal değer (int/float) olarak gelirse doğrudan döndürür.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    # Türk formatı: nokta binlik ayracı, virgül ondalık
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def format_excel_amount(amount: float) -> str:
    """
    Float'ı Türk formatına çevirir.
    2194.32 → '2.194,32'
    -194.0  → '-194,00'
    """
    # Binlik ayraç olarak nokta, ondalık olarak virgül
    formatted = f"{abs(amount):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-{formatted}" if amount < 0 else formatted


# ── Yardımcı: Tarih dönüşümleri ───────────────────────────────────────────────

def parse_excel_date(value) -> Optional[str]:
    """
    Excel hücresinden gelen tarihi 'YYYY-MM-DD' formatına çevirir.
    Kabul edilen girişler:
        - datetime / date nesnesi (openpyxl bazen böyle okur)
        - '4.12.2025', '04.12.2025'  (D.MM.YYYY veya DD.MM.YYYY)
    """
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    if not s:
        return None
    # D.MM.YYYY veya DD.MM.YYYY
    m = re.match(r"^(\d{1,2})\.(\d{2})\.(\d{4})$", s)
    if m:
        day, month, year = m.group(1), m.group(2), m.group(3)
        return f"{year}-{month}-{int(day):02d}"
    return None


def receipt_date_to_excel(receipt_date: str) -> str:
    """
    'YYYY-MM-DD' → 'D.MM.YYYY' (Excel formatı, baştaki sıfır yok)
    '2025-12-04' → '4.12.2025'
    """
    dt = datetime.strptime(receipt_date, "%Y-%m-%d")
    return f"{dt.day}.{dt.month:02d}.{dt.year}"


# ── Excel transaction eşleştirme ──────────────────────────────────────────────

def find_excel_transaction(ws, receipt: Receipt, tolerance: float = AMOUNT_TOLERANCE) -> Optional[int]:
    """
    Worksheet'te Receipt'e uyan from-account satırının satır numarasını döndürür.
    Eşleşme kriterleri:
        - A sütunu: tarih == receipt.date
        - I sütunu: |tutar| ≈ receipt.total (±tolerance)
    Bulunamazsa None döner.
    """
    if not receipt.date or not receipt.total:
        return None

    for row in ws.iter_rows(min_row=1):
        a_cell = row[0]  # A sütunu
        i_cell = row[8]  # I sütunu (0-indexed → 8)

        if a_cell.value is None:
            continue

        row_date = parse_excel_date(a_cell.value)
        if row_date != receipt.date:
            continue

        row_amount = parse_excel_amount(i_cell.value)
        if row_amount is None:
            continue

        if abs(abs(row_amount) - receipt.total) <= tolerance:
            return a_cell.row

    return None


def get_to_account_rows(ws, from_row: int) -> tuple[int, int]:
    """
    from_row'dan sonraki to-account satır aralığını döndürür.
    To-account satırları: A sütunu boş olan ard arda satırlar.
    Döndürür: (first_to_row, last_to_row)
    Eğer hiç to-account satırı yoksa: (from_row + 1, from_row) — boş aralık
    """
    max_row = ws.max_row
    first_to = from_row + 1
    last_to = from_row  # başlangıçta boş aralık

    for r in range(from_row + 1, max_row + 2):
        if r > max_row:
            break
        a_val = ws.cell(row=r, column=1).value
        if a_val is not None and str(a_val).strip():
            break  # yeni transaction başladı
        last_to = r

    return first_to, last_to


# ── Eşleşme kontrolü (process_receipt'ten çağrılır) ──────────────────────────

def find_excel_match(
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

    wb = load_workbook(str(excel_path))
    if sheet_name:
        if sheet_name not in wb.sheetnames:
            print(f"  ❌ Sheet bulunamadı: '{sheet_name}'")
            return None, None
        ws = wb[sheet_name]
    else:
        ws = wb.active

    from_row = find_excel_transaction(ws, receipt)
    if from_row is None:
        return None, None

    account = ws.cell(row=from_row, column=8).value  # H sütunu
    return from_row, str(account) if account else ""


# ── Önizleme ──────────────────────────────────────────────────────────────────

def preview_excel(categorized: list[tuple[ReceiptItem, str]], receipt: Receipt):
    print("\n" + "═" * 60)
    print("  Önizleme — Excel'e yazılacak to-account satırları:")
    print("═" * 60)
    print(f"  Fiş: {receipt.store}  {receipt.date}  {format_excel_amount(receipt.total)} TRY")
    print()
    for item, account in categorized:
        amount_str = format_excel_amount(item.amount)
        print(f"  H: {account:<45}  I: {amount_str:>12}  G: {item.name}")
    print(f"\n  Toplam: {format_excel_amount(receipt.total)}")
    print("═" * 60)


# ── Ana güncelleme fonksiyonu ─────────────────────────────────────────────────

def update_excel(
    excel_path: Path,
    receipt: Receipt,
    categorized: list[tuple[ReceiptItem, str]],
    sheet_name: Optional[str] = None,
) -> bool:
    """
    Excel dosyasındaki eşleşen transaction'ın to-account satırlarını
    kategorize edilmiş fiş kalemleriyle günceller.

    Adımlar:
    1. Workbook aç
    2. Sheet seç (sheet_name veya ilk sheet)
    3. from-account satırını bul (tarih + tutar)
    4. Mevcut to-account satırlarını tespit et
    5. Eski to-account satırlarını sil
    6. Yeni kalem satırlarını ekle
    7. Kaydet

    Başarılıysa True, hata varsa False döner.
    """
    try:
        from openpyxl import load_workbook
    except ImportError:
        print("  ❌ openpyxl kurulu değil. Kurmak için: pip install openpyxl")
        return False

    if not excel_path.exists():
        print(f"  ❌ Excel dosyası bulunamadı: {excel_path}")
        return False

    wb = load_workbook(str(excel_path))

    if sheet_name:
        if sheet_name not in wb.sheetnames:
            print(f"  ❌ Sheet bulunamadı: '{sheet_name}'")
            print(f"     Mevcut sheetler: {', '.join(wb.sheetnames)}")
            return False
        ws = wb[sheet_name]
    else:
        ws = wb.active

    # 1. Eşleşen from-account satırını bul
    from_row = find_excel_transaction(ws, receipt)
    if from_row is None:
        print(f"  ❌ Excel'de eşleşen satır bulunamadı!")
        print(f"     Aranan: {receipt.date}  {receipt.total:.2f} TL")
        return False

    print(f"  ✓ Eşleşen satır: {from_row}  ({ws.cell(row=from_row, column=1).value}  {ws.cell(row=from_row, column=8).value})")

    # 2. Mevcut to-account satırlarını tespit et
    first_to, last_to = get_to_account_rows(ws, from_row)
    existing_to_count = max(0, last_to - first_to + 1)

    # from-row'dan E sütununu al (CURRENCY::TRY) — kopyalanacak
    currency_val = ws.cell(row=from_row, column=5).value  # E sütunu

    # 3. Mevcut to-account satırlarını sil
    if existing_to_count > 0:
        ws.delete_rows(first_to, existing_to_count)

    # 4. Yeni kalem satırları için yer aç
    new_count = len(categorized)
    if new_count > 0:
        ws.insert_rows(first_to, new_count)

    # 5. Yeni satırları doldur
    for idx, (item, account) in enumerate(categorized):
        r = first_to + idx
        # H: hesap adı
        ws.cell(row=r, column=8).value = account
        # I: tutar (pozitif, Türk formatı string olarak)
        ws.cell(row=r, column=9).value = format_excel_amount(item.amount)
        # J: rate
        ws.cell(row=r, column=10).value = "1"
        # G: ürün adı (yorum/not olarak)
        ws.cell(row=r, column=7).value = item.name

    wb.save(str(excel_path))
    return True
