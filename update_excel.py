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
    D  Notes (yedekleme için kullanılıyor)
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

COM notu:
    Excel'i görünmez bir instance olarak açar, işlem yapar, kapatır.
    Dosya zaten Excel'de açıksa mevcut instance'a bağlanır (veri kaybı yok).
    Gereksinim: pip install pywin32   (Windows only)
"""

import re
from contextlib import contextmanager
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from parser import Receipt, ReceiptItem

AMOUNT_TOLERANCE = 0.02  # TL eşleşme toleransı


# ── Yardımcı: Tutar dönüşümleri ───────────────────────────────────────────────

def parse_excel_amount(value) -> Optional[float]:
    """
    Excel hücresinden gelen tutarı float'a çevirir.
    Hem Türk formatı ('2.194,32') hem sayısal değer kabul edilir.
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


def format_excel_amount(amount: Optional[float]) -> str:
    """
    Float'ı Türk formatına çevirir.
    2194.32 → '2.194,32'
    -194.0  → '-194,00'
    """
    if amount is None:
        return "0,00"
    formatted = f"{abs(amount):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if amount < 0:
        formatted = f"-{formatted}"
    return formatted


# ── Yardımcı: Tarih dönüşümleri ───────────────────────────────────────────────

def parse_excel_date(value) -> Optional[str]:
    """
    Excel hücresinden gelen tarihi 'YYYY-MM-DD' formatına çevirir.
    Kabul edilen girişler:
        - datetime / date nesnesi (COM bazen böyle döndürür)
        - '4.12.2025', '04.12.2025'  (D.MM.YYYY veya DD.MM.YYYY)
    """
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    if not s:
        return None
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


# ── COM context manager ───────────────────────────────────────────────────────

@contextmanager
def _excel_workbook(excel_path: Path, sheet_name: Optional[str] = None):
    """
    Excel COM context manager.

    - Excel zaten açıksa mevcut instance'a bağlanır (GetActiveObject).
    - Değilse görünmez yeni bir instance açar.
    - Çıkışta yalnızca *bizim açtığımız* instance'ı kapatır.

    Yield eder: (wb_com_object, ws_com_object)
    Hata olursa IOError fırlatır.
    """
    try:
        import win32com.client
        import pywintypes
    except ImportError:
        raise ImportError(
            "pywin32 kurulu değil. Kurmak için: pip install pywin32"
        )

    target_path = excel_path.resolve()
    xl_app      = None
    wb          = None
    we_opened   = False   # bu bağlamda biz mi açtık?
    old_alerts  = None    # mevcut instance'ın DisplayAlerts değeri

    # Açık Excel instance'larında istediğimiz dosyayı ara
    # (GetActiveObject sadece tek instance döndürür; birden fazla Excel
    #  açıksa bu yeterli değil — ama tipik kullanım için yeterli)
    try:
        xl_app = win32com.client.GetActiveObject("Excel.Application")
        old_alerts = xl_app.DisplayAlerts
        xl_app.DisplayAlerts = False
        # Path karşılaştırması: Path.resolve() ile normalize et
        for i in range(1, xl_app.Workbooks.Count + 1):
            try:
                wb_path = Path(xl_app.Workbooks(i).FullName).resolve()
                if wb_path == target_path:
                    wb = xl_app.Workbooks(i)
                    print(f"  [Excel] Açık dosyaya bağlandı: {wb_path.name}")
                    break
            except Exception:
                continue
        if wb is None:
            # Excel açık ama bu dosya değil — aynı instance'da aç
            print(f"  [Excel] Yeni workbook açılıyor (Excel çalışıyor ama dosya açık değil)")
            wb = xl_app.Workbooks.Open(str(target_path))
            we_opened = True
    except pywintypes.com_error:
        # Excel hiç açık değil — görünmez yeni instance başlat
        print(f"  [Excel] Yeni Excel instance başlatılıyor")
        xl_app = win32com.client.Dispatch("Excel.Application")
        xl_app.Visible = False
        xl_app.DisplayAlerts = False
        wb = xl_app.Workbooks.Open(str(target_path))
        we_opened = True

    try:
        # Sheet seç
        if sheet_name:
            try:
                ws = wb.Worksheets(sheet_name)
            except pywintypes.com_error:
                names = [wb.Worksheets(i).Name for i in range(1, wb.Worksheets.Count + 1)]
                raise IOError(f"Sheet bulunamadı: '{sheet_name}'. Mevcut: {', '.join(names)}")
        else:
            ws = wb.ActiveSheet

        yield wb, ws

    finally:
        # Mevcut instance'ın DisplayAlerts değerini geri yükle
        if old_alerts is not None and xl_app is not None:
            try:
                xl_app.DisplayAlerts = old_alerts
            except Exception:
                pass
        if we_opened:
            wb.Close(SaveChanges=False)  # kaydetmeyi çağıran taraf yapar
            if xl_app.Workbooks.Count == 0:
                xl_app.Quit()


