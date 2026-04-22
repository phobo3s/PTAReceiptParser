#!/usr/bin/env python3
"""Yeni fiş dosyalarını test et"""

import json
from pathlib import Path
from parser import parse_receipt

test_files = {
    "FSREF (1)": ".ocr_cache/WhatsApp Image 2026-04-20 at 17.35.07.json",
    "FSREF (2)": ".ocr_cache/WhatsApp Image 2026-04-20 at 17.35.07 (1).json",
    "METRO": ".ocr_cache/WhatsApp Image 2026-04-20 at 17.35.08.json",
    "METRO e-Fatura": ".ocr_cache/WhatsApp Image 2026-04-20 at 17.35.08 (2).json",
    "CAFEGURUP": ".ocr_cache/WhatsApp Image 2026-04-20 at 17.35.09.json",
    "BİM e-Fatura": ".ocr_cache/WhatsApp Image 2026-04-20 at 17.35.10.json",
}

print("\n" + "=" * 90)
print("YENİ FİŞLER TEST RAPORUNESİ")
print("=" * 90)

results = []
for name, file_path in test_files.items():
    if not Path(file_path).exists():
        print(f"\n❌ {name}: DOSYA BULUNAMADI")
        continue

    print(f"\n📄 {name}")
    print("-" * 90)

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            ocr_json = json.load(f)

        receipt = parse_receipt(ocr_json)

        if receipt is None:
            print("  ❌ PARSER NULL DÖNDÜ")
            results.append({"name": name, "status": "FAILED"})
        else:
            print(f"  ✅ Market: {receipt.store}")
            print(f"     Tarih:  {receipt.date}")
            print(f"     Toplam: {receipt.total:.2f} TL" if receipt.total else "     Toplam: BULUNAMADI")
            print(f"     Ürün:   {len(receipt.items)} adet")
            if receipt.items:
                for i, item in enumerate(receipt.items[:3]):
                    print(f"       {i+1}. {item.name[:40]} = {item.amount:.2f}")
                if len(receipt.items) > 3:
                    print(f"       ... ({len(receipt.items) - 3} daha)")
            results.append({"name": name, "status": "OK", "store": receipt.store, "items": len(receipt.items)})

    except Exception as e:
        print(f"  ❌ HATA: {str(e)[:100]}")
        results.append({"name": name, "status": "ERROR", "error": str(e)[:50]})

print("\n" + "=" * 90)
print("ÖZET")
print("=" * 90)
ok = sum(1 for r in results if r["status"] == "OK")
failed = sum(1 for r in results if r["status"] == "FAILED")
error = sum(1 for r in results if r["status"] == "ERROR")

print(f"✅ Başarılı:    {ok}/{len(results)}")
print(f"⚠️  Başarısız:  {failed}/{len(results)}")
print(f"❌ Hata:       {error}/{len(results)}")

if ok == len(results):
    print("\n🎉 TÜM TESTLER GEÇTİ!")
else:
    print("\n⚠️  DÜZELTME GEREKEN:")
    for r in results:
        if r["status"] != "OK":
            print(f"  - {r['name']}: {r.get('status')}")
