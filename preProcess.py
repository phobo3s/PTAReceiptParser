"""
Fiş Görüntü Ön İşleyici
========================
Bir klasördeki tüm jpg/png fişleri işler ve .processedReceipts/ klasörüne kaydeder.

Kullanım:
    python preprocess.py <klasor>
    python preprocess.py Receipts/
    python preprocess.py fiş.jpg        ← tek dosya da desteklenir

Adımlar (sırayla):
    1. Dönme düzeltme     — Hough çizgileriyle eğim tespiti
    2. Perspektif düzeltme — 4 köşe tespiti + warpPerspective
    3. Kontrast normalize  — CLAHE ile adaptif histogram eşitleme
    4. Adaptif binary      — Termal kağıt için adaptif threshold
    5. Crop               — Fiş dışı boşlukları kırp

Çıktılar:
    .processedReceipts/
        fiş.jpg                  ← final (tüm adımlar uygulanmış)
        debug/
            fiş_1_rotated.jpg
            fiş_2_perspective.jpg
            fiş_3_contrast.jpg
            fiş_4_binary.jpg
            fiş_5_crop.jpg

Bağımlılıklar:
    pip install opencv-python numpy Pillow
"""

import cv2
import numpy as np
from pathlib import Path
from PIL import Image
import sys
import traceback

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png"}
OUTPUT_DIR     = Path(".processedReceipts")
DEBUG_DIR      = OUTPUT_DIR / "debug"


# ═══════════════════════════════════════════════════════════════════════════════
# ADIM 1 — DÖNME DÜZELTMESİ
# ═══════════════════════════════════════════════════════════════════════════════

def correct_rotation(img: np.ndarray) -> tuple[np.ndarray, float]:
    """
    Hough line detection ile fişin eğimini tespit eder ve düzeltir.
    Fişlerin baskın yatay çizgilerini (metin satırları) kullanır.
    Döndürülen açı derece cinsindendir.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Kenar tespiti
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)

    # Hough line transform — sadece belirli uzunluktaki çizgileri al
    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=100,
        minLineLength=img.shape[1] * 0.3,  # görüntü genişliğinin %30'u kadar uzun
        maxLineGap=20
    )

    if lines is None or len(lines) == 0:
        return img, 0.0

    # Her çizginin açısını hesapla
    angles = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if x2 - x1 == 0:
            continue  # dikey çizgi, atla
        angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        # Sadece yataya yakın çizgileri kullan (-30 ile +30 derece arası)
        if -30 < angle < 30:
            angles.append(angle)

    if not angles:
        return img, 0.0

    # Medyan açı — outlier'lara karşı dayanıklı
    median_angle = np.median(angles)

    # Çok küçük açıları düzeltmeye gerek yok (0.3° altı)
    if abs(median_angle) < 0.3:
        return img, 0.0

    # Görüntüyü döndür
    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, median_angle, 1.0)

    # Döndürme sonrası köşe kaymasını önlemek için yeni boyutu hesapla
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

    # Blur + threshold ile arka planı temizle
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Morfolojik işlemler — delikleri kapat
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=3)

    # Kontur bul
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        return img

    # En büyük konturu al (fiş olmalı)
    largest = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(largest)

    # Görüntü alanının en az %20'si kadar olmalı (gürültü filtresi)
    img_area = img.shape[0] * img.shape[1]
    if area < img_area * 0.20:
        return img

    # Kontur yaklaşımı — 4 köşeye indir
    peri = cv2.arcLength(largest, True)
    approx = cv2.approxPolyDP(largest, 0.02 * peri, True)

    if len(approx) != 4:
        # 4 köşe bulunamadı — bounding rect ile fallback
        x, y, w, h = cv2.boundingRect(largest)
        # Görüntünün büyük kısmı zaten fiş ise perspective işlemi gereksiz
        if w > img.shape[1] * 0.85 and h > img.shape[0] * 0.85:
            return img
        # Bounding rect köşelerini kullan
        approx = np.array([
            [[x, y]], [[x + w, y]], [[x + w, y + h]], [[x, y + h]]
        ], dtype=np.float32)
    
    # 4 köşeyi sırala: sol-üst, sağ-üst, sağ-alt, sol-alt
    pts = approx.reshape(4, 2).astype(np.float32)
    rect = order_points(pts)

    tl, tr, br, bl = rect

    # Hedef genişlik ve yüksekliği hesapla
    width_top    = np.linalg.norm(tr - tl)
    width_bottom = np.linalg.norm(br - bl)
    max_width    = int(max(width_top, width_bottom))

    height_left  = np.linalg.norm(bl - tl)
    height_right = np.linalg.norm(br - tr)
    max_height   = int(max(height_left, height_right))

    # Hedef köşeler (düz dikdörtgen)
    dst = np.array([
        [0, 0],
        [max_width - 1, 0],
        [max_width - 1, max_height - 1],
        [0, max_height - 1]
    ], dtype=np.float32)

    M = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(img, M, (max_width, max_height))

    return warped


def order_points(pts: np.ndarray) -> np.ndarray:
    """4 noktayı sol-üst, sağ-üst, sağ-alt, sol-alt sırasına koy."""
    rect = np.zeros((4, 2), dtype=np.float32)

    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]   # sol-üst: en küçük toplam
    rect[2] = pts[np.argmax(s)]   # sağ-alt: en büyük toplam

    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]  # sağ-üst: en küçük fark
    rect[3] = pts[np.argmax(diff)]  # sol-alt: en büyük fark

    return rect


# ═══════════════════════════════════════════════════════════════════════════════
# ADIM 3 — KONTRAST NORMALİZASYON
# ═══════════════════════════════════════════════════════════════════════════════

def enhance_contrast(img: np.ndarray) -> np.ndarray:
    """
    CLAHE (Contrast Limited Adaptive Histogram Equalization) ile
    yerel kontrast artırımı. Termal fişlerin soluk baskısı için etkili.
    Global histogram eşitlemenin aksine, aşırı parlak/karanlık bölgeleri
    bozmadan sadece düşük kontrastlı alanları iyileştirir.
    """
    # LAB renk uzayına çevir (L kanalına CLAHE uygula, rengi koru)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)

    # clipLimit: kontrast sınırı (yüksek → daha agresif ama gürültü ekler)
    # tileGridSize: yerel bölge boyutu
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_enhanced = clahe.apply(l)

    enhanced = cv2.merge([l_enhanced, a, b])
    result = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# ADIM 4 — ADAPTİF BINARY
# ═══════════════════════════════════════════════════════════════════════════════

def to_binary(img: np.ndarray) -> np.ndarray:
    """
    Adaptif threshold ile görüntüyü binary'e çevirir.
    Global threshold yerine adaptif kullanılır çünkü fiş fotoğraflarında
    aydınlatma genellikle eşit değil (cep telefonu flaşı, gölge vs.).

    NOT: Binary görüntü BGR formatında döndürülür (3 kanal) — 
    PaddleOCR ve diğer adımlarla uyumluluk için.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Hafif blur — ince gürültüyü bastır
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)

    # Adaptif Gaussian threshold
    # blockSize: yerel bölge boyutu (tek sayı olmalı)
    # C: mean'den çıkarılan sabit (negatif → daha fazla siyah)
    binary = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=15,
        C=8
    )

    # Tek kanallı → 3 kanallı (BGR)
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)


