"""
PPOCRLabel Label.txt → .ocr_cache converter

Manuel düzeltilmiş ppOCRLabel verilerini parser'ın beklediği
.ocr_cache formatına çevirir. confidence=1.0 (manuel = %100 güven).

Varolan .ocr_cache dosyalarına dokunmaz. Üzerine yazmak istiyorsan
önce ilgili .ocr_cache/*.json dosyasını sil.

Kullanım:
    python import_labels.py [Label.txt yolu] [çıktı klasörü]
    python import_labels.py --all-caches      # paddleocr + trocr cache birlikte

Varsayılanlar:
    Label.txt → PPOCRLabel_Data/Receipts/Label.txt
    çıktı    → .ocr_cache/

--all-caches: hem .ocr_cache/ hem .ocr_cache_trocr/ klasörlerine yazar.
Günlük iş akışında etiketli fişlerin her iki engine için de ground truth
olarak kullanılmasını sağlar.
"""

import json
import sys
from pathlib import Path

from config import PPOCR_DATA_DIR


def convert(label_txt_path: Path, output_dir: Path, base_dir: Path | None = None):
    if base_dir is None:
        base_dir = label_txt_path.parent.parent

    output_dir.mkdir(exist_ok=True)

    imported = skipped = 0

    with open(label_txt_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            tab_idx = line.index("\t")
            img_rel_path = line[:tab_idx]
            annotations = json.loads(line[tab_idx + 1:])

            stem = Path(img_rel_path).stem
            out_path = output_dir / f"{stem}.json"

            if out_path.exists():
                print(f"  = {out_path.name}  (var, atlandı)")
                skipped += 1
                continue

            detections = []
            for ann in annotations:
                detections.append([ann["points"], [ann["transcription"], 1.0]])

            result = {
                "status": "success",
                "image_width": 0,
                "image_height": 0,
                "detections": detections,
            }

            with open(out_path, "w", encoding="utf-8") as f_out:
                json.dump(result, f_out, ensure_ascii=False, indent=2)

            print(f"  + {out_path.name}  ({len(detections)} detection)")
            imported += 1

    print(f"\n{imported} import edildi, {skipped} atlandı → {output_dir}/")


if __name__ == "__main__":
    # --all-caches: hem paddleocr hem trocr cache'ine yaz
    if "--all-caches" in sys.argv:
        label_txt = PPOCR_DATA_DIR / "Label.txt"
        print("=== paddleocr cache (.ocr_cache/) ===")
        convert(label_txt, Path(".ocr_cache"))
        print("\n=== trocr cache (.ocr_cache_trocr/) ===")
        convert(label_txt, Path(".ocr_cache_trocr"))
    else:
        label_txt = Path(sys.argv[1]) if len(sys.argv) > 1 else PPOCR_DATA_DIR / "Label.txt"
        output    = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".ocr_cache")
        convert(label_txt, output)
