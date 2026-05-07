"""
OCR Engine Adapter
==================
PaddleOCR, Tesseract ve Windows OCR için ortak interface.

Tüm motorlar aynı JSON formatını döndürür:
{
    "status": "success",
    "engine": "paddle|tesseract|windows",
    "image_width": int,
    "image_height": int,
    "detections": [
        [
            [[x1,y1],[x2,y2],[x3,y3],[x4,y4]],  # bbox: TL, TR, BR, BL
            ["metin", confidence_float]
        ],
        ...
    ]
}

Kurulum:
    Tesseract : winget install UB-Mannheim.TesseractOCR  (Türkçe dil paketi seç)
                pip install pytesseract
    Windows   : pip install winocr   (Windows 10/11, kurulum gerektirmez)
    Paddle    : pip install paddleocr (mevcut)

Kullanım:
    from ocr_engine import run_ocr, load_engine
    engine = load_engine("tesseract")
    result = run_ocr(engine, Path("fis.jpg"), cache_dir=_OCR_CACHE_DIR,
                     guided_dir=_GUIDED_DIR)
"""

import json
import platform
from pathlib import Path
from typing import Optional

from config import GUIDED_RECEIPTS_DIR as _GUIDED_DIR, OCR_CACHE_DIR as _OCR_CACHE_DIR

# ── Renk kodları (guided receipt overlay) ─────────────────────────────────────
# Her motor farklı renk → aynı fiş üzerinde karşılaştırma yapılabilir

ENGINE_COLORS = {
    "paddle":    (52,  152, 219),   # mavi
    "tesseract": (46,  204, 113),   # yeşil
    "windows":   (230, 126, 34),    # turuncu
}

def _conf_color(conf: float) -> tuple[int, int, int]:
    """Confidence seviyesine göre renk: yeşil → sarı → kırmızı."""
    if conf >= 0.80:
        return (0, 200, 0)
    elif conf >= 0.60:
        return (200, 180, 0)
    else:
        return (200, 0, 0)


def _bbox_to_quad(left: float, top: float, w: float, h: float) -> list[list[float]]:
    """Rect (left, top, w, h) → 4 köşe [TL, TR, BR, BL]."""
    return [
        [left,     top],
        [left + w, top],
        [left + w, top + h],
        [left,     top + h],
    ]


# ── Guided receipt overlay ─────────────────────────────────────────────────────

