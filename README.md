# PTA Receipt Parser

Türkçe termal fiş görüntülerini OCR ile okuyup **hledger** veya **Excel** muhasebe defterine aktaran bir pipeline.

---

## Pipeline Genel Bakış

```
Fiş Görseli (jpg/png)
       │
       ▼
  [preProcess.py]          ← opsiyonel: döndürme, kontrast, crop
       │
       ▼
  [batch.py]               ← ana giriş noktası
       │
       ├─► [ocr_engine.py] ← PaddleOCR / Tesseract / Windows OCR
       │         │
       │         ▼
       │    .ocr_cache/         ← ham OCR sonuçları (JSON, tekrar kullanılır)
       │
       ├─► [parser.py]     ← koordinat tabanlı fiş ayrıştırıcı
       │         │
       │         ├── corrections.toml   ← OCR hata düzeltmeleri (exact match)
       │         └── stores.toml       ← market profilleri + evrensel kurallar
       │
       ├─► [rules.py]      ← ürün kategorileme
       │         │
       │         ├── rules.toml         ← elle yazılan kategorileme kuralları
       │         └── rules_learned.toml ← Claude'un öğrendikleri (otomatik)
       │
       ├─► [update_journal.py]  ← hledger journal çıktısı
       │         └── *.hledger
       │
       └─► [update_excel.py]   ← Excel double-entry defteri çıktısı
```

---

## Dosyalar

### Çekirdek Pipeline

| Dosya | Açıklama |
|---|---|
| `batch.py` | **Ana giriş noktası.** Klasördeki tüm fişleri tarar; OCR → parse → kategorize → journal günceller. |
| `ocr_engine.py` | OCR motor adaptörü. PaddleOCR, Tesseract ve Windows OCR için ortak arayüz. Sonuçları `.ocr_cache/` altında saklar. |
| `parser.py` | Koordinat tabanlı fiş ayrıştırıcı. OCR JSON'ından market, tarih, ürün listesi ve toplam çıkarır. |
| `rules.py` | Ürün adını `rules.toml` + `rules_learned.toml` kurallarına göre hledger account'una eşler. |
| `update_journal.py` | Parse sonucunu hledger journal formatına çevirip mevcut `.hledger` dosyasına yazar. |
| `update_excel.py` | Parse sonucunu proje Excel muhasebe defterine kalem kalem yazar. |
| `preProcess.py` | Görüntü ön işleme: döndürme düzeltme, perspektif, kontrast, keskinleştirme, crop. OCR öncesi kaliteyi artırır. |

### Konfigürasyon Dosyaları

| Dosya | Açıklama |
|---|---|
| `stores.toml` | Market profilleri (BİM, Migros, Metro, ...) ve evrensel kurallar (skip patterns, tarih kalıpları, isim temizleme). |
| `rules.toml` | Elle yazılan kategorileme kuralları. Ürün adı / market / tutar aralığına göre hledger account atar. |
| `rules_learned.toml` | Claude'un interaktif sorularla öğrendiği kategoriler. `rules.py` tarafından otomatik güncellenir. |
| `corrections.toml` | OCR hata düzeltme sözlüğü. Hatalı OCR metni → doğru metin. `build_corrections.py` ile güncellenir. |

### PPOCRLabel / Kalibrasyon

| Dosya | Açıklama |
|---|---|
| `import_labels.py` | PPOCRLabel'dan dışa aktarılan `Label.txt`'yi `.ocr_cache` formatına çevirir. Düzeltilmiş OCR verisini cache olarak kullanmak için. |
| `build_corrections.py` | PPOCRLabel `Cache.cach` (ham OCR) ile `Label.txt` (düzeltilmiş) karşılaştırarak `corrections.toml`'a yeni düzeltme çiftleri ekler. |
| `correct_labels.py` | Claude Haiku Vision ile crop görüntülerini okuyarak `Label.txt` transcription'larını otomatik düzeltir. |

### Destek Araçları

