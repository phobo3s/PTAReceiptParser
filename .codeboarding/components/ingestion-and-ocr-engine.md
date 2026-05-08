---
component_id: 1
component_name: Ingestion & OCR Engine
---

# Ingestion & OCR Engine

## Component Description

Responsible for preparing raw images and extracting raw text data. It handles computer vision enhancements (deskewing, normalization) and abstracts multiple OCR backends (Paddle, Tesseract, etc.) behind a unified interface with a filesystem-based cache to optimize performance.

---

## Key References:

### c:\PTAReceiptParser\preProcess.py (lines 372-516)
```
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

```

### c:\PTAReceiptParser\ocr_engine.py (lines 124-150)
```
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
```

### c:\PTAReceiptParser\batch.py (lines 52-81)
```
def get_ocr_engine():
    """PaddleOCR'ı bir kez yükle, tüm fişlerde kullan."""
    import os
    os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
    from paddleocr import PaddleOCR
    os.environ["FLAGS_use_mkldnn"] = "0"  # oneDNN disable
    print("⏳ PaddleOCR yükleniyor (ilk seferinde model indirilebilir)...")
    ocr = PaddleOCR(
        use_textline_orientation=False,
        lang='tr',
        #use_angle_cls=True,
        #device='cpu',
        # NOT: lang parametresi, model adları verilince ignore ediliyor (UserWarning).
        # Türkçe için PaddleOCR'da özel model yok. en_PP-OCRv5_mobile_rec İ/Ğ/Ş gibi
        # karakterleri tamamen kaçırıyor. Multilingual model bunları "0" veya "I" olarak
        # veriyor — parser bu hataları zaten tolere ediyor, bu yüzden bu daha iyi.
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name="PP-OCRv5_mobile_rec",
        enable_mkldnn=(platform.system() == "Linux"),  # Linux'ta aktif; Windows'ta MKLDNN/PIR crash yapar
        # Detection (DB) Parameters
        #det_limit_side_len=1920,     # Default 960. Telefon fotoğrafları 2000-4000px uzun taraf içerebilir; küçültme küçük yazıları kaybettirir.
        text_det_unclip_ratio=1.6,   # Default ~1.5. "26.00" veya "*250,00" gibi değerlerin tek blok kalması için yeterli; 1.9 yoğun satırlarda komşu kutuları birleştiriyordu.
        text_det_box_thresh=0.5,     # Default ~0.6. Düşürülmüş: soluk/bulanık metni de yakalar.
        text_det_thresh=0.3,         # Binarization threshold. Düşürülmüş: düşük kontrastlı termal kağıt için.
        # Recognition Parameters
        #drop_score=0.5,              # OCR seviyesinde noise filtresi; parser'daki 0.60 eşiğiyle tutarlı.
        use_doc_unwarping=False,
    )
    print("+ PaddleOCR hazır\n")
    return ocr
```


## Source Files:

- `batch.py`
- `config.py`
- `import_labels.py`
- `ocr_engine.py`
- `preProcess.py`