def save_guided_receipt(
    image_path: Path,
    detections: list,
    engine: str,
    guided_dir: Path,
) -> _GUIDED_DIR:
    """
    Her detection'ın bbox'ını ve confidence'ını görsel üzerine çiz.
    Confidence rengi: yeşil (≥0.80) / sarı (≥0.60) / kırmızı (<0.60)
    Motor adı sol üstte gösterilir.
    Dosya adı: {stem}_{engine}.jpg
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("  ⚠️  Pillow yüklü değil, guided receipt kaydedilemedi.")
        return

    guided_dir.mkdir(exist_ok=True)
    out_path = guided_dir / f"{image_path.stem}_{engine}.jpg"

    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)

    engine_color = ENGINE_COLORS.get(engine, (200, 200, 200))

    # Motor adı etiketi (sol üst köşe)
    draw.rectangle([0, 0, 160, 24], fill=engine_color)
    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except Exception:
        font = ImageFont.load_default()
    draw.text((4, 4), f"OCR: {engine}", fill=(255, 255, 255), font=font)

    for det in detections:
        bbox, (text, conf) = det[0], det[1]
        color = _conf_color(conf)

        # Bbox: 4 köşe → polygon
        pts = [(int(p[0]), int(p[1])) for p in bbox]
        draw.polygon(pts, outline=color)

        # Kısa metin etiketi (sol üst köşe)
        x0, y0 = pts[0]
        label = f"{conf:.2f}"
        draw.text((x0, max(0, y0 - 14)), label, fill=color, font=font)

    img.save(str(out_path), quality=90)


# ── Ortak cache wrapper ────────────────────────────────────────────────────────

def ocr_with_cache(
    engine_obj,
    image_path: Path,
    engine_name: str,
    cache_dir: Path,
    guided_dir: Optional[Path] = _GUIDED_DIR,
) -> dict:
    """
    Cache'te varsa OCR'ı tekrar yapmaz.
    Cache dosya adı: {stem}_{engine}.json
    """
    cache_dir.mkdir(exist_ok=True)
    cache_file = cache_dir / f"{image_path.stem}_{engine_name}.json"

    if cache_file.exists():
        print(f"  📂 Cache [{engine_name}]: {cache_file.name}")
        return json.loads(cache_file.read_text(encoding="utf-8"))

    print(f"  🔍 OCR yapılıyor [{engine_name}]: {image_path.name}")
    result = _run_engine(engine_obj, engine_name, image_path)

    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if guided_dir is not _GUIDED_DIR:
        save_guided_receipt(image_path, result["detections"], engine_name, guided_dir)

    return result


def _run_engine(engine_obj, engine_name: str, image_path: Path) -> dict:
    if engine_name == "paddle":
        return _run_paddle(engine_obj, image_path)
    elif engine_name == "tesseract":
        return _run_tesseract(image_path)
    elif engine_name == "windows":
        return _run_windows(image_path)
    else:
        raise ValueError(f"Bilinmeyen OCR motoru: {engine_name}")


# ── PaddleOCR ─────────────────────────────────────────────────────────────────

def load_paddle():
    import os
    import platform
    os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
    from paddleocr import PaddleOCR
    os.environ["FLAGS_use_mkldnn"] = "0"
    print("⏳ PaddleOCR yükleniyor...")
    ocr = PaddleOCR(
        use_textline_orientation=True,
        device='cpu',
        lang='tr',
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name="PP-OCRv5_mobile_rec",
        enable_mkldnn=(platform.system() == "Linux"),
        text_det_unclip_ratio=1.6,
        text_det_box_thresh=0.5,
        text_det_thresh=0.3,
        use_doc_unwarping=True,
    )
    print("✓ PaddleOCR hazır\n")
    return ocr


def _run_paddle(ocr, image_path: Path) -> dict:
    import numpy as np
    from PIL import Image

    img = Image.open(image_path).convert("RGB")
    img_array = np.array(img)
    h, w = img_array.shape[:2]

    result = list(ocr.predict(str(image_path)))

    detections = []
    for ocr_result in result:
        boxes  = ocr_result.get("dt_polys") or ocr_result.get("boxes")
        texts  = ocr_result.get("rec_texts") or ocr_result.get("texts")
        scores = ocr_result.get("rec_scores") or ocr_result.get("scores")
        if boxes is _GUIDED_DIR or texts is _GUIDED_DIR or scores is _GUIDED_DIR:
            continue
        for bbox, text, conf in zip(boxes, texts, scores):
            if hasattr(bbox, "tolist"):
                bbox = bbox.tolist()
            detections.append([bbox, [text, float(conf)]])

    return {"status": "success", "engine": "paddle",
            "image_width": w, "image_height": h, "detections": detections}


# ── Tesseract ─────────────────────────────────────────────────────────────────

def load_tesseract():
    """
    Tesseract'ı kontrol et ve yükle.
    Kurulum: winget install UB-Mannheim.TesseractOCR
             pip install pytesseract
    Türkçe: kurulum sırasında 'tur' dil paketi seçilmeli.
    """
    try:
        import pytesseract
        # Windows default install path
        if platform.system() == "Windows":
            from pathlib import Path as _Path
            default = _Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
            if default.exists():
                pytesseract.pytesseract.tesseract_cmd = str(default)
        # Bağlantı testi
        pytesseract.get_tesseract_version()
        print("✓ Tesseract hazır\n")
        return pytesseract
    except Exception as e:
        raise RuntimeError(
            f"Tesseract bulunamadı: {e}\n"
            "Kurulum: winget install UB-Mannheim.TesseractOCR\n"
            "         pip install pytesseract"
        )


def _run_tesseract(image_path: Path) -> dict:
    import pytesseract
    from pytesseract import Output
    from PIL import Image

    img = Image.open(image_path).convert("RGB")
    w, h = img.size

    # --psm 6: tek tip metin bloğu varsay (fiş için uygun)
    # tur+eng: Türkçe + İngilizce (sayılar için)
    config = "--psm 6 -c preserve_interword_spaces=1"
    data = pytesseract.image_to_data(
        img, lang="tur+eng", output_type=Output.DICT, config=config
    )

    detections = []
    n = len(data["text"])
    for i in range(n):
        text = data["text"][i].strip()
        conf = int(data["conf"][i])

        if not text or conf < 0:   # boş veya Tesseract'ın "belirsiz" (-1) satırları
            continue

        left   = data["left"][i]
        top    = data["top"][i]
        width  = data["width"][i]
        height = data["height"][i]

        if width <= 0 or height <= 0:
            continue

        bbox = _bbox_to_quad(left, top, width, height)
        conf_norm = conf / 100.0

        detections.append([bbox, [text, conf_norm]])

    return {"status": "success", "engine": "tesseract",
            "image_width": w, "image_height": h, "detections": detections}


# ── Windows OCR ───────────────────────────────────────────────────────────────

def load_windows():
    """
    Windows.Media.OCR kullanır — kurulum gerektirmez.
    pip install winocr
    Türkçe: Windows'ta Türkçe dil paketi kurulu olmalı.
    (Ayarlar → Zaman ve Dil → Dil → Türkçe ekle)
    """
    if platform.system() != "Windows":
        raise RuntimeError("Windows OCR sadece Windows'ta çalışır.")
    try:
        import winocr
        print("✓ Windows OCR hazır\n")
        return winocr
    except ImportError:
        raise RuntimeError(
            "winocr paketi bulunamadı.\n"
            "Kurulum: pip install winocr"
        )


async def _run_ocr_async_helper(img_np, lang):
    import winocr
    # The 'await' keyword here knows how to handle the IAsyncOperation
    return await winocr.recognize_cv2(img_np, lang)

def _run_windows(image_path: Path) -> dict:
    import asyncio
    import winocr
    import numpy as np
    from PIL import Image

    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    img_np = np.array(img)

    # winocr async → sync wrapper
    result = asyncio.run(_run_ocr_async_helper(img_np, "tr"))

    detections = []
    for line in result.lines:
        for word in line.words:
            text = word.text.strip()
            if not text:
                continue
            br = word.bounding_rect
            left   = float(br.x)
            top    = float(br.y)
            width  = float(br.width)
            height = float(br.height)
            if width <= 0 or height <= 0:
                continue
            bbox = _bbox_to_quad(left, top, width, height)
            # Windows OCR confidence vermez → 1.0 (güvenilir kabul et)
            detections.append([bbox, [text, 1.0]])

    return {"status": "success", "engine": "windows",
            "image_width": w, "image_height": h, "detections": detections}


# ── Public API ────────────────────────────────────────────────────────────────

def load_engine(name: str):
    """Motor adına göre engine nesnesi döndür."""
    name = name.lower()
    if name == "paddle":
        return load_paddle()
    elif name == "tesseract":
        return load_tesseract()
    elif name == "windows":
        return load_windows()
    else:
        raise ValueError(f"Geçersiz motor: '{name}'. Seçenekler: paddle, tesseract, windows")


def run_ocr(
    engine_obj,
    engine_name: str,
    image_path: Path,
    cache_dir: Path = _OCR_CACHE_DIR,
    guided_dir: Optional[Path] = _GUIDED_DIR,
) -> dict:
    """Ana entry point — cache + guided receipt otomatik."""
    return ocr_with_cache(engine_obj, image_path, engine_name, cache_dir, guided_dir)
