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
#from preProcess import preProcessImage

logging.basicConfig(level=logging.WARNING)  # PaddleOCR loglarını sustur

RULES_FILE         = Path("rules.toml")
LEARNED_RULES_FILE = Path("rules_learned.toml")
SUPPORTED_EXTS     = {".jpg", ".jpeg", ".png"}
OCR_CACHE_DIR      = Path(".ocr_cache")  # işlenmiş json'ları sakla, tekrar OCR'lamaz


# ── OCR ───────────────────────────────────────────────────────────────────────

def get_ocr_engine():
    """PaddleOCR'ı bir kez yükle, tüm fişlerde kullan."""
    import os
    os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
    from paddleocr import PaddleOCR
    os.environ["FLAGS_use_mkldnn"] = "0"  # oneDNN disable
    print("⏳ PaddleOCR yükleniyor (ilk seferinde model indirilebilir)...")
    ocr = PaddleOCR(
        use_textline_orientation=True,
        #use_angle_cls=True,
        device='cpu',
        lang='tr',
        #character_dict_path='./customKeys.txt', # custom keyler. saçma sapan asci karakterleri ile uğraşmayalım diye
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name="PP-OCRv5_mobile_rec",
        enable_mkldnn=False,  # prevents MKLDNN/PIR crash
        # Detection (DB) Parameters
        text_det_unclip_ratio=1.9,   # Default is ~1.5. Increasing this expands the text bounding box. Highly useful for keeping "26.00" or "*250,00" in a single detection block.
        text_det_box_thresh=0.5,     # Default is ~0.6. Lowering this allows the model to detect fainter or slightly blurred text.
        text_det_thresh=0.3,         # Binarization threshold. Lowering it helps with low-contrast print on thermal paper.
        use_doc_unwarping=True
        # Recognition Parameters
        #unknown drop_score=0.7             # Filters out low-confidence random noise (like smudges recognized as characters).
    )
    print("+ PaddleOCR hazır\n")
    return ocr

def run_ocr(ocr_engine, image_path: Path) -> dict:
    import numpy as np
    from PIL import Image

    print(f"  DEBUG: Görüntü açılıyor...")
    img = Image.open(image_path).convert("RGB")
    img_array = np.array(img)
    h, w = img_array.shape[:2]
    print(f"  DEBUG: Görüntü boyutu: {w}x{h}")

    print(f"  DEBUG: predict çağrılıyor...")
    result = list(ocr_engine.predict(str(image_path)))
    print(f"  DEBUG: predict bitti, {len(result)} sonuç")
    
    guided_receipts_dir = Path(".guidedReceipts")
    guided_receipts_dir.mkdir(exist_ok=True)
    output_path = guided_receipts_dir / f"{image_path.name}"
    result[0].img['ocr_res_img'].save(str(output_path))
    
    detections = []
    for i, ocr_result in enumerate(result):
        print(f"  DEBUG: result[{i}] keys: {list(ocr_result.keys())}")
        boxes  = ocr_result.get("dt_polys") or ocr_result.get("boxes")
        texts  = ocr_result.get("rec_texts") or ocr_result.get("texts")
        scores = ocr_result.get("rec_scores") or ocr_result.get("scores")
        print(f"  DEBUG: boxes={boxes is not None}, texts={texts is not None}, scores={scores is not None}")
        if boxes is None or texts is None or scores is None:
            continue
        for bbox, text, conf in zip(boxes, texts, scores):
            if hasattr(bbox, "tolist"):
                bbox = bbox.tolist()
            detections.append([bbox, [text, float(conf)]])
    
    print(f"  DEBUG: Toplam {len(detections)} detection")
    return {"status": "success", "image_width": w, "image_height": h, "detections": detections}

