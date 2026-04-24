# PTAReceiptParser — Genel Durum Raporu
**Tarih:** 2026-04-22  
**Branch:** claude/review-project-notes-GHH7S  
**Analiz yöntemi:** OCR cache'lerinden parse testi, test suite çalıştırma, OCR detection verilerinin manuel incelenmesi

---

## Özet Skorkart

| Kategori | Durum |
|----------|-------|
| Desteklenen mağaza sayısı | 7 (BİM, Migros, Tankar, METRO, FSREF, BUENAS, CafeGrubu) |
| Test fişi sayısı | 21 |
| Tam doğru (tutar eşleşiyor) | ~8 |
| Kısmi (ürünler var, tutar sapma) | ~6 |
| Başarısız (0 ürün veya anlamsız veri) | ~7 |
| Mevcut unit test durumu | 6 hata / 7 test |

---

## Mimari Özeti

```
Görüntü → [preProcess.py] → [OCR Engine] → .ocr_cache/ → [parser.py] → Receipt
                                                                              ↓
                                                                      [rules.py]
                                                                              ↓
                                                               [update_journal.py] / [update_excel.py]
```

**Güçlü Yönler:**
- OCR cache sistemi çok iyi — aynı fiş tekrar taranmıyor
- Tartılı/kg ürün birleştirme mantığı (Durum A/B/C) karmaşık ama çalışıyor
- Snapshot regression testi fikri sağlam
- rules.toml + rules_learned.toml ayrımı temiz
- Çoklu çıktı (hledger + Excel) desteği iyi tasarlanmış

**Zayıf Yönler:**
- batch.py ile ocr_engine.py'de benzer OCR kodu var (senkronize değil)
- METRO e-fatura formatı hiç çalışmıyor
- BUENAS/restoran fişleri hiç çalışmıyor
- Unit testler bozuk (Detection dataclass değişti, cache eksik)
- Windows'ta emoji karakterler UnicodeEncodeError veriyor

---

## Kritik Eksiklikler

### 1. METRO e-Fatura Formatı Desteklenmiyor
METRO'nun e-fatura çıktısı tamamen farklı bir layout'a sahip. Ürün satırları barkod + ürün adını aynı satırda gösteriyor, fiyat bilgisi ise farklı bir formatta bir sonraki satırda yer alıyor. Mevcut `price_pattern` bu formatla eşleşmiyor.

### 2. BUENAS/Restoran Fişlerinde 0 Ürün
Restoran fişlerinde ürün adı (ör. YIYECEK) ayrı bir satırda, KDV kodu ve fiyat (`%10 *2365.00`) ise bir sonraki satırda yer alıyor. Parser bu iki satırı birbirine bağlayamıyor. Üstelik `%10` ile başlayan satır COMMON_SKIP_PATTERNS tarafından siliniyor ve fiyat kayboluyor.

### 3. 5 Unit Test Bozuk
- `Detection` dataclass'ına `bbox` alanı eklendi ama testler güncellenmedi (3 test)
- İki Tankar cache dosyası eksik (2 test)
- BIM isim beklentisi kırık: `'KEKCİK.KAP30G PİNGU'` beklenen `'KEKCİK.KAP30G PİNGUI'` (1 test)

---

## İkincil Sorunlar

### 4. batch.py'de DEBUG print'leri kaldırılmamış
```python
print(f"  DEBUG: predict çağrılıyor...")
print(f"  DEBUG: predict bitti, {len(result)} sonuç")
```

### 5. Windows Unicode Hatası
Emoji karakterler (`⚠️`, `📂` vb.) Windows cp1254 encoding'de `UnicodeEncodeError` veriyor. `sys.stdout.reconfigure(encoding='utf-8')` veya emojisiz alternatifler gerekli.

### 6. batch.py ve ocr_engine.py Senkronize Değil
- `batch.py::get_ocr_engine()` → `use_textline_orientation=False`
- `ocr_engine.py::load_paddle()` → `use_textline_orientation=True`
- Cache key formatları farklı: batch.py `{stem}.json`, ocr_engine.py `{stem}_{engine}.json`
- İki ayrı implementation aynı işi yapıyor, biri tercih edilmeli

### 7. total is None → Format Crash
`batch.py:358`: `f"receipt.total:.2f TL"` — `receipt.total` None olduğunda `TypeError` fırlatır. Sadece `--hledger` ile çalıştırıldığında tetiklenir.

### 8. Kural Eksiklikleri (rules.toml)
- `"SU "` — trailing space olan kural (kırılgan)
- `"YAG "` — trailing space olan kural (kırılgan)
- YIKAMA(OTOMATIK) Tankar fişi → `Gider:Market` yerine `Gider:Otomobil:ArabaYikama` olmalı
- `LEGO` için kural yok → `Gider:Bilinmeyen`

### 9. CafeGrubu (Belge 3_8) Gereksiz Satırlar Yakalıyor
`JOPLAM` (TOPLAM OCR hatası), `610 91.82`, `020 71.50` gibi ödeme kodu satırları ürün olarak parse ediliyor.
- `r"^TOP(LAH|PLAH)?$"` skip pattern var ama `JOPLAM` yakalanamıyor
- `r"^\d{3}\s+\d+[\.,]\d{2}"` skip pattern var ama row text birleşik olduğunda yakalanmıyor

### 10. Migros Bazı Fişlerde Tek Kalem
- `WhatsApp 13:20:46(1)`: Tüm alışveriş tek "GIDA 480 TL" olarak görünüyor
- `WhatsApp 13:20:47`: Tüm alışveriş tek "TEMEL GIDA 1580 TL" olarak görünüyor
- OCR, bu fişlerin görüntü kalitesinden dolayı tüm ürün satırlarını tek bir metin bloğuna birleştirmiş olabilir

---

## Öneri Sıralaması (Etki/Kolaylık)

1. **Unit testleri düzelt** — Detection bbox ekle, eksik cache dosyalarını oluştur/atla (kolay, yüksek etki)
2. **batch.py DEBUG printleri temizle** — 2 satır sil (kolay)
3. **total is None crash'i düzelt** — `f"receipt.total:.2f TL"` → `f"{receipt.total or 0:.2f} TL"` (kolay)
4. **BUENAS parser düzelt** — `%NN` satırına özel ürün-fiyat birleştirme mantığı (orta zorluk)
5. **JOPLAM skip pattern** — `r"^[JT]OPLAM"` → TOPLAM OCR hatalarını da kapat (kolay)
6. **Ödeme kodu satırı skip** — `r"^\d{3}\s+\d+[\.,]\d{2}\s*$"` pattern'ini tam row_text için çalıştır (orta)
7. **METRO formatı** — Tamamen farklı bir parser veya LLM fallback gerekli (zor)
8. **batch.py + ocr_engine.py birleştir** — ocr_engine.py'nin public API'sini kullan (orta)
