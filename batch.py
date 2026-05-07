"""
Fiş Batch İşleyici
==================
Bir klasördeki tüm jpg/png fişleri tarar, OCR yapar,
kategoriler, journal'ı günceller.

Kullanım:
    python batch.py <fis_klasoru> <journal.hledger> [--api-key sk-ant-...]

Bağımlılıklar:
    pip install paddleocr anthropic

Dosya yapısı:
    fis_klasoru/
        bim_20260326.jpg
        migros_20260327.jpg
        ...
    rules.toml          → elle yazılan genel kurallar
    rules_learned.toml  → Claude'un öğrendikleri (otomatik)
"""

import json
import os
import platform
import sys
import re
import logging
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from parser import parse_receipt, print_summary, Receipt, ReceiptItem
from rules import load_rules, find_account, append_learned_rule, DEFAULT_ACCOUNT
from update_journal import (
    parse_journal, find_matching_transaction,
    build_new_transaction, update_journal, preview,
)
from snapshots import save_snapshot, check_snapshot
from preProcess import process_image as preprocess_image

logging.basicConfig(level=logging.WARNING)  # PaddleOCR loglarını sustur

from config import RULES_FILE, RULES_LEARNED as LEARNED_RULES_FILE, OCR_CACHE_DIR, OCR_CACHE_DIR_TROCR

SUPPORTED_EXTS      = {".jpg", ".jpeg", ".png"}
OCR_CACHE_DIR_EASY  = Path(".ocr_cache_easyocr")   # EasyOCR cache (Türkçe karakter desteği)


# ── OCR ───────────────────────────────────────────────────────────────────────

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


def get_easyocr_engine():
    """EasyOCR'ı bir kez yükle. Türkçe karakter desteği var (İ, Ğ, Ş, Ö, Ü, Ç)."""
    try:
        import easyocr
    except ImportError:
        print("❌ EasyOCR yüklü değil: pip install easyocr")
        sys.exit(1)
    print("⏳ EasyOCR yükleniyor (ilk seferinde model indirilebilir)...")
    reader = easyocr.Reader(['tr'], gpu=False, verbose=False)
    print("+ EasyOCR hazır (Türkçe: İ/Ğ/Ş/Ö/Ü/Ç desteği aktif)\n")
    return reader


def get_trocr_engine():
    """PaddleOCR detection + TrOCR large-printed recognition pipeline.

    trocr_adapter/ klasörü varsa (train_trocr.py ile oluşturulur) LoRA
    adapter'ı otomatik yükler — Türkçe fiş tanıma giderek iyileşir.
    """
    try:
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel
        import torch
    except ImportError:
        print("❌ transformers/torch yüklü değil: pip install transformers torch")
        sys.exit(1)

    # Detection: fişler için fine-tune edilmiş PaddleOCR detector
    import os
    os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
    from paddleocr import PaddleOCR
    os.environ["FLAGS_use_mkldnn"] = "0"
    print("⏳ PaddleOCR detector yükleniyor...")
    paddle = PaddleOCR(
        use_textline_orientation=False,
        lang='tr',
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name="PP-OCRv5_mobile_rec",
        enable_mkldnn=(platform.system() == "Linux"),
        text_det_unclip_ratio=1.6,
        text_det_box_thresh=0.5,
        text_det_thresh=0.3,
        use_doc_unwarping=False,
    )

    # Recognition: TrOCR large-printed (+ opsiyonel LoRA adapter)
    MODEL_ID    = "microsoft/trocr-base-printed"
    ADAPTER_DIR = Path("trocr_adapter")

    print(f"⏳ TrOCR yükleniyor ({MODEL_ID})...")
    processor = TrOCRProcessor.from_pretrained(MODEL_ID)
    device    = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    dtype     = torch.float16 if device.type == "cuda" else torch.float32
    model     = VisionEncoderDecoderModel.from_pretrained(MODEL_ID, torch_dtype=dtype)

    # trocr_adapter/ varsa otomatik yükle
    if (ADAPTER_DIR / "adapter_config.json").exists():
        try:
            from peft import PeftModel
            model = PeftModel.from_pretrained(model, str(ADAPTER_DIR))
            print(f"  + LoRA adapter yüklendi: {ADAPTER_DIR}/")
        except ImportError:
            print(f"  ⚠️  trocr_adapter/ var ama peft yüklü değil — temel model kullanılıyor")
            print(f"      pip install peft  ile kurabilirsin")
        except Exception as e:
            print(f"  ⚠️  LoRA adapter yüklenemedi ({e}) — temel model kullanılıyor")
    else:
        print(f"  (trocr_adapter/ yok — temel model kullanılıyor)")

    model.to(device)
    model.eval()
    print(f"+ TrOCR hazır — cihaz: {device} (PaddleOCR det + TrOCR rec)\n")
    return (paddle, processor, model, device)