def ocr_with_cache(ocr_engine, image_path: Path) -> dict:
    """Cache'te varsa OCR'ı tekrar yapmaz."""
    OCR_CACHE_DIR.mkdir(exist_ok=True)
    cache_file = OCR_CACHE_DIR / (image_path.stem + ".json")

    if cache_file.exists():
        print(f"  📂 Cache'ten okunuyor: {cache_file.name}")
        return json.loads(cache_file.read_text(encoding="utf-8"))

    print(f"  🔍 OCR yapılıyor: {image_path.name}")
    result = run_ocr(ocr_engine, image_path)
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
    journal_path: Path,
    ocr_engine,
    rules: list,
    api_key: Optional[str],
    excel_path: Optional[Path] = None,
    excel_sheet: Optional[str] = None,
) -> bool:
    """Bir fişi işle. Başarılıysa True döndür."""
    print(f"\n{'═' * 60}")
    print(f"  📄 {image_path.name}")
    print(f"{'═' * 60}")

    # OCR
    try:
        #processed_path = Path("ProcessedReceipts") / image_path.name
        ocr_json = ocr_with_cache(ocr_engine, image_path)
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

    # Snapshot kontrol — journal güncellemeden önce
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

    # Journal eşleştirme
    transactions = parse_journal(journal_path)
    tx = find_matching_transaction(receipt, transactions)

    if tx is None:
        print(f"  ❌ Journal'da eşleşme bulunamadı!")
        print(f"     Aranan: {receipt.date}  {receipt.total:.2f} TL  ({receipt.store})")
        return False

    print(f"  ✓ Eşleşen transaction: satır {tx.start_line + 1} → {tx.raw_lines[0].strip()}")

    # Kategorile
    categorized = categorize_items(receipt, rules, api_key)

    print("\n  Kategoriler:")
    for item, account in categorized:
        print(f"    {item.name:<40} → {account}")

    # Önizleme + onay
    new_lines = build_new_transaction(tx, categorized, receipt)
    preview(new_lines)

    print("  Journal güncellensin mi? [e/H] ", end="", flush=True)
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "h"

    journal_updated = False
    if answer == "e":
        update_journal(journal_path, tx, new_lines)
        print(f"  ✓ Journal güncellendi")
        journal_updated = True
    else:
        print("  Journal atlandı.")

    # ── Excel güncelleme (opsiyonel, journal'dan bağımsız) ─────────────────────
    if excel_path:
        from update_excel import update_excel, preview_excel
        preview_excel(tx.start_line + 1, categorized, receipt)
        print("  Excel güncellensin mi? [e/H] ", end="", flush=True)
        try:
            excel_answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            excel_answer = "h"
        if excel_answer == "e":
            ok = update_excel(excel_path, receipt, categorized, excel_sheet)
            if ok:
                print(f"  ✓ Excel güncellendi: {excel_path.name}")
            else:
                print(f"  ❌ Excel güncellenemedi")
        else:
            print("  Excel atlandı.")

    return journal_updated


# ── Ana akış ───────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 3:
        print("Kullanım: python batch.py <fis_klasoru> <journal.hledger> [seçenekler]")
        print("\nSeçenekler:")
        print("  --api-key sk-ant-...   Anthropic API anahtarı")
        print("  --excel butce.xlsx     Excel dosyası (opsiyonel, journal'dan bağımsız)")
        print("  --sheet SheetName      Excel sheet adı (default: ilk sheet)")
        print("\nÖrnekler:")
        print("  python batch.py fisler/ butce.hledger")
        print("  python batch.py fisler/ butce.hledger --api-key sk-ant-xxxx")
        print("  python batch.py fisler/ butce.hledger --excel butce.xlsx")
        print("  python batch.py fisler/ butce.hledger --excel butce.xlsx --sheet Harcamalar")
        sys.exit(1)

    fis_dir      = Path(sys.argv[1])
    journal_path = Path(sys.argv[2])

    # API key opsiyonel
    api_key = None
    if "--api-key" in sys.argv:
        idx = sys.argv.index("--api-key")
        if idx + 1 < len(sys.argv):
            api_key = sys.argv[idx + 1]
    # Ya da environment variable'dan
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY")

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
    if not journal_path.exists():
        print(f"❌ Journal bulunamadı: {journal_path}")
        sys.exit(1)

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
    if excel_path:
        sheet_info = f" (sheet: {excel_sheet})" if excel_sheet else " (ilk sheet)"
        print(f"📊 Excel aktif: {excel_path}{sheet_info}")
    print()

    # Ön işleme
    #print(f"⏳ {len(images)} fiş ön işleniyor...")
    #for image_path in images:
    #    preProcessImage(image_path)
    #print(f"✓ Ön işleme tamamlandı\n")

    # Processed Fişleri bul
    #images = sorted([
    #    p for p in Path("./.processedReceipts").iterdir()
    #    if p.suffix.lower() in SUPPORTED_EXTS
    #])

    # Kuralları yükle — öğrenilmiş kurallar önce
    rules = []
    if LEARNED_RULES_FILE.exists():
        rules += load_rules(LEARNED_RULES_FILE)
    rules += load_rules(RULES_FILE)
    
    print(f"📋 {len(rules)} kural yüklendi")
    
    # OCR engine
    ocr_engine = get_ocr_engine()
    
    # İşle
    success, failed, skipped = 0, 0, 0
    for image_path in images:
        ok = process_receipt(image_path, journal_path, ocr_engine, rules, api_key,
                             excel_path=excel_path, excel_sheet=excel_sheet)
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
