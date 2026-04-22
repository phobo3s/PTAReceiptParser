"""
Ön işleme konfigürasyonlarını karşılaştır.

Her konfigürasyon bağımsız bir çıktı dizinine ve OCR cache dizinine yazar,
böylece hangi adımın gerçekten fark yarattığını ölçebilirsiniz.

Kullanım:
    python test_preprocess_compare.py Receipts/
    python test_preprocess_compare.py Receipts/ --engine tesseract
    python test_preprocess_compare.py Receipts/ --skip-ocr    # sadece görüntü oluştur

Test konfigürasyonları:
    raw          — hiç ön işleme yok (ham görüntüler doğrudan OCR'a gider)
    baseline     — standart pipeline (upscale, rotate, perspective, bg-norm, CLAHE, denoise)
    gamma        — baseline + gamma=0.7
    sharpen      — baseline + unsharp masking
    gamma+sharpen— baseline + gamma=0.7 + unsharp masking

Çıktı:
    .preprocess_test/{config}/        ← işlenmiş görüntüler
    .preprocess_test/{config}_ocr/    ← OCR JSON cache
    .preprocess_test/report.txt       ← karşılaştırma tablosu

OCR backend: PaddlePaddle (varsayılan). Tesseract için --engine tesseract.
"""

import json
import shutil
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from preProcess import process_image, SUPPORTED_EXTS

# ── Konfigürasyonlar ───────────────────────────────────────────────────────────

CONFIGS = [
    {"name": "raw",          "use_gamma": 0.0, "use_sharpen": False, "skip_preprocess": True},
    {"name": "baseline",     "use_gamma": 0.0, "use_sharpen": False},
    {"name": "gamma",        "use_gamma": 0.7, "use_sharpen": False},
    {"name": "sharpen",      "use_gamma": 0.0, "use_sharpen": True},
    {"name": "gamma+sharpen","use_gamma": 0.7, "use_sharpen": True},
]

TEST_ROOT = Path(".preprocess_test")


# ── Yardımcı: Laplacian keskinlik skoru ───────────────────────────────────────

def laplacian_sharpness(img: np.ndarray) -> float:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


# ── Yardımcı: OCR çalıştır (PaddleOCR veya Tesseract) ────────────────────────

def run_paddle_ocr(image_path: Path, cache_dir: Path) -> dict | None:
    cache_file = cache_dir / (image_path.stem + ".json")
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    try:
        from paddleocr import PaddleOCR
        ocr = PaddleOCR(use_angle_cls=True, lang="latin", show_log=False)
        result = ocr.ocr(str(image_path), cls=True)
        if result is None or not result:
            data = {"detections": []}
        else:
            detections = []
            for line in result[0] or []:
                box, (text, conf) = line
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                detections.append({
                    "text": text,
                    "confidence": float(conf),
                    "x_min": min(xs), "x_max": max(xs),
                    "y_min": min(ys), "y_max": max(ys),
                })
            data = {"detections": detections}
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data
    except Exception as e:
        print(f"      OCR HATA ({image_path.name}): {e}", file=sys.stderr)
        return None


def run_tesseract_ocr(image_path: Path, cache_dir: Path) -> dict | None:
    cache_file = cache_dir / (image_path.stem + ".json")
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    try:
        import pytesseract
        img = cv2.imread(str(image_path))
        data = pytesseract.image_to_data(img, lang="tur", output_type=pytesseract.Output.DICT)
        detections = []
        for i, text in enumerate(data["text"]):
            text = text.strip()
            if not text:
                continue
            conf = data["conf"][i]
            if conf < 0:
                continue
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            detections.append({
                "text": text,
                "confidence": float(conf) / 100.0,
                "x_min": x, "x_max": x + w,
                "y_min": y, "y_max": y + h,
            })
        result = {"detections": detections}
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    except Exception as e:
        print(f"      OCR HATA ({image_path.name}): {e}", file=sys.stderr)
        return None


