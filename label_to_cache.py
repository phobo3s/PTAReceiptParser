"""
PPOCRLabel Label.txt → .ocr_cache JSON converter

Label.txt'deki manuel düzeltilmiş verileri parser'ın beklediği
.ocr_cache formatına çevirir. confidence=1.0 (manuel = %100 güven).

Kullanım:
    python label_to_cache.py [Label.txt yolu] [çıktı klasörü]

Varsayılanlar:
    Label.txt → PPOCRLabel_Data/Receipts/Label.txt
    çıktı    → .ocr_cache/
"""

import json
import sys
from pathlib import Path


def convert(label_txt_path: Path, output_dir: Path, base_dir: Path | None = None):
    if base_dir is None:
        base_dir = label_txt_path.parent.parent  # PPOCRLabel_Data/Receipts/Label.txt → PPOCRLabel_Data/

    output_dir.mkdir(exist_ok=True)

    converted = 0
    with open(label_txt_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            tab_idx = line.index("\t")
            img_rel_path = line[:tab_idx]
            annotations = json.loads(line[tab_idx + 1:])

            stem = Path(img_rel_path).stem  # "Belge 3_3"

            # Görüntü boyutlarını al
            img_path = base_dir / img_rel_path
            w, h = 0, 0
            try:
                from PIL import Image
                with Image.open(img_path) as img:
                    w, h = img.size
            except Exception:
                pass

            detections = []
            for ann in annotations:
                bbox = ann["points"]        # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
                text = ann["transcription"]
                detections.append([bbox, [text, 1.0]])

            result = {
                "status": "success",
                "image_width": w,
                "image_height": h,
                "detections": detections,
            }

            out_path = output_dir / f"{stem}.json"
            with open(out_path, "w", encoding="utf-8") as f_out:
                json.dump(result, f_out, ensure_ascii=False, indent=2)

            print(f"  → {out_path.name}  ({len(detections)} detection, {w}x{h})")
            converted += 1

    print(f"\n{converted} fiş dönüştürüldü → {output_dir}/")


if __name__ == "__main__":
    label_txt = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("PPOCRLabel_Data/Receipts/Label.txt")
    output    = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".ocr_cache")
    convert(label_txt, output)
