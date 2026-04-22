"""
Fiş Görüntü Ön İşleyici
========================
Bir klasördeki tüm jpg/png fişleri işler ve .processedReceipts/ klasörüne kaydeder.

Kullanım:
    python preProcess.py Receipts/
    python preProcess.py Receipts/ --engine tesseract
    python preProcess.py Receipts/ --sharpen          ← unsharp masking ekle
    python preProcess.py Receipts/ --gamma 0.7        ← gamma düzeltme ekle
    python preProcess.py Receipts/ --sharpen --gamma 0.7

Adımlar (sırayla):
    0. Upscale          — dar görüntüleri (< MIN_WIDTH) büyüt
    1. Dönme düzeltme   — Hough çizgileriyle eğim tespiti
    2. Perspektif       — 4 köşe tespiti + warpPerspective
    3. Bg normalizasyon — gölge/eşitsiz ışık giderme (büyük Gaussian bölme)
    4. Gamma (opsiyonel)— midton parlaklık düzeltme (--gamma 0.7 ile aktif)
    5. CLAHE kontrast   — adaptif histogram eşitleme (parlaklığa göre ayarlı)
    6. Denoise          — bilateral filtre ile ince gürültü bastırma
    7. Sharpen (opsiy.) — unsharp masking ile kenar keskinleştirme (--sharpen ile)
    8. Binary (opsiyonel)— Tesseract için; PaddleOCR için atlanır
    9. Crop             — fiş dışı boşlukları kırp

Bağımlılıklar:
    pip install opencv-python numpy
"""

import cv2
import numpy as np
from pathlib import Path
import sys
import traceback

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png"}
OUTPUT_DIR     = Path(".processedReceipts")
DEBUG_DIR      = OUTPUT_DIR / "debug"

MIN_WIDTH = 800   # px — dar görüntüleri bu genişliğe büyüt


# ═══════════════════════════════════════════════════════════════════════════════
# ADIM 0 — MİNİMUM GENİŞLİK
# ═══════════════════════════════════════════════════════════════════════════════

def enforce_min_width(img: np.ndarray, min_width: int = MIN_WIDTH) -> np.ndarray:
    """
    Görüntü MIN_WIDTH'ten dardsa orantılı olarak büyüt.
    PaddleOCR dar görüntülerde karakter sınırlarını yanlış tespit ediyor.
    """
    h, w = img.shape[:2]
    if w >= min_width:
        return img
    scale = min_width / w
    new_w = min_width
    new_h = int(h * scale)
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)


# ═══════════════════════════════════════════════════════════════════════════════
# ADIM 1 — DÖNME DÜZELTMESİ
# ═══════════════════════════════════════════════════════════════════════════════

def correct_rotation(img: np.ndarray) -> tuple[np.ndarray, float]:
    """
    Hough line detection ile fişin eğimini tespit eder ve düzeltir.
    Fişlerin baskın yatay çizgilerini (metin satırları) kullanır.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)

    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=100,
        minLineLength=img.shape[1] * 0.3,
        maxLineGap=20
    )

    if lines is None or len(lines) == 0:
        return img, 0.0

    angles = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if x2 - x1 == 0:
            continue
        angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if -30 < angle < 30:
            angles.append(angle)

    if not angles:
        return img, 0.0

    median_angle = np.median(angles)
    if abs(median_angle) < 0.3:
        return img, 0.0

    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, median_angle, 1.0)

    cos = abs(M[0, 0])
    sin = abs(M[0, 1])
    new_w = int(h * sin + w * cos)
    new_h = int(h * cos + w * sin)
    M[0, 2] += (new_w - w) / 2
    M[1, 2] += (new_h - h) / 2

    rotated = cv2.warpAffine(
        img, M, (new_w, new_h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE
    )
    return rotated, median_angle


# ═══════════════════════════════════════════════════════════════════════════════
# ADIM 2 — PERSPEKTİF DÜZELTMESİ
# ═══════════════════════════════════════════════════════════════════════════════

def correct_perspective(img: np.ndarray) -> np.ndarray:
    """
    Fişin 4 köşesini tespit ederek perspektif bozulmasını düzeltir.
    Köşe tespiti başarısız olursa orijinal görüntüyü döndürür.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=3)

    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return img

    largest = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(largest)
    img_area = img.shape[0] * img.shape[1]
    if area < img_area * 0.20:
        return img

    peri = cv2.arcLength(largest, True)
    approx = cv2.approxPolyDP(largest, 0.02 * peri, True)

    if len(approx) != 4:
        x, y, w, h = cv2.boundingRect(largest)
        if w > img.shape[1] * 0.85 and h > img.shape[0] * 0.85:
            return img
        approx = np.array([
            [[x, y]], [[x + w, y]], [[x + w, y + h]], [[x, y + h]]
        ], dtype=np.float32)

    pts = approx.reshape(4, 2).astype(np.float32)
    rect = order_points(pts)
    tl, tr, br, bl = rect

    max_width  = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    max_height = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))

    dst = np.array([
        [0, 0], [max_width - 1, 0],
        [max_width - 1, max_height - 1], [0, max_height - 1]
    ], dtype=np.float32)

    M = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(img, M, (max_width, max_height))