def _run_trocr(engine_tuple, image_path: Path, img, w: int, h: int) -> dict:
    """PaddleOCR detection → batch crop → TrOCR recognition pipeline."""
    import torch
    from PIL import ImageDraw

    paddle, processor, model, device = engine_tuple

    # PaddleOCR detection: bbox'ları al
    result = list(paddle.predict(str(image_path)))

    bboxes, crops = [], []
    for ocr_result in result:
        boxes = ocr_result.get("dt_polys") or ocr_result.get("boxes")
        if boxes is None:
            continue
        for bbox in boxes:
            if hasattr(bbox, "tolist"):
                bbox = bbox.tolist()
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            x1 = max(0, int(min(xs)))
            y1 = max(0, int(min(ys)))
            x2 = min(w, int(max(xs)))
            y2 = min(h, int(max(ys)))
            crop = img.crop((x1, y1, x2, y2))
            if crop.width < 4 or crop.height < 4:
                continue
            bboxes.append(bbox)
            crops.append(crop)

    # Crop'ları küçük gruplar halinde gönder (GPU timeout önleme)
    INFERENCE_BATCH = 4
    detections = []
    for i in range(0, len(crops), INFERENCE_BATCH):
        batch_crops  = crops[i:i + INFERENCE_BATCH]
        batch_bboxes = bboxes[i:i + INFERENCE_BATCH]
        pixel_values = processor(images=batch_crops, return_tensors="pt", padding=True).pixel_values
        pixel_values = pixel_values.to(device)
        with torch.no_grad():
            generated_ids = model.generate(pixel_values=pixel_values)
        texts = processor.batch_decode(generated_ids, skip_special_tokens=True)
        for bbox, text in zip(batch_bboxes, texts):
            text = text.strip()
            if text:
                detections.append([bbox, [text, 0.95]])

    # Görselleştirme (.guidedReceipts/ klasörüne)
    guided_receipts_dir = Path(".guidedReceipts")
    guided_receipts_dir.mkdir(exist_ok=True)
    vis = img.copy()
    draw = ImageDraw.Draw(vis)
    for det in detections:
        pts = [(p[0], p[1]) for p in det[0]]
        draw.polygon(pts, outline="blue")
    vis.save(str(guided_receipts_dir / image_path.name))

    print(f"  TrOCR: Toplam {len(detections)} detection")
    return {"status": "success", "image_width": w, "image_height": h, "detections": detections}


def run_ocr(ocr_engine, image_path: Path, engine_name: str = "paddleocr") -> dict:
    """OCR çalıştır ve parser'ın beklediği ortak formatta döndür."""
    import numpy as np
    from PIL import Image

    img = Image.open(image_path).convert("RGB")
    img_array = np.array(img)
    h, w = img_array.shape[:2]

    if engine_name == "easyocr":
        return _run_easyocr(ocr_engine, image_path, img, w, h)
    elif engine_name == "trocr":
        return _run_trocr(ocr_engine, image_path, img, w, h)
    else:
        return _run_paddleocr(ocr_engine, image_path, img_array, w, h)


