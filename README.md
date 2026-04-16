# PTAReceiptParser

**Plain Text Accounting Receipt Parser** — Türkçe market fişlerini OCR ile okuyup hledger journal ve/veya Excel muhasebe defterine kalem kalem yazar.

```
Fotoğraf → PaddleOCR → Parse → Kategorize → hledger ve/veya Excel güncelle
```

---

## Özellikler

- **Local-first** — bulut OCR yok, abonelik yok; modeller ilk çalıştırmada indirilir
- **Çift çıktı** — aynı fişi hem hledger journal hem de Excel double-entry defterine yazabilir; kanallar bağımsız seçilir
- **Kural motoru** — regex tabanlı, önce öğrenilmiş kurallar, sonra `rules.toml`
- **Oto-öğrenme** — bilinmeyen ürünler için verilen cevaplar `rules_learned.toml`'a kaydedilir, bir daha sorulmaz
- **Claude API** — bilinmeyen ürünler Claude'a gönderilebilir (opsiyonel)
- **Ağırlık satırları** — `0.74kg × 19.75` formatını doğru parçalar
- **OCR önbellek** — `.ocr_cache/` klasörüne yazılır; aynı fiş tekrar taranmaz
- **Snapshot** — parse sonucu kaydedilir; ileride yeniden çalıştırıldığında fark varsa uyarır

---

## Nasıl Çalışır

### hledger — Önce / Sonra

```
2026-03-26 BİM
    gider:market                              333.07 TRY
    Borçlar:kart                             -333.07 TRY
```

↓

```
2026-03-26 BİM
    gider:market:gida:atistirmalik            26.00 TRY  ; KEKÇİK PİNGUİ
    gider:market:gida:kuru-gida               29.00 TRY  ; KABARTMA TOZU
    gider:market:gida:atistirmalik            21.50 TRY  ; ŞEKERLİ VANİLİN
    gider:kitap                               65.00 TRY  ; HİKAYE KİTAPLARI
    gider:market:poset                         1.00 TRY  ; ALIŞVERİŞ POŞETİ
    gider:market:gida:sebze                   14.62 TRY  ; PATATES (0.74kg × 19.75)
    gider:market:gida:sebze                   47.76 TRY  ; BİBER KAPYA (0.24kg × 199.00)
    gider:market:gida:meyve                   23.14 TRY  ; ELMA GOLDEN (0.26kg × 89.00)
    gider:market:gida:meyve                   40.05 TRY  ; ELMA STARKİNG (0.45kg × 89.00)
    Borçlar:kart                            -333.07 TRY
```

### Excel — Double-Entry Defteri

Excel'de her transaction iki bölümden oluşur:

| Satır | A | H (Hesap) | I (Tutar) | L (Not) |
|-------|---|-----------|-----------|---------|
| 1. (from) | 26.03.2026 | `Borçlar:kart` | `-333,07` | — |
| 2. (to) | — | `gider:market:gida:meyve` | `23,14` | ELMA GOLDEN |
| 3. (to) | — | `gider:market:gida:sebze` | `14,62` | PATATES |
| … | — | … | … | … |

Eşleştirme tarih + tutar (±0,02 TL) ile yapılır. Mevcut to-account satırları silinip yenileri eklenir; from-account satırı dokunulmadan korunur.

---

## Kurulum

```bash
pip install paddleocr pillow numpy anthropic openpyxl
```

> PaddleOCR ilk çalıştırmada model dosyalarını indirir (~300 MB).

Gereksinimler:
- Python 3.12+
- Windows 11 / Linux

---

## Dosya Yapısı

```
PTAReceiptParser/
├── batch.py            # Ana giriş noktası — klasördeki fişleri toplu işler
├── parser.py           # OCR JSON → Receipt nesnesi (mağaza profilleri burada)
├── rules.py            # Kural motoru + oto-öğrenme
├── update_journal.py   # hledger journal eşleştirme ve yerinde güncelleme
├── update_excel.py     # Excel double-entry defteri eşleştirme ve güncelleme
├── snapshots.py        # OCR snapshot kayıt/karşılaştırma
├── rules.toml          # Kategori kuralları (elle düzenlenir)
└── rules_learned.toml  # Claude/manuel cevaplardan otomatik üretilir
```