# ═══════════════════════════════════════════════════════════════════════════════
# ADIM 5 — CROP
# ═══════════════════════════════════════════════════════════════════════════════

def crop_receipt(img: np.ndarray, padding: int = 10) -> np.ndarray:
    """
    Fiş içeriğinin etrafındaki boş/beyaz alanları kırpar.
    Binary görüntüde (siyah metin, beyaz arka plan) siyah piksel
    olmayan satır/sütunları kenar olarak tespit eder.
    
    padding: kenar etrafında bırakılacak piksel boşluğu
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Beyaz olmayan pikselleri bul
    # binary görüntüde metin siyah (0), arka plan beyaz (255)
    # invertlediğimizde metin beyaz olur, sum ile satır/sütun içeriği ölçülür
    inverted = cv2.bitwise_not(gray)
    
    row_sums = inverted.sum(axis=1)   # her satırdaki siyah piksel sayısı
    col_sums = inverted.sum(axis=0)   # her sütundaki siyah piksel sayısı

    # İçerik olan satır/sütunları bul (threshold: en az 5 piksel)
    threshold = 5 * 255
    rows_with_content = np.where(row_sums > threshold)[0]
    cols_with_content = np.where(col_sums > threshold)[0]

    if len(rows_with_content) == 0 or len(cols_with_content) == 0:
        return img  # içerik bulunamadı, orijinali döndür

    # Sınırları belirle
    top    = max(0, rows_with_content[0]  - padding)
    bottom = min(img.shape[0], rows_with_content[-1]  + padding + 1)
    left   = max(0, cols_with_content[0] - padding)
    right  = min(img.shape[1], cols_with_content[-1] + padding + 1)

    # Çok küçük crop sonuçlarını reddet (orijinalin %30'undan küçükse)
    cropped_area = (bottom - top) * (right - left)
    original_area = img.shape[0] * img.shape[1]
    if cropped_area < original_area * 0.30:
        return img

    return img[top:bottom, left:right]


# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def save_debug(img: np.ndarray, stem: str, step: int, name: str):
    """Debug görüntüsünü kaydet."""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DIR / f"{stem}_{step}_{name}.jpg"
    cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return path


def process_image(image_path: Path) -> bool:
    """
    Tek bir görüntüyü işle.
    Başarılıysa True, hata varsa False döndür.
    """
    print(f"\n  📄 {image_path.name}")

    # Görüntüyü oku
    img = cv2.imread(str(image_path))
    if img is None:
        print(f"    ❌ Görüntü okunamadı: {image_path}")
        return False

    h, w = img.shape[:2]
    print(f"    📐 Boyut: {w}×{h}px")
    stem = image_path.stem

    # ── Adım 1: Dönme düzeltme ────────────────────────────────────────────────
    print(f"    [1/5] Dönme düzeltme...", end=" ", flush=True)
    try:
        rotated, angle = correct_rotation(img)
        if abs(angle) >= 0.3:
            print(f"✓  ({angle:+.1f}°)")
        else:
            print(f"–  (düzeltme gerekmedi)")
        save_debug(rotated, stem, 1, "rotated")
    except Exception as e:
        print(f"⚠️  hata, atlandı: {e}")
        rotated = img

    # ── Adım 2: Perspektif düzeltme ───────────────────────────────────────────
    print(f"    [2/5] Perspektif düzeltme...", end=" ", flush=True)
    try:
        warped = correct_perspective(rotated)
        h2, w2 = warped.shape[:2]
        if (h2, w2) != (rotated.shape[0], rotated.shape[1]):
            print(f"✓  ({rotated.shape[1]}×{rotated.shape[0]} → {w2}×{h2})")
        else:
            print(f"–  (köşe tespiti yapılamadı, orijinal korundu)")
        save_debug(warped, stem, 2, "perspective")
    except Exception as e:
        print(f"⚠️  hata, atlandı: {e}")
        warped = rotated

    # ── Adım 3: Kontrast normalizasyon ────────────────────────────────────────
    print(f"    [3/5] Kontrast normalizasyon...", end=" ", flush=True)
    try:
        contrasted = enhance_contrast(warped)
        print(f"✓")
        save_debug(contrasted, stem, 3, "contrast")
    except Exception as e:
        print(f"⚠️  hata, atlandı: {e}")
        contrasted = warped

    # ── Adım 4: Binary ────────────────────────────────────────────────────────
    print(f"    [4/5] Adaptif binary...", end=" ", flush=True)
    try:
        binary = to_binary(contrasted)
        print(f"✓")
        save_debug(binary, stem, 4, "binary")
    except Exception as e:
        print(f"⚠️  hata, atlandı: {e}")
        binary = contrasted

    # ── Adım 5: Crop ──────────────────────────────────────────────────────────
    print(f"    [5/5] Crop...", end=" ", flush=True)
    try:
        cropped = crop_receipt(binary)
        h3, w3 = cropped.shape[:2]
        print(f"✓  (final: {w3}×{h3}px)")
        save_debug(cropped, stem, 5, "crop")
    except Exception as e:
        print(f"⚠️  hata, atlandı: {e}")
        cropped = binary

    # ── Final kayıt ───────────────────────────────────────────────────────────
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / image_path.name
    cv2.imwrite(str(out_path), cropped, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print(f"    ✅ Kaydedildi: {out_path}")

    return True


def process_folder(folder: Path):
    """Klasördeki tüm desteklenen görüntüleri işle."""
    images = sorted([
        p for p in folder.iterdir()
        if p.suffix.lower() in SUPPORTED_EXTS
    ])

    if not images:
        print(f"❌ {folder} içinde jpg/png bulunamadı")
        return

    print(f"\n{'═' * 60}")
    print(f"  📁 {len(images)} görüntü bulundu: {folder}")
    print(f"  📂 Çıktı: {OUTPUT_DIR}/")
    print(f"  🔍 Debug: {DEBUG_DIR}/")
    print(f"{'═' * 60}")

    success = 0
    failed  = 0
    for img_path in images:
        try:
            ok = process_image(img_path)
            if ok:
                success += 1
            else:
                failed += 1
        except Exception as e:
            print(f"    ❌ Beklenmeyen hata: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{'═' * 60}")
    print(f"  Tamamlandı: {success} başarılı, {failed} başarısız")
    print(f"  Final görüntüler : {OUTPUT_DIR}/")
    print(f"  Debug görüntüler : {DEBUG_DIR}/")
    print(f"{'═' * 60}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print("Kullanım: python preprocess.py <klasor_veya_dosya>")
        print("Örnek:    python preprocess.py Receipts/")
        print("Örnek:    python preprocess.py fiş.jpg")
        sys.exit(1)

    target = Path(sys.argv[1])

    if not target.exists():
        print(f"❌ Bulunamadı: {target}")
        sys.exit(1)

    if target.is_dir():
        process_folder(target)
    elif target.suffix.lower() in SUPPORTED_EXTS:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        process_image(target)
    else:
        print(f"❌ Desteklenmeyen dosya türü: {target.suffix}")
        print(f"   Desteklenen: {', '.join(SUPPORTED_EXTS)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
