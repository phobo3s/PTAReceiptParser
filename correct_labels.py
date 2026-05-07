"""
PPOCRLabel Label.txt OCR düzeltici — Claude Haiku Vision

Her crop görüntüsünü Haiku'ya gönderir, transcription'ları düzeltir.
Orijinal Label.txt'i korumak için Label_corrected.txt yazar.

Kullanım:
    python correct_labels.py [Label.txt yolu]

Varsayılan:
    PPOCRLabel_Data/Receipts/Label.txt
"""

import json
import sys
import base64
import time
from pathlib import Path

import anthropic

PROMPT = (
    "Bu bir Türkçe fişten alınmış küçük bir metin kesimidir. "
    "Görüntüdeki metni olduğu gibi oku. "
    "Sadece metni yaz, açıklama ekleme, tırnak kullanma."
)


def encode_image(path: Path) -> str:
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def ask_haiku(client: anthropic.Anthropic, crop_path: Path) -> str:
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": encode_image(crop_path),
                    },
                },
                {"type": "text", "text": PROMPT},
            ],
        }],
    )
    return msg.content[0].text.strip()


def correct_label_txt(label_txt_path: Path):
    crop_dir = label_txt_path.parent / "crop_img"
    out_path  = label_txt_path.parent / "Label_corrected.txt"

    client = anthropic.Anthropic()

    lines = label_txt_path.read_text(encoding="utf-8").splitlines()
    corrected_lines = []

    for line in lines:
        if not line.strip():
            corrected_lines.append(line)
            continue

        tab_idx     = line.index("\t")
        img_rel     = line[:tab_idx]
        annotations = json.loads(line[tab_idx + 1:])
        stem        = Path(img_rel).stem  # "Belge 3_3"

        print(f"\n{'='*60}")
        print(f"Fiş: {stem}  ({len(annotations)} detection)")
        print(f"{'='*60}")

        changed = 0
        for i, ann in enumerate(annotations):
            crop_path = crop_dir / f"{stem}_crop_{i}.jpg"
            if not crop_path.exists():
                continue

            original = ann["transcription"]
            corrected = ask_haiku(client, crop_path)

            if corrected != original:
                print(f"  [{i:>2}] '{original}'")
                print(f"       → '{corrected}'")
                ann["transcription"] = corrected
                changed += 1
            else:
                print(f"  [{i:>2}] '{original}'  ✓")

            time.sleep(0.1)  # rate limit önlemi

        print(f"\n  → {changed} düzeltme yapıldı")
        corrected_lines.append(img_rel + "\t" + json.dumps(annotations, ensure_ascii=False))

    out_path.write_text("\n".join(corrected_lines) + "\n", encoding="utf-8")
    print(f"\nKaydedildi: {out_path}")


if __name__ == "__main__":
    from config import PPOCR_DATA_DIR
    label_txt = Path(sys.argv[1]) if len(sys.argv) > 1 else PPOCR_DATA_DIR / "Label.txt"
    correct_label_txt(label_txt)