| Dosya | Açıklama |
|---|---|
| `snapshots.py` | Başarılı parse sonuçlarını kaydeder. Regresyon testi: kod değişikliklerinden sonra eski fişler hâlâ doğru parse ediliyor mu? |
| `llm_parser.py` | Claude API tabanlı alternatif parser. Regex yerine LLM kullanır; regex'in başaramadığı zor fişler için. |
| `generate_tesseract_cache.py` | Tüm fişler için Tesseract cache dosyaları oluşturur (motor karşılaştırması için). |

### Test Dosyaları

| Dosya | Açıklama |
|---|---|
| `test_parser.py` | `parser.py` için birim testler. |
| `test_new_files.py` | Yeni eklenen fiş görsellerini test parse eder ve çıktıyı gösterir. |
| `test_compare_engines.py` | Farklı OCR motorlarının aynı fiş üzerindeki sonuçlarını karşılaştırır. |
| `test_preprocess_compare.py` | Ön işleme adımlarının OCR kalitesine etkisini karşılaştırır. |

---

## Hizli Baslangic

### 1. Bagimliliklar

```bash
pip install paddleocr anthropic openpyxl pillow opencv-python numpy shapely
```

### 2. Fisleri Isle

```bash
# Tek klasordeki tum fisleri isle, journal'i guncelle
py batch.py Receipts/ muhasebe.hledger

# API key ile (Claude kategorileme sorulari icin)
py batch.py Receipts/ muhasebe.hledger --api-key sk-ant-...

# Gorselleri once on islemden gecir
py preProcess.py Receipts/
py batch.py .processedReceipts/ muhasebe.hledger
```

### 3. Tek Fis Test

```bash
# Ham OCR ciktisindan parse et
py parser.py .ocr_cache/bim_fisi.json

# hledger formatinda cikar
py parser.py .ocr_cache/bim_fisi.json --hledger
```

### 4. OCR Hata Duzeltme Is Akisi

```
PPOCRLabel'da fisleri ac
→ Hatali transkripsiyonlari duzelт
→ Kaydet (Label.txt guncellenir)
→ py build_corrections.py          ← yeni duzeltmeler corrections.toml'a eklenir
→ py import_labels.py              ← duzeltilmis cache'i .ocr_cache'e yaz
```

### 5. Regresyon Testi

```bash
py snapshots.py --regression
```

---

## Karar Agaci: Hata Nerede Duzeltilmeli?

```
Fis yanlis cikti verdi
        │
        ├─ OCR yanlis okudu (karakter hatasi: TARIH→TARİH, UISA→VISA)
        │         └─► PPOCRLabel'da duzelт → py build_corrections.py
        │
        ├─ Dogru okundu ama parser yanlis ayirdi (satir gruplaması, fiyat tespiti)
        │         └─► stores.toml duzenle (price_pattern, skip_patterns, y_tolerance...)
        │
        └─ Dogru parse edildi ama yanlis kategoriye girdi
                  └─► rules.toml duzenle veya Claude'a sor (rules_learned.toml)
```

---

## Desteklenen Marketler

`stores.toml` dosyasinda tanimli:

- **BIM** — normal mod
- **Migros / Market** — normal mod
- **Tankar / Akaryakit** — normal mod
- **METRO / ETRD GrosMarket** — two-line mod (urun adi ve fiyat ayri satirlarda)
- **FSREF CAN GIDA** — normal mod
- **BUENAS / Restoran** — normal mod
- **CAFEGURUP / Restoran** — normal mod

Yeni market eklemek icin `stores.toml`'a `[store.xxx]` blogu ekle.

---

## OCR Motorlari

`ocr_engine.py` uc motoru destekler:

| Motor | Kurulum | Not |
|---|---|---|
| **PaddleOCR** (varsayilan) | `pip install paddleocr` | En iyi `*` ve Turkce karakter performansi |
| **Tesseract** | `winget install UB-Mannheim.TesseractOCR` + `pip install pytesseract` | Turkce dil paketi gerekli |
| **Windows OCR** | `pip install winocr` | Kurulum gerektirmez; Windows 10/11 |

**Model:** `PP-OCRv5_mobile_rec` — server modeline gore `*` karakterini daha iyi tanir, Turkce harflerde daha az mutasyon yapar.