def order_points(pts: np.ndarray) -> np.ndarray:
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


# ═══════════════════════════════════════════════════════════════════════════════
# ADIM 3 — ARKA PLAN NORMALİZASYONU (YENİ)
# ═══════════════════════════════════════════════════════════════════════════════

def normalize_background(img: np.ndarray) -> np.ndarray:
    """
    Gölge ve eşitsiz aydınlatmayı giderir.

    Yöntem: büyük bir Gaussian bulanıklaştırma ile arka plan tahmin edilir,
    sonra orijinal görüntü bu arka plana bölünür. Telefon flaşının yarattığı
    parlak merkez / karanlık kenar etkisini ortadan kaldırır.

    Sadece düşük parlaklıklı veya yüksek yerel varyansa sahip görüntülere
    uygulanır; zaten temiz tarayıcı görüntülerine dokunulmaz.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    brightness = gray.mean()

    # Parlak ve düzgün görüntüler için atla (tarayıcı çıktısı gibi)
    if brightness > 200:
        return img

    # Büyük kernel ile arka plan tahmini
    bg = cv2.GaussianBlur(gray, (55, 55), 0)
    # Bölme: arka plan etkisini kaldır, ölçekle [0,255]'e geri çek
    normalized = cv2.divide(gray.astype(np.float32), bg.astype(np.float32))
    normalized = np.clip(normalized * 200, 0, 255).astype(np.uint8)

    return cv2.cvtColor(normalized, cv2.COLOR_GRAY2BGR)


# ═══════════════════════════════════════════════════════════════════════════════
# ADIM 4 — KONTRAST NORMALİZASYON (CLAHE)
# ═══════════════════════════════════════════════════════════════════════════════

def enhance_contrast(img: np.ndarray) -> np.ndarray:
    """
    CLAHE ile yerel kontrast artırımı.
    Parlaklığa göre clipLimit otomatik ayarlanır:
    - Koyu görüntü (brightness < 150) → agresif (clipLimit=3.0)
    - Orta (150-200)                  → orta  (clipLimit=2.0)
    - Parlak (> 200)                  → hafif (clipLimit=1.5)
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    brightness = gray.mean()

    if brightness < 150:
        clip = 3.0
    elif brightness < 200:
        clip = 2.0
    else:
        clip = 1.5

    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8))
    l_enhanced = clahe.apply(l)
    enhanced = cv2.merge([l_enhanced, a, b])
    return cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)


# ═══════════════════════════════════════════════════════════════════════════════
# ADIM 4 — GAMMA DÜZELTMESİ (OPSİYONEL, YENİ)
# ═══════════════════════════════════════════════════════════════════════════════

def gamma_correct(img: np.ndarray, gamma: float = 0.7) -> np.ndarray:
    """
    Güç yasası dönüşümü ile midton parlaklığını artırır.
    gamma < 1.0 → karanlık pikseller aydınlanır (önerilen: 0.6–0.8)
    gamma > 1.0 → görüntü karartılır (genelde kullanılmaz)

    CLAHE'den önce uygulanır: gamma genel seviyeyi kaldırır,
    CLAHE ardından yerel kontrast detayını ince ayarlar.
    Karanlık telefon fotoğraflarında (brightness < 150) en etkili.
    """
    table = np.array(
        [(i / 255.0) ** gamma * 255 for i in range(256)], dtype=np.uint8
    )
    return cv2.LUT(img, table)


