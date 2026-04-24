# Parser Teknik Detaylar ve Köklü Sorunlar
**Tarih:** 2026-04-22

---

## 1. METRO e-Fatura Format Analizi

METRO OCR çıktısı (Belge 3_5) incelendiğinde ürün satırları şu formatta:

```
0804531710052 0007648 *TAS PIRINC MAKARNA 400G   [barcode + code + *name]
123,27 2ADX1*246,54 %1*249,00                      [price_info]

8696236118446 0396783 *FINELIFE CAM AGACI 84G YESIL
*188.02 *1ADX1*188,02%1*189.90

86962361185450396787 FINELIFEKARDAN ADAM CIK0 84G  
*188.02*1ADX1*188.02%1*189.90
```

Mevcut `price_pattern = r"^[\*x×](-?[\d\.]+,\d{2}|-?[\d]+[\.,]\d{2})$"` bu formatla eşleşmiyor çünkü:
- Fiyat satırı tek bir sayı değil, miktar*birim fiyat*KDV karışık bir şey
- Ürün adı barkodla aynı detection'da

**Gerekli değişiklik:**
METRO profili için özel bir ürün çiftleme mantığı yazılmalı: bir detection'da barkod+isim, sonraki detection'da fiyat. Veya `llm_parser.py`'e yönlendirme.

Alternatif: `NET TOPLAM *2.777,63` satırını doğrudan tek kalem olarak al (tüm METRO alışverişini bir bütün olarak yaz). Kullanım kararı olarak hâlâ anlamlı olabilir.

---

## 2. BUENAS/Restoran Format Analizi

BUENAS OCR çıktısı (Belge 3_2):
```
YIYECEK        y≈808   [ürün adı]
%10            y≈860   [KDV kodu]
*2365.00       y≈868   [fiyat]
TOPKDV         y≈969   [atlansın]
*215.00        y≈1020  [atlansın]
TOPLAM         y≈1046  [toplam]
*2365.00       y≈1094  [toplam değeri]
```

y_tolerance=15 ile `%10` ve `*2365.00` farklı satırlara düşüyor.
- Satır 1: `[YIYECEK]` — fiyat yok → skip
- Satır 2: `[%10]` — `^%\d+` skip pattern'e takılıyor → skip
- Satır 3: `[*2365.00]` — isim yok → skip

**Çözüm A (Basit):** `%NN` veya `%NN ` ile başlayan bir satırın arkasından fiyat geliyorsa, önceki fiyatsız satırla birleştir (name row look-back).

**Çözüm B (Kapsamlı):** Restoran profilleri için `name_row_then_price_row` modunu destekle. Ardışık `[name]` ve `[%KDV *fiyat]` satır çiftlerini birleştir.

---

## 3. BİM Belge 3_10 — Eksik Ürün Sorunu

OCR detection'ları (y sıralaması):
```
*49,50    y≈1018  [TURK KAHVESI fiyatı — DÜZ: önce fiyat?]
%01       y≈1040
TURK KAHVESI 100G AE  y≈1070
*60,00    y≈1084  [EKMEK ODUN fiyatı]
%01       y≈1101
EKMEK ODUN  y≈1129  [ürün adı]
*100,00   y≈1143  [TUVALET KAGIDI fiyatı]
%20       y≈1158
TUVALET KAGIDI 12Li  y≈1183
```

Bu ilginç bir yapı: bazı BİM fişlerinde fiyat (`*49,50`) ürün adından (`TURK KAHVESI`) ÖNCE geliyor (Y koordinatı daha küçük). y_tolerance=20 ile bunların aynı satıra düşüp düşmediği bağlama göre değişiyor.

`EKMEK ODUN` (y≈1129) ve `*60,00` (y≈1084) arasındaki fark 45px — y_tolerance=20 içinde değil, farklı satıra düşüyorlar. Fiyat satırı üstte, isim altında → `first_price_idx = len(row)-1` mantığı tersine işliyor.

Bu, PaddleOCR'ın bazı fişlerde item-price düzenini Y ekseni bazında ters sıraladığını gösteriyor. **Tespit edilmesi zor ve düzeltilmesi riskli bir durum.**

