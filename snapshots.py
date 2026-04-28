"""
Parse Snapshot Sistemi
======================
Başarılı parse sonuçlarını (toplam tuttuğunda) bir JSON dosyasına kaydeder.
Regresyon testi için: regex/kod değişikliklerinden sonra eski fişlerin
hâlâ aynı sonuçları verip vermediğini kontrol eder.

Kullanım:
    # Regresyon testi (tüm snapshot'ları yeniden parse et):
    python snapshots.py --regression

    # Belirli bir OCR cache dizini ile:
    python snapshots.py --regression --cache-dir .ocr_cache
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from parser import parse_receipt, Receipt

SNAPSHOTS_FILE = Path(".parse_snapshots") / "snapshots.json"
AMOUNT_TOLERANCE = 0.02  # TL


# ── Snapshot okuma/yazma ───────────────────────────────────────────────────────

def _load_snapshots() -> dict:
    if not SNAPSHOTS_FILE.exists():
        return {}
    try:
        return json.loads(SNAPSHOTS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_snapshots(data: dict) -> None:
    SNAPSHOTS_FILE.parent.mkdir(exist_ok=True)
    SNAPSHOTS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _receipt_to_snapshot(receipt: Receipt) -> dict:
    return {
        "store": receipt.store,
        "date": receipt.date,
        "total": receipt.total,
        "items_sum": round(sum(i.amount for i in receipt.items), 2),
        "items": [
            {"name": i.name, "amount": i.amount}
            for i in receipt.items
        ],
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }

# ── Dışarıya açık API ─────────────────────────────────────────────────────────

def totals_match(receipt: Receipt) -> bool:
    """Hesaplanan toplam ile fişteki toplam örtüşüyor mu?"""
    if receipt.total is None:
        return False
    calc = sum(i.amount for i in receipt.items)
    return abs(calc - receipt.total) <= AMOUNT_TOLERANCE

def save_snapshot(ocr_path: Path, receipt: Receipt) -> bool:
    """
    Toplam tutuyorsa snapshot kaydeder.
    Daha önce kaydedilmişse üzerine yazar (güncelleme).
    Döndürür: kaydedildiyse True, atlandıysa False.
    """
    if not totals_match(receipt):
        return False

    key = ocr_path.name
    data = _load_snapshots()
    data[key] = _receipt_to_snapshot(receipt)
    _save_snapshots(data)
    return True


def check_snapshot(ocr_path: Path, receipt: Receipt) -> list[str]:
    """
    Mevcut parse sonucunu kaydedilmiş snapshot ile karşılaştırır.
    Döndürür: fark mesajlarının listesi (boşsa regresyon yok).
    """
    key = ocr_path.name
    data = _load_snapshots()

    if key not in data:
        return []  # Henüz snapshot yok, sorun değil

    snap = data[key]
    diffs: list[str] = []

    # Toplam değişti mi?
    if receipt.total != snap.get("total"):
        diffs.append(
            f"Toplam değişti: {snap.get('total')} -> {receipt.total}"
        )

    # Hesaplanan toplam değişti mi?
    new_sum = round(sum(i.amount for i in receipt.items), 2)
    if new_sum != snap.get("items_sum"):
        diffs.append(
            f"Kalem toplamı değişti: {snap.get('items_sum')} -> {new_sum}"
        )

    # Kalem sayısı değişti mi?
    snap_items = snap.get("items", [])
    if len(receipt.items) != len(snap_items):
        diffs.append(
            f"Kalem sayısı değişti: {len(snap_items)} -> {len(receipt.items)}"
        )

    # Her kalemi karşılaştır (isim + tutar)
    snap_map = {it["name"]: it["amount"] for it in snap_items}
    new_map  = {it.name: it.amount for it in receipt.items}

    for name, amount in new_map.items():
        if name not in snap_map:
            diffs.append(f"Yeni kalem: {name}  {amount:.2f} TL")
        elif abs(amount - snap_map[name]) > AMOUNT_TOLERANCE:
            diffs.append(
                f"Tutar değişti [{name}]: {snap_map[name]:.2f} -> {amount:.2f} TL"
            )

    for name in snap_map:
        if name not in new_map:
            diffs.append(f"Kaybolan kalem: {name}  {snap_map[name]:.2f} TL")

    return diffs


# ── Regresyon testi ────────────────────────────────────────────────────────────

def run_regression(cache_dir: Path = Path(".ocr_cache")) -> int:
    """
    Tüm snapshot'ları yeniden parse ederek regresyon testi yapar.
    Döndürür: bulunan regresyon sayısı.
    """
    data = _load_snapshots()
    if not data:
        print("Henüz hiç snapshot kaydedilmemiş.")
        return 0

    print("=" * 60)
    print(f"  Regresyon Testi  ({len(data)} snapshot)")
    print("=" * 60)

    regressions = 0
    ok_count     = 0
    skip_count   = 0

    for key, snap in data.items():
        ocr_file = cache_dir / key
        if not ocr_file.exists():
            print(f"  [ATLANDI]  {key}  (OCR cache bulunamadi)")
            skip_count += 1
            continue

        try:
            ocr_json = json.loads(ocr_file.read_text(encoding="utf-8"))
            receipt  = parse_receipt(ocr_json)
        except Exception as e:
            print(f"  [HATA]     {key}  ({e})")
            regressions += 1
            continue

        diffs = check_snapshot(ocr_file, receipt)
        if diffs:
            print(f"  [REGRESYON]  {key}")
            for d in diffs:
                print(f"      - {d}")
            regressions += 1
        else:
            print(f"  [OK]         {key}")
            ok_count += 1

    print("-" * 60)
    print(f"  Sonuc: {ok_count} OK, {regressions} regresyon, {skip_count} atlandi")
    print("=" * 60)
    print()
    return regressions


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    cache_dir = Path(".ocr_cache")

    if "--cache-dir" in sys.argv:
        idx = sys.argv.index("--cache-dir")
        if idx + 1 < len(sys.argv):
            cache_dir = Path(sys.argv[idx + 1])

    if "--regression" in sys.argv:
        regressions = run_regression(cache_dir)
        sys.exit(1 if regressions else 0)

    print("Kullanım:")
    print("  python snapshots.py --regression")
    print("  python snapshots.py --regression --cache-dir .ocr_cache")
    sys.exit(0)


if __name__ == "__main__":
    main()
