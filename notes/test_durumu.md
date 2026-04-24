# Unit Test Durum Raporu
**Tarih:** 2026-04-22  
**Çalıştırılan:** `python -m pytest test_parser.py -v`  
**Sonuç:** 6 hata / 1 başarılı / 7 toplam

---

## Başarısız Testler

### 1-3. `test_group_into_rows_*` (3 test)
**Hata:** `TypeError: Detection.__init__() missing 1 required positional argument: 'bbox'`

`Detection` dataclass'ına `bbox: list[list[float]]` alanı eklendi ama unit testler eski signature'ı kullanıyor. Testler `bbox` olmadan `Detection` nesnesi oluşturuyor.

**Düzeltme:** Test dosyasındaki `Detection(...)` çağrılarına dummy bbox ekle:
```python
bbox = [[x_min, y_min], [x_max, y_min], [x_max, y_max], [x_min, y_max]]
Detection(text="...", confidence=0.9, x_min=10, x_max=50,
          y_min=100, y_max=110, y_center=105, bbox=bbox)
```

### 4. `test_parse_receipt_bim_sample_1`
**Hata:** `AssertionError: 'KEKCİK.KAP30G PİNGU' != 'KEKCİK.KAP30G PİNGUI'`

OCR çıktısı `'KEKCiK.KAP30G PiNGU'` (sonunda `I` yok). Parser bunu `'KEKCİK.KAP30G PİNGU'` olarak parse ediyor. Test beklentisi `PİNGUI`. Bu bir OCR kalitesi sorunu — OCR gerçekten son harfi kaçırıyor.

**Düzeltme:** Test beklentisini güncel OCR çıktısıyla güncelle: `PİNGU` (trailing `I` bekleme).

### 5. `test_parse_receipt_tankar_sample_1`
**Hata:** `FileNotFoundError: .ocr_cache/WhatsApp Image 2026-03-27 at 09.05.48.json`

Bu dosya `.ocr_cache/` dizininde yok. Mevcut cache'te `09.05.32` (BİM) ve `09.05.57` (Tankar) var ama `09.05.48` yok.

**Düzeltme:** Dosya adını kontrol et — muhtemelen Tankar fişinin adı farklı veya test yanlış dosyaya bakıyor. `09.05.57.json` kullanılabilir.

### 6. `test_parse_receipt_tankar_sample_2`
**Hata:** `FileNotFoundError: .ocr_cache/WhatsApp Image 2026-03-27 at 09.05.57.json`

`09.05.57` kaydında Tankar fişi var ama `.ocr_cache/` dizininde değil — `.guidedReceipts/` ve `Receipts/` dizinlerinde `09.05.57.jpeg` görüntüsü var ama OCR cache'i hiç çalıştırılmamış.

**Düzeltme:** PaddleOCR kurulu ortamda `batch.py` çalıştırıp cache oluştur. Ya da testi `09.05.52.24.json` (mevcut Tankar fişi) ile güncelle.

---

## Başarılı Test

### `test_parse_receipt_bim_sample_2` ✅
`WhatsApp Image 2026-04-07 at 08.45.16.json` → YUMURTA 87.50 TL — tam doğru.

---

## Eksik Test Kapsamı

Test dosyası şu senaryoları hiç test etmiyor:
- METRO e-fatura parse
- Migros tartılı ürün parse
- BUENAS/restoran parse
- CafeGrubu parse
- FSREF parse
- `merge_weight_rows` (Durum A/B/C)
- `extract_date` çeşitli tarih formatları
- `parse_price` Türkçe format (2.537,47), İngilizce format (2537.47)
- market tespiti başarısız → fallback davranışı