---

## 4. batch.py — ocr_engine.py Tutarsızlığı

batch.py'de inline OCR kodu:
```python
ocr = PaddleOCR(
    use_textline_orientation=False,  # ← batch.py
    text_det_unclip_ratio=1.6,
    text_det_box_thresh=0.5,
    text_det_thresh=0.3,
    use_doc_unwarping=False,
)
```

ocr_engine.py'de:
```python
ocr = PaddleOCR(
    use_textline_orientation=True,   # ← ocr_engine.py
    text_det_unclip_ratio=1.6,
    text_det_box_thresh=0.5,
    text_det_thresh=0.3,
    use_doc_unwarping=True,          # ← farklı
)
```

Cache key'leri de farklı:
- batch.py: `{stem}.json`
- ocr_engine.py: `{stem}_{engine}.json`

Bu nedenle ikisi birbirinin cache'ini okuyamıyor. `ocr_engine.py`'nin public API'si (`run_ocr` + `load_engine`) tercih edilmeli, batch.py bu API'yi kullanmalı.

---

## 5. Snapshot Tutarsızlığı

`snapshots.json` bazı fişlerde "doğru" sonuçları saklamış (geçmiş çalıştırmalardan) ve şu an parse sonuçları farklı. Örn. `Belge 3_13.json` snapshot'ta 1 item var ama güncel batch run 0 item gösteriyor.

Snapshot sistemi, parser değişikliklerini test etmek için değerli. Ancak mevcut durumda bazı snapshot'lar stale — gerçek beklentileri yansıtmıyor.

**Öneri:** Her fiş için ground truth tablosu oluşturulmalı (beklenen item sayısı, beklenen toplam, bilinen OCR sapmaları). Snapshot mekanizması bu ground truth'a göre değil, "önceki çalıştırmayla aynı mı" kontrolüne göre çalışıyor — regression testing için iyi, ground truth validation için yetersiz.

---

## 6. Confidence Eşiği ve Gürültü

`group_into_rows`: `cleaned = [d for d in detections if d.confidence >= 0.60]`

BİM Belge 3_1 (`WhatsApp Image 2026-03-27`) OCR incelemesinde `conf < 0.60` olan gürültü detections'ları:
```
'uhyCl'   conf=0.38
'M80e'    conf=0.38
'mose'    conf=0.38
'3lCwro'  conf=0.43
'品'       conf=0.11
```

Bu düşük confidence'lı detections'lar zaten filtreleniyor ama `'1'` (conf=0.51), `'2'` (conf=0.42) gibi rakamlar eşiğin altında olduğundan filtreleniyor. Bu iyi bir davranış.

---

## 7. Tartılı Ürün Kaybı — WhatsApp 09:05:32

ELMA STARKING (40.05 TL) kayboluyor. OCR'da:
```
0.45 kg X 89.00     y≈1071
'$1.'               y≈1093  [conf=0.62, gürültü — filtreleniyor]
*40.05              y≈1087
ELMA STARKING       y≈1102
```

`0.45 kg X 89.00` (y=1071) weight satırı. Sonraki satır: `*40.05` (y=1087) + `ELMA STARKING` (y=1102).

`merge_weight_rows` Durum A kontrolü:
- `row_has_price(rows[i+1])` → `*40.05` ve `ELMA STARKING` aynı satırda mı?
- Eğer y_tolerance=20 ile `*40.05` (y≈1093) ve `ELMA STARKING` (y≈1113) aynı satıra düşüyorsa: 2-satır merge (Durum A, 2 satır) → çalışmalı
- Eğer düşmüyorsa: 3-satır merge (Durum A, 3 satır) → `rows[i+2]` kontrol edilmeli

Görünüşe göre `$1.` (conf=0.62 — eşiğin üstünde!) detection'ı araya giriyor ve satır gruplama bozuluyor. `$1.` skip pattern'de var (`r"^\$\d*\.?$"`) ama `group_into_rows` işleminden SONRA skip kontrol yapılıyor. Skip pattern ile `group_into_rows` işleminden ÖNCE temizleme yapılsaydı bu sorun olmazdı.