# ═══════════════════════════════════════════════════════════════════════════════
# ADIM 6 — DENOISE
# ═══════════════════════════════════════════════════════════════════════════════

def denoise(img: np.ndarray) -> np.ndarray:
    """
    Bilateral filtre ile gürültü bastırma.
    Kenarları korurken düz alanlardaki piksel gürültüsünü giderir.
    Çok net tarayıcı görüntülerinde etkisiz ama zararı da yok.
    """
    return cv2.bilateralFilter(img, d=5, sigmaColor=30, sigmaSpace=30)


# ═══════════════════════════════════════════════════════════════════════════════
# ADIM 7 — UNSHARP MASKING (OPSİYONEL, YENİ)
# ═══════════════════════════════════════════════════════════════════════════════

def sharpen(img: np.ndarray, strength: float = 1.5) -> np.ndarray:
    """
    Unsharp masking: bulanık kopyayı orijinalden çıkararak kenarları vurgular.

    Formül: output = img * strength - blur * (strength - 1)
    strength=1.5 → %50 kenar güçlendirme (önerilen başlangıç)
    strength=2.0 → %100, agresif; gürültüyü de yükseltir

    Denoise sonrası uygulanır: önce gürültü bastırılır, sonra kenarlar açılır.
    Hem karanlık hem parlak görüntülerde işe yarar.
    """
    blurred = cv2.GaussianBlur(img, (0, 0), sigmaX=2)
    sharpened = cv2.addWeighted(img, strength, blurred, -(strength - 1), 0)
    return np.clip(sharpened, 0, 255).astype(np.uint8)


# ═══════════════════════════════════════════════════════════════════════════════
# ADIM 8 — ADAPTİF BINARY (SADECE TESSERACT İÇİN)
# ═══════════════════════════════════════════════════════════════════════════════

def to_binary(img: np.ndarray) -> np.ndarray:
    """
    Adaptif threshold ile binary'e çevir + küçük gürültü noktalarını temizle.

    UYARI: PaddleOCR için bu adımı ATLAYINIZ.
    PaddleOCR derin öğrenme tabanlı; gradient bilgisini kullanır.
    Binary dönüşüm bu bilgiyi yok eder ve accuracy'yi düşürür.
    Tesseract gibi geleneksel OCR motorları için uygundur.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)

    binary = cv2.adaptiveThreshold(
        blurred, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=15,
        C=8
    )

    # Küçük gürültü noktalarını sil (1-2 piksel izole nokta)
    kernel = np.ones((2, 2), np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)


# ═══════════════════════════════════════════════════════════════════════════════
# ADIM 7 — CROP
# ═══════════════════════════════════════════════════════════════════════════════

def crop_receipt(img: np.ndarray, padding: int = 10) -> np.ndarray:
    """Fiş içeriğinin etrafındaki boş alanları kırp."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    inverted = cv2.bitwise_not(gray)

    row_sums = inverted.sum(axis=1)
    col_sums = inverted.sum(axis=0)

    threshold = 5 * 255
    rows_with_content = np.where(row_sums > threshold)[0]
    cols_with_content = np.where(col_sums > threshold)[0]

    if len(rows_with_content) == 0 or len(cols_with_content) == 0:
        return img

    top    = max(0, rows_with_content[0]  - padding)
    bottom = min(img.shape[0], rows_with_content[-1]  + padding + 1)
    left   = max(0, cols_with_content[0] - padding)
    right  = min(img.shape[1], cols_with_content[-1] + padding + 1)

    cropped_area = (bottom - top) * (right - left)
    if cropped_area < img.shape[0] * img.shape[1] * 0.30:
        return img

    return img[top:bottom, left:right]


# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def save_debug(img: np.ndarray, stem: str, step: int, name: str):
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DIR / f"{stem}_{step}_{name}.jpg"
    cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return path