def _run_paddleocr(ocr_engine, image_path: Path, img_array, w: int, h: int) -> dict:
    result = list(ocr_engine.predict(str(image_path)))

    guided_receipts_dir = Path(".guidedReceipts")
    guided_receipts_dir.mkdir(exist_ok=True)
    output_path = guided_receipts_dir / image_path.name
    result[0].img['ocr_res_img'].save(str(output_path))

    detections = []
    for i, ocr_result in enumerate(result):
        boxes  = ocr_result.get("dt_polys") or ocr_result.get("boxes")
        texts  = ocr_result.get("rec_texts") or ocr_result.get("texts")
        scores = ocr_result.get("rec_scores") or ocr_result.get("scores")
        if boxes is None or texts is None or scores is None:
            continue
        for bbox, text, conf in zip(boxes, texts, scores):
            if hasattr(bbox, "tolist"):
                bbox = bbox.tolist()
            detections.append([bbox, [text, float(conf)]])

    return {"status": "success", "image_width": w, "image_height": h, "detections": detections}


def _run_easyocr(reader, image_path: Path, img, w: int, h: int) -> dict:
    """EasyOCR çalıştır. Bbox formatı PaddleOCR ile aynı — parser doğrudan okur."""
    from PIL import ImageDraw

    results = reader.readtext(str(image_path))

    # Görselleştirme (.guidedReceipts/ klasörüne)
    guided_receipts_dir = Path(".guidedReceipts")
    guided_receipts_dir.mkdir(exist_ok=True)
    vis = img.copy()
    draw = ImageDraw.Draw(vis)
    for (bbox, text, conf) in results:
        pts = [(int(p[0]), int(p[1])) for p in bbox]
        draw.polygon(pts, outline="red")
    vis.save(str(guided_receipts_dir / image_path.name))

    # EasyOCR: (bbox, text, conf) — bbox [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
    # load_detections() beklediği format: [bbox, [text, conf]]  — aynı polygon, direkt uyumlu
    detections = []
    for (bbox, text, conf) in results:
        # EasyOCR bazen numpy array döndürür, listeye çevir
        if hasattr(bbox, "tolist"):
            bbox = bbox.tolist()
        else:
            bbox = [[int(p[0]), int(p[1])] for p in bbox]
        detections.append([bbox, [text, float(conf)]])

    print(f"  EasyOCR: Toplam {len(detections)} detection")
    return {"status": "success", "image_width": w, "image_height": h, "detections": detections}

def ocr_with_cache(ocr_engine, image_path: Path, engine_name: str = "paddleocr") -> dict:
    """Cache'te varsa OCR'ı tekrar yapmaz. Her engine'in ayrı cache klasörü var."""
    if engine_name == "easyocr":
        cache_dir = OCR_CACHE_DIR_EASY
    elif engine_name == "trocr":
        cache_dir = OCR_CACHE_DIR_TROCR
    else:
        cache_dir = OCR_CACHE_DIR
    cache_dir.mkdir(exist_ok=True)
    cache_file = cache_dir / (image_path.stem + ".json")

    if cache_file.exists():
        print(f"  📂 Cache'ten okunuyor: {cache_file.name}")
        return json.loads(cache_file.read_text(encoding="utf-8"))

    print(f"  🔍 OCR yapılıyor: {image_path.name}")
    result = run_ocr(ocr_engine, image_path, engine_name)
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


# ── Claude API fallback ────────────────────────────────────────────────────────

def ask_claude(item_name: str, store: str, amount: float, api_key: str) -> Optional[str]:
    """
    Rule bulunamazsa Claude'a sor.
    Sadece account adını döndürmesini iste — kısa, ucuz.
    """
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""Sen bir kişisel finans asistanısın. Türk hledger kullanıcısı için market fişi kalemlerini kategorilendiriyorsun.

Market: {store}
Ürün adı: {item_name}
Tutar: {amount:.2f} TL

Aşağıdaki hledger account hiyerarşisini kullan:
- gider:market:gida:meyve
- gider:market:gida:sebze
- gider:market:gida:et
- gider:market:gida:tavuk
- gider:market:gida:sut-urunleri
- gider:market:gida:ekmek-tahil
- gider:market:gida:bakliyat
- gider:market:gida:icecek
- gider:market:gida:atistirmalik
- gider:market:gida:kahvaltilik
- gider:market:gida:kuru-gida
- gider:market:temizlik
- gider:market:kisisel-bakim
- gider:market:poset
- gider:market:diger
- gider:kitap
- gider:kirtasiye
- gider:ulasim:yakit

