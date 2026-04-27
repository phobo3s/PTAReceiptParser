"""
build_corrections.py — PPOCRLabel Cache.cach vs Label.txt karşılaştırarak
corrections.toml'a yeni düzeltme çiftleri ekler (additive).

Kullanım:
    py build_corrections.py
    py build_corrections.py [Cache.cach] [Label.txt] [corrections.toml]

Varsayılanlar:
    Cache.cach       ->PPOCRLabel_Data/Receipts/Cache.cach
    Label.txt        ->PPOCRLabel_Data/Receipts/Label.txt
    corrections.toml ->corrections.toml  (proje kökü)

Çalışma mantığı:
    - Her görsel için Cache.cach ve Label.txt'deki detection'ları index bazlı eşleştirir
    - Metin farklıysa ->yeni çift olarak corrections.toml'a ekler
    - Zaten var olan çiftleri tekrar eklemez
    - Elle eklenen girişlere dokunmaz
"""

import json
import sys
import tomllib
from pathlib import Path


DEFAULT_CACHE   = Path("PPOCRLabel_Data/Receipts/Cache.cach")
DEFAULT_LABELS  = Path("PPOCRLabel_Data/Receipts/Label.txt")
DEFAULT_CORRECTIONS = Path("corrections.toml")


def _load_ppocr_file(path: Path) -> dict[str, list[dict]]:
    """Cache.cach veya Label.txt ->{img_path: [annotation, ...]} dict döndür."""
    result = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            tab_idx = line.index("\t")
            img_path = line[:tab_idx]
            annotations = json.loads(line[tab_idx + 1:])
            result[img_path] = annotations
    return result


def _load_existing_corrections(path: Path) -> set[tuple[str, str]]:
    """Mevcut corrections.toml'daki (wrong, right) çiftlerini set olarak döndür."""
    if not path.exists():
        return set()
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return {
        (c["wrong"], c["right"])
        for c in data.get("correction", [])
    }


def _toml_escape(s: str) -> str:
    r"""TOML basic string icin " ve \ escape et."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build(
    cache_path: Path = DEFAULT_CACHE,
    label_path: Path = DEFAULT_LABELS,
    corrections_path: Path = DEFAULT_CORRECTIONS,
) -> None:
    print(f"Cache  : {cache_path}")
    print(f"Labels : {label_path}")
    print(f"Output : {corrections_path}\n")

    cache_data  = _load_ppocr_file(cache_path)
    label_data  = _load_ppocr_file(label_path)
    existing    = _load_existing_corrections(corrections_path)

    new_entries: list[tuple[str, str]] = []

    for img_key in label_data:
        label_anns = label_data[img_key]
        cache_anns = cache_data.get(img_key)

        if cache_anns is None:
            print(f"  UYARI: Cache'de yok, atlandı: {img_key}")
            continue

        if len(label_anns) != len(cache_anns):
            print(f"  UYARI: Detection sayısı farklı ({len(cache_anns)} vs {len(label_anns)}), atlandı: {img_key}")
            continue

        for idx, (cache_ann, label_ann) in enumerate(zip(cache_anns, label_anns)):
            wrong = cache_ann["transcription"].strip()
            right = label_ann["transcription"].strip()

            if wrong == right:
                continue  # aynı, düzeltme yok

            pair = (wrong, right)
            if pair in existing:
                continue  # zaten var

            new_entries.append(pair)
            existing.add(pair)  # aynı çalışmada tekrar ekleme

    if not new_entries:
        print("OK Yeni düzeltme bulunamadı, corrections.toml değişmedi.")
        return

    # corrections.toml'un başına header yaz (yoksa)
    if not corrections_path.exists():
        corrections_path.write_text(
            "# corrections.toml — OCR düzeltme sözlüğü\n"
            "# build_corrections.py tarafından otomatik oluşturulur/güncellenir.\n"
            "# Elle de düzenlenebilir.\n"
            "#\n"
            "# wrong  = OCR'ın ürettiği hatalı metin\n"
            "# right  = doğru metin\n",
            encoding="utf-8",
        )

    with open(corrections_path, "a", encoding="utf-8") as f:
        for wrong, right in new_entries:
            f.write(
                f'\n[[correction]]\n'
                f'wrong = "{_toml_escape(wrong)}"\n'
                f'right = "{_toml_escape(right)}"\n'
            )

    print(f"OK {len(new_entries)} yeni düzeltme eklendi ->{corrections_path}")
    for wrong, right in new_entries:
        print(f'  "{wrong}"  -> "{right}"')


if __name__ == "__main__":
    cache  = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CACHE
    labels = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_LABELS
    corr   = Path(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_CORRECTIONS
    build(cache, labels, corr)