def process_image(
    image_path: Path,
    engine: str = "paddle",
    use_gamma: float = 0.0,
    use_sharpen: bool = False,
    output_dir: Path = OUTPUT_DIR,
    debug: bool = True,
) -> bool:
    """
    Tek bir görüntüyü işle.

    engine     : "paddle" (default) veya "tesseract" — binary adımını etkiler
    use_gamma  : 0.0 = kapalı, 0.1–0.9 = açık (önerilen: 0.7)
    use_sharpen: True = unsharp masking uygula
    output_dir : çıktı klasörü (test senaryolarında farklı dizin verilebilir)
    """
    print(f"\n  {image_path.name}")

    img = cv2.imread(str(image_path))
    if img is None:
        print(f"    HATA: Goruntu okunamaді: {image_path}")
        return False

    h, w = img.shape[:2]
    opts = []
    if use_gamma:   opts.append(f"gamma={use_gamma}")
    if use_sharpen: opts.append("sharpen")
    print(f"    Boyut: {w}x{h}px  [{', '.join(opts) or 'baseline'}]")
    stem = image_path.stem
    debug_dir = output_dir / "debug"

    def _dbg(im, step, name):
        if debug:
            debug_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(
                str(debug_dir / f"{stem}_{step}_{name}.jpg"),
                im, [cv2.IMWRITE_JPEG_QUALITY, 92]
            )

    # ── Adım 0: Minimum genişlik ──────────────────────────────────────────────
    w0 = img.shape[1]
    cur = enforce_min_width(img)
    if cur.shape[1] != w0:
        print(f"    [0] Upscale: {w0}px -> {cur.shape[1]}px")
    _dbg(cur, 0, "upscale")

    # ── Adım 1: Dönme düzeltme ────────────────────────────────────────────────
    try:
        cur, angle = correct_rotation(cur)
        if abs(angle) >= 0.3:
            print(f"    [1] Donme: {angle:+.1f} derece")
        _dbg(cur, 1, "rotated")
    except Exception as e:
        print(f"    [1] Donme: HATA ({e})")

    # ── Adım 2: Perspektif düzeltme ───────────────────────────────────────────
    try:
        w_before = cur.shape[1]
        warped = correct_perspective(cur)
        if warped.shape != cur.shape:
            print(f"    [2] Perspektif: {cur.shape[1]}x{cur.shape[0]} -> {warped.shape[1]}x{warped.shape[0]}")
        cur = warped
        _dbg(cur, 2, "perspective")
    except Exception as e:
        print(f"    [2] Perspektif: HATA ({e})")

    # Perspektif sonrası genişlik kontrolü
    w_after = cur.shape[1]
    cur = enforce_min_width(cur)
    if cur.shape[1] != w_after:
        print(f"    [2] Post-perspektif upscale: {w_after}px -> {cur.shape[1]}px")

    # ── Adım 3: Arka plan normalizasyonu ──────────────────────────────────────
    try:
        brightness = cv2.cvtColor(cur, cv2.COLOR_BGR2GRAY).mean()
        cur = normalize_background(cur)
        status = f"uygulandı (parlak={brightness:.0f})" if brightness <= 200 else f"atlandı (parlak={brightness:.0f})"
        print(f"    [3] Bg normaliz: {status}")
        _dbg(cur, 3, "bg_norm")
    except Exception as e:
        print(f"    [3] Bg normaliz: HATA ({e})")

    # ── Adım 4: Gamma (opsiyonel) ─────────────────────────────────────────────
    if use_gamma > 0:
        try:
            cur = gamma_correct(cur, gamma=use_gamma)
            print(f"    [4] Gamma: {use_gamma}")
            _dbg(cur, 4, "gamma")
        except Exception as e:
            print(f"    [4] Gamma: HATA ({e})")
    else:
        print(f"    [4] Gamma: atlandı")

    # ── Adım 5: CLAHE kontrast ────────────────────────────────────────────────
    try:
        cur = enhance_contrast(cur)
        print(f"    [5] CLAHE: tamamlandı")
        _dbg(cur, 5, "clahe")
    except Exception as e:
        print(f"    [5] CLAHE: HATA ({e})")

    # ── Adım 6: Denoise ───────────────────────────────────────────────────────
    try:
        cur = denoise(cur)
        print(f"    [6] Denoise: tamamlandı")
        _dbg(cur, 6, "denoised")
    except Exception as e:
        print(f"    [6] Denoise: HATA ({e})")

    # ── Adım 7: Sharpen (opsiyonel) ───────────────────────────────────────────
    if use_sharpen:
        try:
            cur = sharpen(cur)
            print(f"    [7] Sharpen: tamamlandı")
            _dbg(cur, 7, "sharpened")
        except Exception as e:
            print(f"    [7] Sharpen: HATA ({e})")
    else:
        print(f"    [7] Sharpen: atlandı")

    # ── Adım 8: Binary (sadece Tesseract) ─────────────────────────────────────
    if engine == "tesseract":
        try:
            cur = to_binary(cur)
            print(f"    [8] Binary: uygulandı (Tesseract)")
            _dbg(cur, 8, "binary")
        except Exception as e:
            print(f"    [8] Binary: HATA ({e})")
    else:
        print(f"    [8] Binary: atlandı (PaddleOCR)")

    # ── Adım 9: Crop ──────────────────────────────────────────────────────────
    try:
        cur = crop_receipt(cur)
        print(f"    [9] Crop: final {cur.shape[1]}x{cur.shape[0]}px")
        _dbg(cur, 9, "crop")
    except Exception as e:
        print(f"    [9] Crop: HATA ({e})")

    # ── Final kayıt ───────────────────────────────────────────────────────────
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / image_path.name
    cv2.imwrite(str(out_path), cur, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print(f"    => {out_path}")
    return True


def process_folder(
    folder: Path,
    engine: str = "paddle",
    use_gamma: float = 0.0,
    use_sharpen: bool = False,
    output_dir: Path = OUTPUT_DIR,
    debug: bool = True,
):
    images = sorted([
        p for p in folder.iterdir()
        if p.suffix.lower() in SUPPORTED_EXTS
    ])

    if not images:
        print(f"HATA: {folder} icinde jpg/png bulunamadi")
        return

    opts = []
    if use_gamma:   opts.append(f"gamma={use_gamma}")
    if use_sharpen: opts.append("sharpen")
    config_label = ", ".join(opts) or "baseline"

    print(f"\n{'='*60}")
    print(f"  {len(images)} goruntu: {folder}  (engine={engine}, {config_label})")
    print(f"  Cikti: {output_dir}/")
    print(f"  Debug: {output_dir / 'debug'}/")
    print(f"{'='*60}")

    success = failed = 0
    for img_path in images:
        try:
            ok = process_image(
                img_path,
                engine=engine,
                use_gamma=use_gamma,
                use_sharpen=use_sharpen,
                output_dir=output_dir,
                debug=debug,
            )
            success += 1 if ok else 0
            failed  += 0 if ok else 1
        except Exception as e:
            print(f"    HATA: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*60}")
    print(f"  Tamamlandi: {success} basarili, {failed} basarisiz")
    print(f"{'='*60}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Fiş görüntüsü ön işleyici",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Örnekler:
  python preProcess.py Receipts/
  python preProcess.py Receipts/ --gamma 0.7
  python preProcess.py Receipts/ --sharpen
  python preProcess.py Receipts/ --gamma 0.7 --sharpen
  python preProcess.py Receipts/ --engine tesseract --output .processed_tess
        """
    )
    ap.add_argument("target", help="Klasör veya tek görüntü")
    ap.add_argument("--engine", choices=["paddle", "tesseract"], default="paddle",
                    help="OCR motoru (binary adımını etkiler, varsayılan: paddle)")
    ap.add_argument("--gamma", type=float, default=0.0, metavar="GAMMA",
                    help="Gamma düzeltme değeri (0.0=kapalı, önerilen: 0.7)")
    ap.add_argument("--sharpen", action="store_true",
                    help="Unsharp masking ile kenar keskinleştirme uygula")
    ap.add_argument("--output", type=Path, default=OUTPUT_DIR, metavar="DIR",
                    help=f"Çıktı dizini (varsayılan: {OUTPUT_DIR})")
    ap.add_argument("--no-debug", action="store_true", help="Debug görüntülerini kaydetme")
    args = ap.parse_args()

    target = Path(args.target)
    if not target.exists():
        print(f"HATA: Bulunamadi: {target}")
        sys.exit(1)

    if target.is_dir():
        process_folder(
            target,
            engine=args.engine,
            use_gamma=args.gamma,
            use_sharpen=args.sharpen,
            output_dir=args.output,
            debug=not args.no_debug,
        )
    elif target.suffix.lower() in SUPPORTED_EXTS:
        args.output.mkdir(parents=True, exist_ok=True)
        process_image(
            target,
            engine=args.engine,
            use_gamma=args.gamma,
            use_sharpen=args.sharpen,
            output_dir=args.output,
            debug=not args.no_debug,
        )
    else:
        print(f"HATA: Desteklenmeyen dosya turu: {target.suffix}")
        sys.exit(1)


if __name__ == "__main__":
    main()