# ── Excel transaction eşleştirme ──────────────────────────────────────────────

def _find_transaction_in_ws(ws, receipt: Receipt, tolerance: float = AMOUNT_TOLERANCE) -> Optional[int]:
    """
    Açık worksheet COM nesnesinde Receipt'e uyan from-account satırını döndürür.
    Eşleşme: A=tarih AND |I|≈toplam
    """
    if not receipt.date or not receipt.total:
        return None

    used_range = ws.UsedRange
    last_row   = used_range.Row + used_range.Rows.Count - 1

    for r in range(1, last_row + 1):
        a_val = ws.Cells(r, 1).Value   # A
        if a_val is None:
            continue
        row_date = parse_excel_date(a_val)
        if row_date != receipt.date:
            continue

        i_val    = ws.Cells(r, 9).Value  # I
        row_amt  = parse_excel_amount(i_val)
        if row_amt is None:
            continue

        if abs(abs(row_amt) - receipt.total) <= tolerance:
            return r

    return None


def _get_to_account_rows(ws, from_row: int) -> tuple[int, int]:
    """
    from_row'dan sonraki to-account satır aralığını döndürür.
    To-account satırları: A sütunu boş olan ard arda satırlar.
    Döndürür: (first_to_row, last_to_row)
    Eğer hiç to-account satırı yoksa: (from_row+1, from_row) — boş aralık
    """
    used_range = ws.UsedRange
    max_row    = used_range.Row + used_range.Rows.Count - 1

    first_to = from_row + 1
    last_to  = from_row  # başlangıçta boş aralık

    for r in range(from_row + 1, max_row + 2):
        if r > max_row:
            break
        a_val = ws.Cells(r, 1).Value
        if a_val is not None and str(a_val).strip():
            break
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
    if not excel_path.exists():
        print(f"  ❌ Excel dosyası bulunamadı: {excel_path}")
        return None, None

    try:
        with _excel_workbook(excel_path, sheet_name) as (_, ws):
            from_row = _find_transaction_in_ws(ws, receipt)
            if from_row is None:
                return None, None
            account = ws.Cells(from_row, 8).Value  # H
            return from_row, str(account) if account else ""
    except (ImportError, IOError) as e:
        print(f"  ❌ {e}")
        return None, None


# ── Mevcut to-account okuma ───────────────────────────────────────────────────

def read_excel_to_accounts(
    excel_path: Path,
    receipt: Receipt,
    sheet_name: Optional[str] = None,
) -> dict[float, str]:
    """
    Excel'deki mevcut to-account satırlarını okur.
    Döndürür: {tutar: hesap_adı}
    """
    if not excel_path.exists():
        return {}

    try:
        with _excel_workbook(excel_path, sheet_name) as (_, ws):
            from_row = _find_transaction_in_ws(ws, receipt)
            if from_row is None:
                return {}

            first_to, last_to = _get_to_account_rows(ws, from_row)
            if last_to < first_to:
                return {}

            result: dict[float, str] = {}
            for r in range(first_to, last_to + 1):
                account = ws.Cells(r, 8).Value   # H
                amount  = parse_excel_amount(ws.Cells(r, 9).Value)  # I
                if account and amount is not None:
                    result[round(amount, 2)] = str(account)
            return result
    except (ImportError, IOError):
        return {}


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


