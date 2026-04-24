# Fiş Bazlı Parse Analizi
**OCR:** PaddleOCR PP-OCRv5 mobile (cache'ten)  
**Toplam test fişi:** 21 (13 Belge_3_X + 8 WhatsApp)

---

## Sonuç Etiketleri
- ✅ Tam doğru — ürünler + tutar eşleşiyor  
- ⚠️ Kısmi — ürünler var ama tutar sapması veya OCR hataları  
- ❌ Başarısız — 0 ürün veya tamamen yanlış veri

---

## Belge 3 Serisi (Receipts/Belge 3_X.jpg)

### Belge 3_1 — FSREF CAN GIDA ✅
- Tarih: 2026-04-18 | Toplam: 390.00 TL
- 1 ürün: `ICECEK 390.00 TL`
- Gözlem: Ürün adında Türkçe karakter yok (İÇECEK yerine ICECEK). OCR hatası değil, muhtemelen fişte böyle yazıyor.
- Tutar eşleşiyor ✓

### Belge 3_2 — BUENAS / RESTORAN ❌
- Tarih: 2026-04-18 | Toplam: 2365.00 TL
- 0 ürün → **parse tamamen başarısız**
- **Neden:** `YIYECEK` adı ve fiyatı `*2365.00` farklı satırlarda. 
  - `YIYECEK` y≈808 → fiyatsız satır → skip
  - `%10 *2365.00` y≈868 → `^%\d+` skip pattern'e takılıyor
- Çözüm: Restoran formatında "isim satırı + fiyat satırı" birleştirme veya `^%\d+` pattern'ini daha dikkatli yaz

### Belge 3_3 — Bilinmeyen Market ⚠️ (Migros fallback)
- Tarih: 2026-04-18 | Toplam: 1210.00 TL
- 4 ürün, hesaplanan 1180 TL (fark: 30 TL)
- Market tespit edilemedi → Migros profiliyle fallback
- `Profiterol` 3x (OCR bir tanesini `Profrtеrol` okudu), `TavukgMuslü Kazandı` (OCR hatası)
- Gerçek market muhtemelen başka bir restoran/kafe — profil eklenmeli

### Belge 3_4 — Bilinmeyen Market ❌
- Tarih: yok | Toplam: bilinmiyor  
- 1 anlamsız ürün: `1 ISTENMIYYOR 10` fiyat 0.00 TL
- Market tespit edilemedi, Migros fallback
- Fiş muhtemelen okunaksız veya tamamen farklı bir format

### Belge 3_5 — METRO / ETRD GrosMarket ❌
- Tarih: 2025-12-21 | Toplam: 2777.63 TL (NET TOPLAM)
- 0 ürün → **METRO e-fatura formatı desteklenmiyor**
- **Neden:** METRO e-fatura, ürünü barkod+kod+isim formatında tek satırda gösteriyor:
  ```
  0804531710052 0007648 *TAS PIRINC MAKARNA 400G
  123,27 2ADX1*246,54 %1*249,00
  ```
  `price_pattern = r"^[\*x×](...)"` bu formatla eşleşmiyor.
- Çözüm: METRO için özel satır-çift ayrıştırıcı veya tüm receipt LLM'e göndermek

### Belge 3_6 — BİM ⚠️
- Tarih: 2025-12-13 | Toplam: 509.00 TL (fişte toplam yok, hesaplama eşleşme yapılamıyor)
- 7 ürün, hesaplanan 509.00 TL
- Gözlemler:
  - `SU 5L` (doğru, OCR `SU` + `5L` birleştirdi)
  - `ALISVERIS POSETi BiM` (doğru ama `İ→i`)
  - `ZENCEF0L 250G` (OCR 0→İ hatası: `ZENCEFİL` yerine `ZENCEF0L`)
  - Fişte toplam satırı olmadığından tutar doğrulama yapılamıyor

### Belge 3_7 — BİM ✅
- Tarih: 2026-03-30 | Toplam: 166.00 TL
- 3 ürün: YUMURTA, SANDViC EKMEGi, ALISVERiS POSETi
- Tutar eşleşiyor ✓
- OCR hataları: `İ→i`, `İ→0` (`SANDViC`, `BiM`) ama parse çalışıyor

### Belge 3_8 — CafeGrubu / Restoran ⚠️
- Tarih: 2025-12-26 | Toplam: 1139.00 TL (hesaplanan 4017.00 TL)
- 4 gerçek ürün + 3 hatalı ürün (JOPLAM, ödeme kodu satırları)
- **Sorunlar:**
  - `JOPLAM 1439.00 TL` → OCR TOPLAM'ı yanlış okumuş (`J→T`), skip pattern `r"^JOPLAM"` var ama match olmuyor (row_text kontrolünde)
  - `610 91.82 → 1010.00 TL` ve `020 71.50 → 429.00 TL` → ödeme kodu satırları ürün sayılıyor
  - Skip pattern `r"^\d{3}\s+\d+[\.,]\d{2}"` var ama `row_text` birleşimi nedeniyle head-match çalışmıyor olabilir

### Belge 3_9 — BİM ✅ (tutar) / ⚠️ (kalite)
- Tarih: 2025-12-21 | Toplam: 570.44 TL
- 17 ürün, tutar tam eşleşiyor ✓
- **OCR kalite sorunları:**
  - `ORTAKALKG` → `PORTAKAL KG` olmalı
  - `YVA` → `AYVA` (başında A kaybı)
  - `JMURTA 63-72G 15Li` → `YUMURTA` (Y→J)
  - `TIN SUT 1L DOST` → `ALTIN SUT` (baştaki AL kaybı)
  - `KT0V0TE K0TAPLARI` → `AKTİVİTE KİTAPLARI` (A kaybı, 0→İ)
  - Bazı ürünler 2-3 kez tekrarlıyor (OCR aynı satırı birden fazla algılamış?)

### Belge 3_10 — BİM ⚠️
- Tarih: 2025-12-13 | Toplam: 209.50 TL
- 2 ürün, hesaplanan 149.50 TL (**60 TL fark**)
- Eksik ürün: `EKMEK ODUN 60.00 TL` OCR cache'te görünüyor (`*60,00` var) ama parse edilemiyor
- **Neden araştırılmalı:** `EKMEK ODUN` y≈1129, `*60,00` y≈1084 → y sıralaması ters mi? OCR kutu koordinatları bozuk olabilir

### Belge 3_11 — BİM ⚠️
- Tarih: 2025-12-15 | Toplam: yok
- 2 ürün ama 1 yanlış: `% 20 449.17 89.83 → 539.00 TL`
- KDV özet satırı (`%20 449.17 89.83`) ürün olarak kaydedilmiş
- Skip pattern `r"^%\s*\d+(\s+%)?"` ile başlıyorsa yakalanıyor ama burada row_text `% 20 449.17 89.83` — baştaki `%` var, pattern eşleşmeli?

### Belge 3_12 — BİM ❌
- Tarih: 2025-12-15 | Toplam: yok
- 3 ürün ama 2 yanlış: `TOPLAH 419.00 TL` ve `Nakit 419.00 TL`
- `TOPLAM`'ın OCR hatası `TOPLAH` skip pattern'de yok
- `Nakit` payment satırı skip edilmiyor (skip pattern `r"^Nakit\b"` var ama büyük-küçük harf duyarlılığı?)
  - `re.IGNORECASE` kullanılıyor, `^nakit` eşleşmeli... belki row başka bir şeyle birleşmiş

### Belge 3_13 — Migros (DUFREL MARKET) ⚠️
- Tarih: 2025-12-10 | Toplam: 1600.00 TL
- Snapshot: 1 ürün (`HIGHIANDPARK12Y070CL 1600.00 TL`)
- Eski batch run'ında: 0 ürün (muhtemelen parser değişikliğinden önce)
- OCR tüm satırı tek blok olarak okumuş: `HIGHIANDPARK12Y070CL%20*1.600,00`
- `%20` → URL encoding temizleme ile boşluk oluyor, inline split çalışıyor

---

## WhatsApp Fişleri

### WhatsApp 2026-03-27 09:05:32 — BİM ⚠️
- Tarih: 2026-03-26 | Toplam: 333.07 TL
- 9 ürün, hesaplanan 293.02 TL (**40.05 TL fark**)
- **Eksik:** `ELMA STARKING (0.45kg × 89.00) = 40.05 TL` kayıp
  - OCR'da görünüyor: `0.45 kg X 89.00`, `*40.05`, `ELMA STARKING`
  - Muhtemelen weight merge logic'te Durum C (price taşma) hatalı
- İsim `PATATES 1 (0.74kg × 19.75)` — trailing `1` kaldırılmalı (`%1.` KDV kodu)
- Snapshot testinde `receipt.items[0].name` beklentisi kırık: `PİNGU` vs beklenen `PİNGUI`

### WhatsApp 2026-04-07 08:45:16 — BİM ✅
- Tarih: 2026-04-06 | Toplam: 87.50 TL
- 1 ürün: `YUMURTA 10 LU 63-73G`
- Tutar tam eşleşiyor ✓

### WhatsApp 2026-04-09 15:28:49 — Tankar ⚠️
- Tarih: 2026-03-09 | Toplam: 2537.47 TL
- 1 ürün: `MOTORINSVPD 2537.47 TL` (weight merge çalışmamış)
- Beklenen: `MOTORIN SVPD (38.4LTX × 2537.00) 2537.00 TL` gibi bir format
- Toplam doğru ama ürün adı/format bozuk

### WhatsApp 2026-04-09 15:52:24 — Tankar ✅
- Tarih: 2026-03-11 | Toplam: 250.00 TL
- 1 ürün: `YIKAMA(OTOMATIK) 250.00 TL`
- Tutar eşleşiyor ✓
- Kategori sorunu: `Gider:Market` yerine `Gider:Otomobil:ArabaYikama` olmalı

### WhatsApp 2026-04-13 13:20:46(1) — Migros ❌
- Tarih: 2026-04-12 | Toplam: 480.00 TL
- 1 anlamsız ürün: `GIDA 480.00 TL`
- OCR, çok ürünlü Migros fişini tek "GIDA" bloğu olarak okumuş
- Görüntü kalitesi çok düşük olabilir veya fiş kağıdı katlanmış

### WhatsApp 2026-04-13 13:20:46 — Migros ⚠️
- Tarih: 2025-04-11 | Toplam: 1760.67 TL (hesaplanan 1810.65 TL)
- 10 ürün, **49.98 TL fark** (ASYA LALE İLKBAHAR çifte kayıt + ESMALT DEPOZITO fiyat hatası?)
- OCR hataları: `JSMANTYE` (J→Y), `ESMALTS DEPOZITOLU` gibi
- Market tespiti: DUFREL/MIGROS ama header'da identifier yok → "Market tespit edilemedi" → Migros fallback
- Tarih 2025-04-11 (yıl yanlış olabilir, fiş aslında 2026)

### WhatsApp 2026-04-13 13:20:47(1) — Migros ✅
- Tarih: 2026-04-12 | Toplam: 500.92 TL
- 5 ürün: EFES GLUTENSIZ BIRA (×2), DOMATES SALKIMKG (tartılı), KESTANE MANTARI, PLASTIK POSET
- Tutar tam eşleşiyor ✓
- Tartılı ürün doğru parse edilmiş: `DOMATESSALKIMKG (0.68kg × 149.95) 101.97 TL`

### WhatsApp 2026-04-13 13:20:47 — Migros ❌
- Tarih: 2026-04-12 | Toplam: 1580.00 TL
- 1 anlamsız ürün: `TEMEL GIDA 1580.00 TL`
- Aynı sorun: OCR tüm fişi tek metin bloğuna birleştirmiş

---

## Genel OCR Hata Desenleri

| Hata | Örnek | Sıklık |
|------|-------|--------|
| İ → i | `BiM`, `POSETi` | Çok yaygın |
| İ → 0 | `B0M`, `PALET0` | Yaygın |
| Harf başında kayıp | `YVA` (AYVA), `LMER` (İLMER) | Orta |
| J/Y karışması | `JOPLAM` (TOPLAM), `JMURTA` (YUMURTA) | Nadir |
| Harf/rakam karışması | `ZENCEF0L`, `0SMANIYE` | Yaygın |
| Gürültü detection'ları | `'S'`, `'ç'`, `'ins'`, `'品'` | Yaygın (conf<0.60 ile filtreleniyor) |