def run_ocr(image_path: Path, cache_dir: Path, engine: str) -> dict | None:
    if engine == "tesseract":
        return run_tesseract_ocr(image_path, cache_dir)
    return run_paddle_ocr(image_path, cache_dir)


# ── Yardımcı: OCR sonuçlarından istatistik ────────────────────────────────────

def ocr_stats(data: dict) -> dict:
    dets = data.get("detections", [])
    if not dets:
        return {"n": 0, "avg_conf": 0.0, "low_conf": 0, "pct_low": 0.0}
    confs = [d["confidence"] for d in dets]
    low = sum(1 for c in confs if c < 0.80)
    return {
        "n": len(dets),
        "avg_conf": sum(confs) / len(confs),
        "low_conf": low,
        "pct_low": low / len(confs) * 100,
    }


# ── Ana karşılaştırma ─────────────────────────────────────────────────────────

def run_comparison(receipt_folder: Path, engine: str = "paddle", skip_ocr: bool = False):
    images = sorted([p for p in receipt_folder.iterdir() if p.suffix.lower() in SUPPORTED_EXTS])
    if not images:
        print(f"HATA: {receipt_folder} içinde görüntü bulunamadı")
        sys.exit(1)

    print(f"\n{'='*70}")
    print(f"  ÖN İŞLEME KARŞILAŞTIRMA — {len(images)} görüntü, engine={engine}")
    print(f"{'='*70}")

    # Her konfigürasyon için görüntüleri hazırla
    config_dirs: dict[str, Path] = {}
    for cfg in CONFIGS:
        cfg_name = cfg["name"]
        out_dir = TEST_ROOT / cfg_name
        config_dirs[cfg_name] = out_dir

        print(f"\n[{cfg_name}] görüntüler hazırlanıyor...")
        if cfg.get("skip_preprocess"):
            # raw: orijinalleri kopyala
            out_dir.mkdir(parents=True, exist_ok=True)
            for img_path in images:
                dst = out_dir / img_path.name
                if not dst.exists():
                    shutil.copy2(img_path, dst)
            print(f"  {len(images)} dosya kopyalandı -> {out_dir}")
        else:
            for img_path in images:
                dst = out_dir / img_path.name
                if dst.exists():
                    continue
                process_image(
                    img_path,
                    engine=engine,
                    use_gamma=cfg.get("use_gamma", 0.0),
                    use_sharpen=cfg.get("use_sharpen", False),
                    output_dir=out_dir,
                    debug=False,
                )

    if skip_ocr:
        print("\n--skip-ocr: OCR atlandı. Görüntüler hazır:")
        for cfg in CONFIGS:
            print(f"  {TEST_ROOT / cfg['name']}/")
        return

    # OCR çalıştır ve istatistik topla
    # results[img_stem][cfg_name] = {"sharpness": float, "ocr": stats_dict}
    results: dict[str, dict[str, dict]] = {p.stem: {} for p in images}

    for cfg in CONFIGS:
        cfg_name = cfg["name"]
        out_dir = config_dirs[cfg_name]
        ocr_dir = TEST_ROOT / f"{cfg_name}_ocr"
        print(f"\n[{cfg_name}] OCR çalıştırılıyor...")

        for img_path in images:
            processed = out_dir / img_path.name
            if not processed.exists():
                continue
            img = cv2.imread(str(processed))
            sharp = laplacian_sharpness(img) if img is not None else 0.0

            ocr_data = run_ocr(processed, ocr_dir, engine)
            stats = ocr_stats(ocr_data) if ocr_data else {"n": 0, "avg_conf": 0.0, "low_conf": 0, "pct_low": 0.0}

            results[img_path.stem][cfg_name] = {"sharpness": sharp, "ocr": stats}
            print(f"  {img_path.name}: n={stats['n']}  avg={stats['avg_conf']:.3f}  low={stats['pct_low']:.1f}%  sharp={sharp:.0f}")

    # ── Karşılaştırma tablosu ─────────────────────────────────────────────────
    cfg_names = [c["name"] for c in CONFIGS]
    col_w = 14

    header_parts = [f"{'Görüntü':<30}"]
    for cn in cfg_names:
        header_parts.append(f"{cn:^{col_w}}")
    header = " | ".join(header_parts)

    subhdr_parts = [" " * 30]
    for _ in cfg_names:
        subhdr_parts.append(f"{'avg conf':^{col_w}}")
    subhdr = " | ".join(subhdr_parts)

    sep = "-" * len(header)

    lines = [
        "",
        "=" * len(header),
        "KARŞILAŞTIRMA TABLOSU — Ortalama OCR Güveni (avg_conf)",
        "=" * len(header),
        header,
        subhdr,
        sep,
    ]

    totals: dict[str, list[float]] = {cn: [] for cn in cfg_names}
    totals_sharp: dict[str, list[float]] = {cn: [] for cn in cfg_names}

    for stem, cfg_data in sorted(results.items()):
        row_parts = [f"{stem[:30]:<30}"]
        for cn in cfg_names:
            if cn in cfg_data:
                v = cfg_data[cn]["ocr"]["avg_conf"]
                totals[cn].append(v)
                totals_sharp[cn].append(cfg_data[cn]["sharpness"])
                row_parts.append(f"{v:^{col_w}.3f}")
            else:
                row_parts.append(f"{'—':^{col_w}}")
        lines.append(" | ".join(row_parts))

    lines.append(sep)

    # Ortalama satırı
    avg_parts = [f"{'ORTALAMA':<30}"]
    for cn in cfg_names:
        if totals[cn]:
            avg_parts.append(f"{sum(totals[cn])/len(totals[cn]):^{col_w}.3f}")
        else:
            avg_parts.append(f"{'—':^{col_w}}")
    lines.append(" | ".join(avg_parts))

    # Keskinlik satırı
    sharp_parts = [f"{'  (keskinlik)':{'<'}30}"]
    for cn in cfg_names:
        if totals_sharp[cn]:
            sharp_parts.append(f"{sum(totals_sharp[cn])/len(totals_sharp[cn]):^{col_w}.0f}")
        else:
            sharp_parts.append(f"{'—':^{col_w}}")
    lines.append(" | ".join(sharp_parts))

    lines.append("=" * len(header))
    lines.append("")
    lines.append("Düşük güven (<0.80) yüzdesi:")
    lines.append(sep)
    pct_parts = [f"{'ORTALAMA %low':{'<'}30}"]
    totals_low: dict[str, list[float]] = {cn: [] for cn in cfg_names}
    for stem, cfg_data in results.items():
        for cn in cfg_names:
            if cn in cfg_data:
                totals_low[cn].append(cfg_data[cn]["ocr"]["pct_low"])
    for cn in cfg_names:
        if totals_low[cn]:
            pct_parts.append(f"{sum(totals_low[cn])/len(totals_low[cn]):^{col_w}.1f}%")
        else:
            pct_parts.append(f"{'—':^{col_w}}")
    lines.append(" | ".join(pct_parts))
    lines.append("=" * len(header))
    lines.append("")

    report = "\n".join(lines)
    print(report)

    report_path = TEST_ROOT / "report.txt"
    report_path.write_text(report, encoding="utf-8")
    print(f"Rapor kaydedildi: {report_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="Ham fiş görüntülerinin bulunduğu klasör")
    ap.add_argument("--engine", choices=["paddle", "tesseract"], default="paddle")
    ap.add_argument("--skip-ocr", action="store_true",
                    help="Sadece görüntüleri işle, OCR çalıştırma")
    args = ap.parse_args()

    run_comparison(Path(args.folder), engine=args.engine, skip_ocr=args.skip_ocr)
