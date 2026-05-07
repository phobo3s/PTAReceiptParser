"""
LLM tabanlı fiş parser — regex yerine Claude API kullanır.

Kullanım:
    from llm_parser import parse_with_llm
    receipt = parse_with_llm(ocr_json, api_key="sk-ant-...")

    # CLI:
    python llm_parser.py .ocr_cache/WhatsApp*.json --api-key sk-ant-...
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

from parser import Detection, Receipt, ReceiptItem, load_detections, group_into_rows
from config import OCR_CACHE_DIR, PARSE_LLM_CACHE_DIR

CACHE_DIR = PARSE_LLM_CACHE_DIR
MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """\
Türkçe termal fiş görüntülerinden OCR ile çıkarılmış metin satırları verilecek.
Görevin: satırları analiz ederek fişin yapısını JSON olarak çıkarmak.

Kurallar:
- store: market/işyeri adı (ör. "BİM", "Migros", "Tankar"). Bulamazsan null.
- date: YYYY-MM-DD formatında. Bulamazsan null.
- items: satın alınan ürün/hizmet listesi.
  - Her item için: name (temizlenmiş ürün adı) ve amount (TL cinsinden float).
  - KDV, TOPLAM, ödeme bilgisi, tarih, barkod gibi satırlar item DEĞİLDİR.
  - Tartılı ürünler (0,74kg × 19,75 gibi) varsa adın sonuna ekle: "PATATES (0.74kg × 19.75)".
  - OCR hatası olan isimleri düzelt: küçük 'i' → büyük 'İ', harf bağlamında '0' → 'O'.
- total: Odenecek/TOPLAM satırındaki float değer. Bulamazsan null.
- Her sayısal değerde Türkçe format kullanılmış olabilir (1.234,56 → 1234.56).