---

## Kullanım

### Sadece OCR + kategorize (güncelleme yok)
```bash
python batch.py fisler/
```

### hledger güncelle
```bash
python batch.py fisler/ --hledger butce.hledger
```

### Excel güncelle
```bash
python batch.py fisler/ --excel butce.xlsx
python batch.py fisler/ --excel butce.xlsx --sheet Harcamalar
```

### Her ikisini birden güncelle
```bash
python batch.py fisler/ --hledger butce.hledger --excel butce.xlsx
```

### Claude API ile (bilinmeyen ürünler otomatik kategorilenir)
```bash
python batch.py fisler/ --hledger butce.hledger --api-key sk-ant-...
# veya
export ANTHROPIC_API_KEY=sk-ant-...
python batch.py fisler/ --hledger butce.hledger
```

### Tek fiş parse testi
```bash
python parser.py fisler/bim_20260326.jpg --debug
```

Seçenekler özeti:

| Bayrak | Açıklama |
|--------|----------|
| `--hledger <dosya>` | hledger journal dosyası |
| `--excel <dosya>` | Excel dosyası (`.xlsx` / `.xlsm`) |
| `--sheet <ad>` | Excel sheet adı (default: ilk sheet) |
| `--api-key <key>` | Anthropic API anahtarı |

---

## Kategori Kuralları

Kurallar yukarıdan aşağıya işlenir; ilk eşleşen kazanır. Tüm kriterler AND'dir.

```toml
# rules.toml

[[rule]]
item    = "ELMA|ARMUT|MUZ"
account = "gider:market:gida:meyve"

[[rule]]
store      = "OPET|SHELL|BP"
amount_min = 500.0
account    = "gider:ulasim:yakit"

[[rule]]
store      = "OPET|SHELL|BP"
amount_max = 499.99
account    = "gider:market:diger"
```

Kullanılabilir kriterler: `item`, `store`, `amount_min`, `amount_max`.

Bilinmeyen ürünler interaktif olarak sorulur (veya Claude API varsa otomatik gönderilir). Cevaplar `rules_learned.toml`'a kaydedilir ve `rules.toml`'dan önce yüklenir.

---

## Yeni Mağaza Ekleme

`parser.py` içindeki `STORE_PROFILES` sözlüğüne profil ekle:

```python
"mystore": {
    "name": "MyStore",
    "identifiers": [r"MYSTORE A\.S"],
    "layout": {
        "price_x_min": 450,   # isim sütunu ile fiyat sütununu ayıran x eşiği
        "y_tolerance": 18,    # aynı satır gruplaması için piksel toleransı
        "header_y_max": 640,  # ürünlerin başladığı y değeri
        "footer_y_min": 1150, # toplamların başladığı y değeri
    },
    "price_pattern": r"^\*?(\d+[\.,]\d{2})$",
    "skip_patterns": [r"^KDV", r"^TOPLAM KDV"],
    "total_pattern": r"^TOPLAM",
    "date_pattern":  r"(\d{2}\.\d{2}\.\d{4})\s*\d{2}:\d{2}",
    "name_cleanup":  [(r"\s+%\d+\.?\s*$", "")],
},
```

Yeni profil kalibre etmenin en kolay yolu: örnek bir fişe OCR çalıştırıp `.ocr_cache/` içindeki JSON'ı incelemek.

---

## Desteklenen Mağazalar

| Mağaza | Tür | Durum |
|--------|-----|-------|
| BİM | Market | ✅ |
| Migros | Market | ✅ |
| TANKAR | Yakıt / Oto yıkama | ✅ |

Yeni mağaza profilleriyle PR'lar memnuniyetle karşılanır.

---

## Lisans

MIT
