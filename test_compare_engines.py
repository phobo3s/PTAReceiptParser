#!/usr/bin/env python3
"""
Paddle vs Tesseract karşılaştırma testi
Mevcut cache dosyalarını kullanır — yeni OCR yapmaz.
"""

import json
import statistics
from pathlib import Path
from parser import parse_receipt

CACHE = Path(".ocr_cache")

# Paddle json'larını bul (suffix içermeyen = paddle)
paddle_files = sorted([
    f for f in CACHE.glob("*.json")
    if "_tesseract" not in f.name
    and "_windows" not in f.name
])

print("=" * 90)
print(f"PADDLE vs TESSERACT KARŞILAŞTIRMA  ({len(paddle_files)} fiş)")
print("=" * 90)

rows = []
paddle_only_fail = []
tess_only_fail   = []
both_fail        = []

for pf in paddle_files:
    stem = pf.stem   # e.g. "Belge 3_4"
    tf   = CACHE / f"{stem}_tesseract.json"

    p_data = json.loads(pf.read_text(encoding="utf-8"))
    t_data = json.loads(tf.read_text(encoding="utf-8")) if tf.exists() else None

    # Parse
    try:
        pr = parse_receipt(p_data)
    except Exception as e:
        pr = None

    try:
        tr = parse_receipt(t_data) if t_data else None
    except Exception as e:
        tr = None

    # Tesseract confidence stats
    tess_confs = []
    if t_data:
        for det in t_data.get("detections", []):
            c = det[1][1]
            if c > 0:
                tess_confs.append(c)
    tess_low = sum(1 for c in tess_confs if c < 0.60)
    tess_avg = statistics.mean(tess_confs) if tess_confs else 0

    rows.append({
        "name":      stem,
        "p_store":   (pr.store  or "—") if pr else "—",
        "p_date":    (pr.date   or "—") if pr else "—",
        "p_total":   f"{pr.total:.2f}" if (pr and pr.total is not None) else "—",
        "p_items":   len(pr.items) if pr else 0,
        "t_store":   (tr.store  or "—") if tr else "—",
        "t_date":    (tr.date   or "—") if tr else "—",
        "t_total":   f"{tr.total:.2f}" if (tr and tr.total is not None) else "—",
        "t_items":   len(tr.items) if tr else 0,
        "t_avg":     f"{tess_avg:.2f}",
        "t_low":     tess_low,
        "t_total_det": len(tess_confs),
        "match":     (pr and tr and
                      pr.store == tr.store and
                      pr.date  == tr.date  and
                      pr.total == tr.total),
    })

    if pr and not tr:    paddle_only_fail.append(stem)
    if tr and not pr:    tess_only_fail.append(stem)
    if not pr and not tr: both_fail.append(stem)

# ── Çıktı ────────────────────────────────────────────────────────────────────
HDR = f"{'Fiş':<34} {'P.Store':<10} {'P.Date':<12} {'P.Tot':>8} {'P.#':>3}  " \
      f"{'T.Store':<10} {'T.Date':<12} {'T.Tot':>8} {'T.#':>3}  " \
      f"{'T.Avg':>6} {'T.Low%':>6}  {'EŞ':>3}"
print(HDR)
print("-" * 120)

match_count = 0
for r in rows:
    low_pct = f"{100*r['t_low']/r['t_total_det']:.0f}%" if r['t_total_det'] else "—"
    icon    = "✅" if r["match"] else "⚠️ "
    if r["match"]: match_count += 1
    print(
        f"{r['name']:<34} {r['p_store']:<10} {r['p_date']:<12} {r['p_total']:>8} {r['p_items']:>3}  "
        f"{r['t_store']:<10} {r['t_date']:<12} {r['t_total']:>8} {r['t_items']:>3}  "
        f"{r['t_avg']:>6} {low_pct:>6}  {icon}"
    )

print("=" * 120)
print(f"\nSONUÇ: {match_count}/{len(rows)} fiş tam eşleşti (store+date+total)")

# ── Uyuşmazlık detayı ────────────────────────────────────────────────────────
mismatches = [r for r in rows if not r["match"]]
if mismatches:
    print(f"\n{'─'*90}")
    print("UYUŞMAZLIK DETAYI:")
    for r in mismatches:
        print(f"\n  [{r['name']}]")
        if r["p_store"] != r["t_store"]:
            print(f"    store  : Paddle={r['p_store']!r:<12}  Tesseract={r['t_store']!r}")
        if r["p_date"]  != r["t_date"]:
            print(f"    date   : Paddle={r['p_date']!r:<12}  Tesseract={r['t_date']!r}")
        if r["p_total"] != r["t_total"]:
            print(f"    total  : Paddle={r['p_total']!r:<12}  Tesseract={r['t_total']!r}")
        if r["p_items"] != r["t_items"]:
            print(f"    items  : Paddle={r['p_items']:<12}  Tesseract={r['t_items']}")

# ── Tesseract confidence analizi ─────────────────────────────────────────────
print(f"\n{'─'*90}")
print("TESSERACT CONFIDENCE ANALİZİ (eğik/bulanık metin tespiti):")
print(f"{'Fiş':<34} {'Toplam Det':>10} {'Avg Conf':>9} {'<0.60':>6} {'<0.80':>6}  DURUM")
print("-" * 80)

for r in rows:
    n   = r["t_total_det"]
    avg = float(r["t_avg"]) if r["t_avg"] != "0.00" else 0
    low = r["t_low"]
    low80 = 0  # recalculate below
    pct = 100*low/n if n else 0
    status = "⚠️  DÜŞÜK" if (avg < 0.75 or pct > 30) else "✅ İyi"
    print(f"{r['name']:<34} {n:>10} {avg:>9.3f} {low:>6} {pct:>5.0f}%  {status}")

print("\n(Not: Tesseract confidence <0.60 = gürültü/eğik/bulanık tahmin)")

# ── Detaylı sorunlu fiş ──────────────────────────────────────────────────────
print(f"\n{'─'*90}")
print("EĞİK METİN ÖRNEKLERİ (Tesseract conf<0.50 olan detection'lar):")
for pf in paddle_files:
    stem = pf.stem
    tf   = CACHE / f"{stem}_tesseract.json"
    if not tf.exists(): continue
    t_data = json.loads(tf.read_text(encoding="utf-8"))
    very_low = [(det[1][0], det[1][1]) for det in t_data.get("detections", [])
                if 0 < det[1][1] < 0.50]
    if len(very_low) > 5:
        print(f"\n  [{stem}] — {len(very_low)} çok düşük confidence:")
        for text, conf in very_low[:8]:
            print(f"    conf={conf:.2f}  text={text!r}")
        if len(very_low) > 8:
            print(f"    ... ({len(very_low)-8} daha)")