SADECE account adını yaz, başka hiçbir şey yazma. Örnek: gider:market:gida:meyve"""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",  # en ucuz model, bu iş için yeterli
            max_tokens=50,
            messages=[{"role": "user", "content": prompt}],
        )
        account = response.content[0].text.strip().lower()
        # Güvenlik: sadece gider: ile başlayan cevapları kabul et
        if account.startswith("gider:"):
            return account
        return None
    except Exception as e:
        print(f"  ⚠️  Claude API hatası: {e}")
        return None


# ── Kategori tespiti ───────────────────────────────────────────────────────────

def categorize_items(
    receipt: Receipt,
    rules: list,
    api_key: Optional[str],
) -> list[tuple[ReceiptItem, str]]:
    results = []
    unknown_cache = {}  # aynı ürünü tekrar sorma

    for item in receipt.items:
        # 1. Rule engine
        account = find_account(item.name, receipt.store, item.amount, rules)
        if account:
            results.append((item, account))
            continue

        # 2. Cache
        if item.name in unknown_cache:
            results.append((item, unknown_cache[item.name]))
            continue

        # 3. Claude API fallback
        if api_key:
            print(f"  🤖 Claude'a soruluyor: {item.name} ({item.amount:.2f} TL)")
            account = ask_claude(item.name, receipt.store, item.amount, api_key)
            if account:
                print(f"     → {account}")
                unknown_cache[item.name] = account
                append_learned_rule(item.name, account, LEARNED_RULES_FILE)
                results.append((item, account))
                continue

        # 4. Manuel giriş (API yoksa veya başarısızsa)
        print(f"\n  ❓ Tanınmayan ürün: \033[1m{item.name}\033[0m  ({item.amount:.2f} TL)")
        print(f"     Hangi hesaba? (boş → '{DEFAULT_ACCOUNT}')")
        try:
            answer = input("     > ").strip()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        chosen = answer if answer else DEFAULT_ACCOUNT
        unknown_cache[item.name] = chosen
        append_learned_rule(item.name, chosen, LEARNED_RULES_FILE)
        results.append((item, chosen))

    return results


# ── Tek fiş işleme ─────────────────────────────────────────────────────────────

def process_receipt(
    image_path: Path,
    ocr_engine,
    rules: list,
    api_key: Optional[str],
    journal_path: Optional[Path] = None,
    excel_path: Optional[Path] = None,
    excel_sheet: Optional[str] = None,
    engine_name: str = "paddleocr",
) -> bool:
    """Bir fişi işle. En az bir kanal güncellendiyse True döndür."""
    print(f"\n{'═' * 60}")
    print(f"  📄 {image_path.name}")
    print(f"{'═' * 60}")

    # OCR
    try:
        ocr_json = ocr_with_cache(ocr_engine, image_path, engine_name)
    except Exception as e:
        print(f"  ❌ OCR hatası: {e}")
        return False

    # Parse
    try:
        receipt = parse_receipt(ocr_json)
        print_summary(receipt)
    except ValueError as e:
        print(f"  ❌ Parse hatası: {e}")
        return False

    # Snapshot kontrol
    ocr_path = OCR_CACHE_DIR / (image_path.stem + ".json")
    snap_diffs = check_snapshot(ocr_path, receipt)
    if snap_diffs:
        print(f"  [!] SNAPSHOT FARKI TESPIT EDILDI:")
        for diff in snap_diffs:
            print(f"      - {diff}")
        print(f"  Snapshot guncellensin mi? [e/H] ", end="", flush=True)
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "h"
        if answer == "e":
            save_snapshot(ocr_path, receipt)
            print(f"  [snapshot] Guncellendi: {ocr_path.name}")
        else:
            print(f"  [snapshot] Korundu -- fis atlandi.")
            return False
    else:
        saved = save_snapshot(ocr_path, receipt)
        if saved:
            print(f"  [snapshot] Kaydedildi: {ocr_path.name}")

    # Kategorile — her iki kanal için de gerekli
    categorized = categorize_items(receipt, rules, api_key)

    print("\n  Kategoriler:")
    for item, account in categorized:
        print(f"    {item.name:<40} → {account}")

    any_updated = False

    # ── hledger güncelleme ─────────────────────────────────────────────────────
    if journal_path:
        transactions = parse_journal(journal_path)
        tx = find_matching_transaction(receipt, transactions)

        if tx is None:
            total_str = f"{receipt.total:.2f}" if receipt.total is not None else "bilinmiyor"
            print(f"\n  ❌ hledger: eşleşme bulunamadı!")
            print(f"     Aranan: {receipt.date}  {total_str} TL  ({receipt.store})")
        else:
            print(f"  ✓ hledger: satır {tx.start_line + 1} → {tx.raw_lines[0].strip()}")
            new_lines = build_new_transaction(tx, categorized, receipt)
            preview(new_lines)
            print("  hledger güncellensin mi? [e/H] ", end="", flush=True)
            try:
                answer = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "h"
            if answer == "e":
                update_journal(journal_path, tx, new_lines)
                print(f"  ✓ hledger güncellendi")
                any_updated = True
            else:
                print("  hledger atlandı.")

    # ── Excel güncelleme ───────────────────────────────────────────────────────
    if excel_path:
        from update_excel import find_excel_match, update_excel, preview_excel
        from_row, account = find_excel_match(excel_path, receipt, excel_sheet)

        if from_row is None:
            total_str = f"{receipt.total:.2f}" if receipt.total is not None else "bilinmiyor"
            print(f"\n  ❌ Excel: eşleşme bulunamadı!")
            print(f"     Aranan: {receipt.date}  {total_str} TL")
        else:
            print(f"  ✓ Excel: satır {from_row} → {account}")
            preview_excel(categorized, receipt)
            print("  Excel güncellensin mi? [e/H] ", end="", flush=True)
            try:
                answer = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "h"
            if answer == "e":
                ok = update_excel(excel_path, receipt, categorized, excel_sheet)
                if ok:
                    print(f"  ✓ Excel güncellendi: {excel_path.name}")
                    any_updated = True
                else:
                    print(f"  ❌ Excel güncellenemedi")
            else:
                print("  Excel atlandı.")

    return any_updated


# ── Ana akış ───────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Kullanım: python batch.py <fis_klasoru> [seçenekler]")
        print("\nSeçenekler:")
        print("  --hledger butce.hledger  hledger journal güncelle")
        print("  --excel   butce.xlsx     Excel defteri güncelle")
        print("  --sheet   SheetName      Excel sheet adı (default: ilk sheet)")
        print("  --api-key sk-ant-...     Anthropic API anahtarı")
        print("  --preprocess             Görüntüleri OCR öncesi ön işle")
        print("  --engine paddleocr|easyocr  OCR motoru (default: paddleocr)")
        print("\nÖrnekler:")
        print("  python batch.py fisler/ --hledger butce.hledger")
        print("  python batch.py fisler/ --excel butce.xlsx")
        print("  python batch.py fisler/ --hledger butce.hledger --excel butce.xlsx")
        print("  python batch.py fisler/ --excel butce.xlsx --sheet Harcamalar")
        sys.exit(1)

    fis_dir = Path(sys.argv[1])

    # API key opsiyonel
    api_key = None
    if "--api-key" in sys.argv:
        idx = sys.argv.index("--api-key")
        if idx + 1 < len(sys.argv):
            api_key = sys.argv[idx + 1]
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY")

    # hledger opsiyonel
    journal_path = None
    if "--hledger" in sys.argv:
        idx = sys.argv.index("--hledger")
        if idx + 1 < len(sys.argv):
            journal_path = Path(sys.argv[idx + 1])
            if not journal_path.exists():
                print(f"❌ hledger dosyası bulunamadı: {journal_path}")
                sys.exit(1)

    # Excel opsiyonel
    excel_path = None
    if "--excel" in sys.argv:
        idx = sys.argv.index("--excel")
        if idx + 1 < len(sys.argv):
            excel_path = Path(sys.argv[idx + 1])
            if not excel_path.exists():
                print(f"❌ Excel dosyası bulunamadı: {excel_path}")
                sys.exit(1)

    excel_sheet = None
    if "--sheet" in sys.argv:
        idx = sys.argv.index("--sheet")
        if idx + 1 < len(sys.argv):
            excel_sheet = sys.argv[idx + 1]

    if not fis_dir.is_dir():
        print(f"❌ Klasör bulunamadı: {fis_dir}")
        sys.exit(1)

    if not journal_path and not excel_path:
        print("⚠️  Güncelleme kanalı seçilmedi — sadece OCR + kategorize yapılacak.")
        print("   Güncelleme için --hledger ve/veya --excel ekle.\n")

    # Fişleri bul
    images = sorted([
        p for p in fis_dir.iterdir()
        if p.suffix.lower() in SUPPORTED_EXTS
    ])

    if not images:
        print(f"❌ {fis_dir} içinde jpg/png bulunamadı")
        sys.exit(1)

    print(f"📁 {len(images)} fiş bulundu: {fis_dir}")
    if api_key:
        print(f"🤖 Claude API aktif (bilinmeyen ürünler otomatik kategorilenir)")
    else:
        print(f"⚠️  Claude API yok — bilinmeyen ürünler manuel girilecek")
        print(f"   (ANTHROPIC_API_KEY env veya --api-key ile ekleyebilirsin)")
    if journal_path:
        print(f"📒 hledger aktif: {journal_path}")
    if excel_path:
        sheet_info = f" (sheet: {excel_sheet})" if excel_sheet else " (ilk sheet)"
        print(f"📊 Excel aktif: {excel_path}{sheet_info}")
    print()

    # Ön işleme (--preprocess ile aktif)
    if "--preprocess" in sys.argv:
        print(f"⏳ {len(images)} fiş ön işleniyor...")
        for image_path in images:
            preprocess_image(image_path, engine="paddle", debug=False)
        print(f"✓ Ön işleme tamamlandı\n")
        images = sorted([
            p for p in Path(".processedReceipts").iterdir()
            if p.suffix.lower() in SUPPORTED_EXTS
        ])

    # Kuralları yükle — öğrenilmiş kurallar önce
    rules = []
    if LEARNED_RULES_FILE.exists():
        rules += load_rules(LEARNED_RULES_FILE)
    rules += load_rules(RULES_FILE)
    
    print(f"📋 {len(rules)} kural yüklendi")
    
    # OCR engine seçimi
    engine_name = "paddleocr"
    if "--engine" in sys.argv:
        idx = sys.argv.index("--engine")
        if idx + 1 < len(sys.argv):
            engine_name = sys.argv[idx + 1].lower()
    if engine_name not in ("paddleocr", "easyocr", "trocr"):
        print(f"❌ Bilinmeyen engine: {engine_name}  (paddleocr, easyocr veya trocr olmalı)")
        sys.exit(1)

    print(f"🔧 OCR engine: {engine_name}")
    if engine_name == "easyocr":
        ocr_engine = get_easyocr_engine()
    elif engine_name == "trocr":
        ocr_engine = get_trocr_engine()
    else:
        ocr_engine = get_ocr_engine()

    # İşle
    success, failed, skipped = 0, 0, 0
    for image_path in images:
        ok = process_receipt(image_path, ocr_engine, rules, api_key,
                             journal_path=journal_path,
                             excel_path=excel_path, excel_sheet=excel_sheet,
                             engine_name=engine_name)
        if ok:
            success += 1
        else:
            failed += 1
    
    # Özet
    print(f"\n{'═' * 60}")
    print(f"  Tamamlandı: {success} güncellendi, {failed} başarısız")
    if LEARNED_RULES_FILE.exists():
        learned_count = len(load_rules(LEARNED_RULES_FILE))
        print(f"  📚 rules_learned.toml: {learned_count} kural birikti")
    print(f"{'═' * 60}\n")


if __name__ == "__main__":
    main()