Sadece JSON döndür, başka açıklama ekleme. Format:
{
  "store": "BİM",
  "date": "2026-03-26",
  "total": 333.07,
  "items": [
    {"name": "KEKCİK.KAP30G PİNGUİ", "amount": 26.00},
    {"name": "PATATES (0.74kg × 19.75)", "amount": 14.62}
  ]
}
"""


def _parse_turkish_number(s: str) -> Optional[float]:
    """1.234,56 veya 234,56 veya 234.56 → float."""
    s = s.strip()
    if re.match(r'^\d{1,3}(\.\d{3})+,\d{2}$', s):
        return float(s.replace(".", "").replace(",", "."))
    if re.match(r'^\d+,\d{2}$', s):
        return float(s.replace(",", "."))
    if re.match(r'^\d+\.\d{2}$', s):
        return float(s)
    try:
        return float(s)
    except ValueError:
        return None


def _ocr_to_text(ocr_json: dict) -> str:
    """OCR JSON → okunabilir satır metni (Y sıralı, satır gruplu)."""
    detections = load_detections(ocr_json)
    if not detections:
        return ""

    y_tol = (max(d.y_max for d in detections) - min(d.y_min for d in detections)) * 0.02
    y_tol = max(10, min(y_tol, 30))

    rows = group_into_rows(detections, y_tolerance=y_tol)

    lines = []
    for row in rows:
        parts = [d.text for d in row]
        lines.append("  ".join(parts))
    return "\n".join(lines)


def _call_claude(text: str, api_key: str) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"Fiş OCR metni:\n\n{text}"
        }]
    )
    raw = msg.content[0].text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
    if m:
        raw = m.group(1)
    return json.loads(raw)


def show_formatted_text(ocr_json: dict, label: str = "") -> None:
    """OCR JSON'un LLM'e gönderilecek formatını ekrana yaz (dry-run için)."""
    text = _ocr_to_text(ocr_json)
    if label:
        print(f"\n{'─'*60}")
        print(f"[{label}]")
        print(f"{'─'*60}")
    print(text)


def _build_receipt(data: dict, detections: list[Detection]) -> Receipt:
    items = []
    for it in data.get("items") or []:
        name = (it.get("name") or "").strip()
        amt_raw = it.get("amount")
        if not name:
            continue
        if isinstance(amt_raw, (int, float)):
            amount = float(amt_raw)
        elif isinstance(amt_raw, str):
            amount = _parse_turkish_number(amt_raw) or 0.0
        else:
            amount = 0.0
        items.append(ReceiptItem(name=name, amount=amount, raw_name=name))

    total_raw = data.get("total")
    if isinstance(total_raw, (int, float)):
        total = float(total_raw)
    elif isinstance(total_raw, str):
        total = _parse_turkish_number(total_raw)
    else:
        total = None

    return Receipt(
        store=data.get("store"),
        date=data.get("date"),
        items=items,
        total=total,
        raw_detections=detections,
    )


def parse_with_llm(
    ocr_json: dict,
    api_key: Optional[str] = None,
    cache_key: Optional[str] = None,
) -> Receipt:
    """
    OCR JSON → Receipt (LLM tabanlı).

    cache_key: .parse_llm_cache/{cache_key}.json — None ise cache kullanılmaz.
    api_key: ANTHROPIC_API_KEY env'den de alınabilir.
    """
    if api_key is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY gerekli (env veya parametre).")

    detections = load_detections(ocr_json)

    # Cache kontrolü
    cache_file: Optional[Path] = None
    if cache_key:
        CACHE_DIR.mkdir(exist_ok=True)
        cache_file = CACHE_DIR / f"{cache_key}.json"
        if cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            return _build_receipt(data, detections)

    text = _ocr_to_text(ocr_json)
    data = _call_claude(text, api_key)

    if cache_file:
        cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    return _build_receipt(data, detections)


# ── CLI: tüm cache dosyalarını karşılaştır ────────────────────────────────────

def _compare_all(api_key: str, force: bool = False):
    from parser import parse_receipt as regex_parse

    cache_files = sorted([
        f for f in OCR_CACHE_DIR.glob("*.json")
        if "_tesseract" not in f.name and "_windows" not in f.name
    ])

    print(f"\n{'='*100}")
    print(f"REGEX vs LLM KARŞILAŞTIRMA  ({len(cache_files)} fiş)")
    print(f"{'='*100}")
    HDR = (
        f"{'Fiş':<42} "
        f"{'R.Store':<12} {'R.Date':<12} {'R.Total':>9} {'R.#':>3}  "
        f"{'L.Store':<12} {'L.Date':<12} {'L.Total':>9} {'L.#':>3}  EŞ"
    )
    print(HDR)
    print("-" * 120)

    ok = mismatch = llm_fail = regex_fail = 0

    for cf in cache_files:
        ocr_json = json.loads(cf.read_text(encoding="utf-8"))
        stem = cf.stem

        try:
            rr = regex_parse(ocr_json)
        except Exception:
            rr = None

        ck = stem if not force else None
        try:
            lr = parse_with_llm(ocr_json, api_key=api_key, cache_key=stem)
        except Exception as e:
            lr = None
            print(f"  [LLM HATA] {stem}: {e}", file=sys.stderr)

        r_store = rr.store or "—" if rr else "—"
        r_date  = rr.date  or "—" if rr else "—"
        r_total = f"{rr.total:.2f}" if (rr and rr.total is not None) else "—"
        r_items = len(rr.items) if rr else 0

        l_store = lr.store or "—" if lr else "—"
        l_date  = lr.date  or "—" if lr else "—"
        l_total = f"{lr.total:.2f}" if (lr and lr.total is not None) else "—"
        l_items = len(lr.items) if lr else 0

        match = (rr and lr and
                 rr.store == lr.store and
                 rr.date  == lr.date  and
                 rr.total is not None and lr.total is not None and
                 abs(rr.total - lr.total) < 0.01)
        icon = "✅" if match else "⚠️ "
        if match:
            ok += 1
        else:
            mismatch += 1
        if not rr:
            regex_fail += 1
        if not lr:
            llm_fail += 1

        name = stem[:40]
        print(
            f"{name:<42} "
            f"{r_store:<12} {r_date:<12} {r_total:>9} {r_items:>3}  "
            f"{l_store:<12} {l_date:<12} {l_total:>9} {l_items:>3}  {icon}"
        )

    print("=" * 120)
    print(f"\nSONUÇ: {ok}/{len(cache_files)} tam eşleşti  |  "
          f"Regex fail: {regex_fail}  |  LLM fail: {llm_fail}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="Belirli OCR JSON dosyaları")
    ap.add_argument("--api-key", default=os.environ.get("ANTHROPIC_API_KEY"))
    ap.add_argument("--compare", action="store_true", help="Regex vs LLM karşılaştır")
    ap.add_argument("--force", action="store_true", help="Cache'i yoksay, yeniden çağır")
    ap.add_argument("--dry-run", action="store_true", help="LLM'e gönderilecek metni göster, API çağırma")
    args = ap.parse_args()

    if args.dry_run:
        files = args.files or sorted([
            str(f) for f in OCR_CACHE_DIR.glob("*.json")
            if "_tesseract" not in f.name and "_windows" not in f.name
        ])
        for path in files:
            ocr_json = json.loads(Path(path).read_text(encoding="utf-8"))
            show_formatted_text(ocr_json, label=Path(path).stem[:60])
        sys.exit(0)

    if not args.api_key:
        print("HATA: ANTHROPIC_API_KEY gerekli (env veya --api-key)")
        print("      Önce formatı görmek için: python llm_parser.py --dry-run")
        sys.exit(1)

    if args.compare or not args.files:
        _compare_all(args.api_key, force=args.force)
    else:
        for path in args.files:
            ocr_json = json.loads(Path(path).read_text(encoding="utf-8"))
            stem = Path(path).stem
            r = parse_with_llm(ocr_json, api_key=args.api_key, cache_key=stem)
            print(f"\n[{stem}]")
            print(f"  Store : {r.store}")
            print(f"  Date  : {r.date}")
            print(f"  Total : {r.total}")
            print(f"  Items ({len(r.items)}):")
            for it in r.items:
                print(f"    {it.amount:>8.2f}  {it.name}")