# ── Ana güncelleme: tek worksheet'e yaz ──────────────────────────────────────

def _apply_to_ws(ws, receipt: Receipt, categorized: list[tuple[ReceiptItem, str]]) -> Optional[int]:
    """
    Açık worksheet COM nesnesine tek bir fişi yazar. Kaydetmez.
    Başarılıysa from_row (int) döner, bulunamazsa None.
    """
    from_row = _find_transaction_in_ws(ws, receipt)
    if from_row is None:
        print(f"  ❌ Excel'de eşleşen satır bulunamadı!")
        total_str = f"{receipt.total:.2f} TL" if receipt.total else "toplam bilinmiyor"
        print(f"     Aranan: {receipt.date}  {total_str}")
        return None

    a_val = ws.Cells(from_row, 1).Value
    h_val = ws.Cells(from_row, 8).Value
    print(f"  ✓ Eşleşen satır: {from_row}  ({a_val}  {h_val})")

    first_to, last_to = _get_to_account_rows(ws, from_row)
    existing_count    = max(0, last_to - first_to + 1)

    # Mevcut to-account'ları D sütununa yedekle
    if existing_count > 0:
        from datetime import date as _date
        from_acc = ws.Cells(from_row, 8).Value
        parts = []
        for r in range(first_to, last_to + 1):
            acc = ws.Cells(r, 8).Value
            if acc and acc != from_acc:
                parts.append(str(acc))
        if parts:
            ws.Cells(from_row, 4).Value = f"[{_date.today()}] " + " | ".join(parts)

        # COM ile satır silme — merged cell sorunu yok
        del_range = ws.Rows(f"{first_to}:{last_to}")
        del_range.Delete()

    # Yeni satırları ekle
    new_count = len(categorized)
    if new_count > 0:
        ws.Rows(f"{first_to}:{first_to + new_count - 1}").Insert()

    for idx, (item, account) in enumerate(categorized):
        r = first_to + idx
        ws.Cells(r, 7).Value  = item.name    # G
        ws.Cells(r, 8).Value  = account       # H
        ws.Cells(r, 9).Value  = round(item.amount, 2)  # I — Excel kendi formatını uygular
        ws.Cells(r, 10).Value = "1"           # J

    return from_row


# ── Public API ────────────────────────────────────────────────────────────────

def update_excel(
    excel_path: Path,
    receipt: Receipt,
    categorized: list[tuple[ReceiptItem, str]],
    sheet_name: Optional[str] = None,
) -> bool:
    """Tek fiş için: aç → yaz → kaydet."""
    results = update_excel_batch(excel_path, [(receipt, categorized)], sheet_name)
    return results[0] is not None


def update_excel_batch(
    excel_path: Path,
    items: list[tuple[Receipt, list[tuple[ReceiptItem, str]]]],
    sheet_name: Optional[str] = None,
) -> list[Optional[int]]:
    """
    Birden fazla fişi tek open+save ile yazar.
    Döndürür: her fiş için from_row (başarılıysa int, bulunamazsa None).
    """
    if not excel_path.exists():
        print(f"  ❌ Excel dosyası bulunamadı: {excel_path}")
        return [None] * len(items)

    try:
        with _excel_workbook(excel_path, sheet_name) as (wb, ws):
            results = [_apply_to_ws(ws, receipt, categorized) for receipt, categorized in items]
            wb.Save()
            return results
    except ImportError as e:
        print(f"  ❌ {e}")
        return [None] * len(items)
    except IOError as e:
        print(f"  ❌ {e}")
        return [None] * len(items)
