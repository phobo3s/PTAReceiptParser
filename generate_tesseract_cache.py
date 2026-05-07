#!/usr/bin/env python3
"""
Mevcut tüm fişler için Tesseract cache dosyaları oluştur.
Görselleri Receipts/ ve .guidedReceipts/ dizinlerinde arar.
Cache: .ocr_cache/{stem}_tesseract.json
"""

from pathlib import Path
from ocr_engine import load_engine, run_ocr
from config import OCR_CACHE_DIR, GUIDED_RECEIPTS_DIR

CACHE_DIR   = OCR_CACHE_DIR
IMAGE_DIRS  = [Path("Receipts"), GUIDED_RECEIPTS_DIR]
IMG_EXTS    = {".jpg", ".jpeg", ".png"}

# Tüm görselleri bul: stem → path
image_map: dict[str, Path] = {}
for d in IMAGE_DIRS:
    if not d.exists():
        continue
    for f in d.iterdir():
        if f.suffix.lower() in IMG_EXTS:
            image_map[f.stem] = f

# Cache'deki paddle dosyalarını bul (suffix içermeyenler = paddle)
paddle_stems = [
    f.stem for f in CACHE_DIR.glob("*.json")
    if "_tesseract" not in f.name and "_windows" not in f.name
]

print(f"Toplam paddle cache: {len(paddle_stems)}")
print(f"Bulunan görsel: {len(image_map)}")

engine = load_engine("tesseract")

done = 0
missing = []
for stem in sorted(paddle_stems):
    tess_cache = CACHE_DIR / f"{stem}_tesseract.json"
    if tess_cache.exists():
        print(f"  ✓ Zaten var: {tess_cache.name}")
        done += 1
        continue

    img_path = image_map.get(stem)
    if img_path is None:
        missing.append(stem)
        print(f"  ✗ Görsel bulunamadı: {stem!r}")
        continue

    run_ocr(engine, "tesseract", img_path, CACHE_DIR, guided_dir=GUIDED_RECEIPTS_DIR)
    done += 1

print(f"\n{'─'*60}")
print(f"Tamamlanan: {done}/{len(paddle_stems)}")
if missing:
    print(f"Görsel bulunamayan ({len(missing)} adet):")
    for s in missing:
        print(f"  - {s!r}")
