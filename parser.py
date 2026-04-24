"""
Fiş Parser - Koordinat tabanlı, çoklu market desteği
Kullanım: python parser.py ocr_output.json [--hledger] [--debug]
"""

import json
import re
import sys
import os
from dataclasses import dataclass
from typing import Optional
from shapely.geometry import Polygon

# Global debug flag
DEBUG = False


# ── Veri yapıları ─────────────────────────────────────────────────────────────

@dataclass
class Detection:
    text: str
    confidence: float
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    y_center: float
    bbox: list[list[float]]


@dataclass
class ReceiptItem:
    name: str
    amount: float
    raw_name: str  # OCR'dan gelen ham isim


@dataclass
class Receipt:
    store: str
    date: Optional[str]
    items: list[ReceiptItem]
    total: Optional[float]
    raw_detections: list[Detection]


# ── Market profilleri ──────────────────────────────────────────────────────────
COMMON_SKIP_PATTERNS=[
    r"^TCKN",
    r"^ETTN",
    r"^FATURA",
    r"^E-Arsiv",
    r"^Sira No",
    r"^Buyuk Mukellef",
    r"^\d{15,}$",              # barkod numaraları
    r"^TOPLAM\s*K[OD]V",        # "TOPLAM KDV" / "TOPLAMKDV" / "TOPLAMKOV" (KDV özeti, asıl toplam değil)
    r"^Odenecek",
    r"^Banka",
    r"^GARANTI",
    r"^Onay",
    r"^Ref\.No",
    r"^KDV\s+(MATRAH|TUTAR|DAHIL)",
    r"^(KDV|MATRAH|KOV TUTAR|KOV DAH)",
    r"TOPKDV",
    r"^POS:",
    r"^GS No",
    r"^\d{2}\.\d{2}\.\d{4}",  # tarih satırları (ödeme bölümü)
    r"^#+\s",                  # ## ile başlayan POS sistem satırları (ör. ## BarkoPOS-2.0.14.70)
    r"^[BI]:[\d]+",            # B:706 S:9638 gibi
    r"^\d{4,6}\*+\d{4}$",     # kart numarası
    r"^%[\s\d]+\s*$",           # %1, %20, % 2 0 gibi tek başına KDV kodu satırları

    r"^\$\d*\.?$",            # $0. — mobile OCR gürültüsü
    r"^[\$各\\]",           # 各1 $c}$ gibi saçma karakterler
    r"^\d+\.$",               # sadece "1."
    r"^\d+[\.,]\d{2}$",       # sadece sayı (KDV tablo satırları)
    r"^\d+[\.,]\d{2}\s+\d+[\.,]\d{2}",  # KDV tablo satırı
    r"^\([\d）]+\)$",
    r"^AFATOPLAM",  # Ara toplam (Tankar)
    r"^ARATOPLAM",
    r"^TOP(LAH|PLAH)?$",  # Sadece "TOP" / "TOPLAH" / "TOPPLAH" label'ı
    r"^JOPLAM",             # OCR hatası: "TOPLAM" → "JOPLAM"
    r"^KDV$",  # KDV satırı (TOPLAM değil)
    r"^SN:",
    r"^EFT-[PF]OS",  # ödeme yöntemi satırı (toplam değil)
    r"^Nakit\b",           # "Nakit" ödeme yöntemi
    r"^Kredi\s+Kartı\b",   # "Kredi Kartı" ödeme yöntemi
    r"^BROT\s+GIDA",  # gıda-dışı KDV özet satırı
    r"^BRUT\s+GIDA",  # Brut gıda satırı (METRO e-Fatura)
    r"^(NET|ODEME|ÖDEM|ÖDEME)\s+TUTARI",  # Özet tutarı satırları
    r"^\d{3}\s+\d+[\.,]\d{2}",  # Ödeme kodu + tutar: "020 71.50", "610 91.82"
    # METRO e-Fatura / genel fatura başlık satırları
    r"^MERKEZ:",
    r"^OLUŞTURMA\s+TARİHİ",
    r"^FİİLİ\s+SEVK\s+TARİHİ",
    r"^METRO\s+FATURA\s+NO",
    r"^İŞLEM\s+NO",
    r"^KASİYER\s+NO",
    r"^MÜŞTERI\s+NO",
    r"^VKN",
    r"^D\d+\s+[A-Z]",        # METRO barkod+ürün: D123456 XYZ
    r"^[0-9]{8,}[A-Z]",      # barkod+harf karması
    # İletişim / web satırları
    r"^TEL[:\s：]",
    r"^FAX[:\s：]",
    r"^www\.",
    r"^ww\.",
    # Kart / ödeme detay satırları
    r"^KREDI\s+KARTI",
    r"^KART\s+NO",
    r"^RRN:",
    r"^ACQUİRER",
    r"^TUTAR\s+KARŞILAŞTIRI",
    r"^HİZMET\s+ALINDI",
    r"^BU\s+BEL",
]

PRICE_PREFIX_CLEANUP = [
    (r"[￥¥$§¢€£₹₽]", "*"),  # OCR garip para birimi → *
    (r"%[0-9A-Fa-f]{2}", " "),  # URL encoding: %20 → boşluk (inline fiyat satırlarında)
]

COMMON_NAME_CLEANUPS = [
    (r"[\u4e00-\u9fff\u3400-\u4dbf]+",""), # Çince falan temizliği
    (r"\s{2,}", " "),                       # çift boşluk
    # OCR, büyük İ'yi küçük i olarak okuyabiliyor; tamamen büyük harf tokenlerinde düzelt
    (r'\S+', lambda m: m.group().replace('i', 'İ')
             if not re.search(r'[a-zçğşüö]', m.group().replace('i', ''))
             else m.group()),
    # OCR, harf bağlamında 0 (sıfır) ile O (harf) karıştırıyor: T0ZU → TOZU
    (r'(?<=[A-ZÇĞŞÜÖİ])0(?=[A-ZÇĞŞÜÖİ])', 'O'),
]

COMMON_DATE_PATTERNS = [
    (r"^.*:?(\d{2}([\.\-\/\\])\d{2}\2\d{4})(\s*\d{2}:\d{2})?"),  # 31.12.2026, 31-12-2026
    (r"^.*?(\d{2}\.(\d{2})\d{4})\b"),                              # 31.122026 (iki delimiter yok, OCR birleştirmiş)
    (r"(\d{2})([\.\-]?)(\d{2})\2(\d{4})"),                        # Boşluklu tarihler: "31 . 12 . 2026"
    (r"^.*?(\d{2}([\.\-\/])\d{2}\2\d{2})\b"),                    # 18/04/26 — 2 haneli yıl (20xx)
]

STORE_PROFILES = {
    "bim": {
        "name": "BİM",
        "identifiers": [
            r"BIM BIRLESIK",
            r"BİM BİRLEŞİK",
            r"BiM\s+Bi[Rr]",   # OCR bozukluğu: BiM BiRIESiK / BiRLESiK
            r"SIM BIRLESIK",    # S↔B OCR karışması
            r"BIM A\.S",
        ],
        "layout": {
            # Aynı satır toleransı (piksel)
            "y_tolerance": 20,
            # Header bölgesi: bu Y'nin altından itibaren ürünler başlar
            "header_y_max": 640,
            # Footer bölgesi: bu Y'den sonrası toplam/KDV/banka bilgisi
            "footer_y_min": 9999,
        },
        "price_pattern": r"^\*(\d+[\.,]\d{2})$",
        "skip_patterns": COMMON_SKIP_PATTERNS + [
            r"^\([\d）]+\)$"          # (1） gibi
        ],
        "total_pattern": r"^(Odenecek K[OD]V Dahil|TOPLAM(?!\s*K[OD]V)|KRED[iİI] KARTI)",
        "date_pattern": COMMON_DATE_PATTERNS,  # boşluksuz da yakala, arkasından saat de gelebilir.
        # Ürün adı temizleme
        "name_cleanup": COMMON_NAME_CLEANUPS + [
            (r"^[=\-]+\s*", ""),                    # baştaki = veya - OCR kalıntısı
            (r"\s*(?:\d+%|[\$%°]\d*\.?)\s*$", ""),     # sondaki KDV kodu: 10% veya %20 veya $0. veya °1
            (r"\s+\$\\.*?\$\s*$", ""),              # $\1c}$ gibi LaTeX artığı
            (r"\s+\\?\d+\.\s*$", ""),               # sondaki OCR artığı: \11. gibi
            (r"^(\d+[\.,]\d+)\s*kg\s*[Xx×]\s*(\d+[\.,]\d+)\s+", r"(\1kg × \2) "),  # öndeki kg bilgisi
        ],
    },
    "migros": {
        "name": "Migros / Market",
        "identifiers": [
            r"MIGROS",
            r"MİGROS",
            r"HAKAN KARACA",
            r"CAN MARKET",
            r"DUFREL",              # Migros şube markası
            r"GIDA\s+SAN",         # Gıda şirketleri
        ],
        "layout": {
            "y_tolerance": 15,
            "header_y_max": 900,    # Belge 3_13 gibi şubeler Y=800-900'de header
            "footer_y_min": 9999,
        },
        "price_pattern": r"^.*\*(-?[\d]+\.[\d]+,\d{2}|-?[\d\.]+,\d{2}|-?[\d]+[\.,]\d{2}|-?[\d]{3,})$",
        "skip_patterns": COMMON_SKIP_PATTERNS + [
            r"^\([\d）]+\)$",         # (1） gibi
            r"^KOV\s+Z",              # KOV Z20 metadata
            r"^%\d+\s+%",            # %25 % iNDiRiM gürültüsü
        ],
        "total_pattern": r"^TOPLAM|^EFT-[PF]OS",
        "date_pattern": COMMON_DATE_PATTERNS,
        "name_cleanup": COMMON_NAME_CLEANUPS + [
            (r"^[=\-]+\s*", ""),                        # baştaki = veya - OCR kalıntısı
            (r"\s*(?:\d+%|%\d+)\s*$", ""),           # sondaki KDV kodu: %20 %1 %01 veya 10%
        ],
    },
    "tankar": {
        "name": "Tankar",
        "identifiers": [
            r"TANKAR",
            r"TANKRR",
            r"\bANKAR\b",            # OCR bazen baştaki T'yi düşürüyor
        ],
        "layout": {
            "y_tolerance": 20,  # Standart row tolerance
            "header_y_max": 330,  # Ürünler Y>650'de başlıyor
            "footer_y_min": 9999,  # KDV Y~799 include et, TOPLAM Y=843 include et
        },
        "price_pattern": r"^.*\*([\d\.]+,\d{2}|[\d]+[\.,]\d{2}|[\d]{3,})$",  # 2537.47, 250,00, 250 (3+ digit)
        "skip_patterns": COMMON_SKIP_PATTERNS,
        "total_pattern": r"^TOPLAM|^TOP|^K.KARTI|^EFT-[PF]OS",  # TOPLAM, TOP satır başında, veya TOP ortada
        "date_pattern": COMMON_DATE_PATTERNS,
        "name_cleanup": COMMON_NAME_CLEANUPS + [],
    },
    "metro": {
        "name": "METRO / ETRD GrosMarket",
        "identifiers": [
            r"METRO",
            r"ETRD",
            r"ETRDGROSMARET",
            r"FETRO",             # OCR F↔M hatası
            r"ETRO\s+GROS",      # "ETRO GROSMARKET" OCR kırpması
            r"metro-tr",          # Web adresi
        ],
        "layout": {
            "y_tolerance": 15,
            "header_y_max": 1500,  # e-Fatura header'ı Y=1140+'a kadar uzanabiliyor
            "footer_y_min": 9999,
        },
        "price_pattern": r"^[\*x×](-?[\d\.]+,\d{2}|-?[\d]+[\.,]\d{2})$",
        "skip_patterns": COMMON_SKIP_PATTERNS + [
            r"^[0-9]{8,}[A-Z]",
        ],
        "total_pattern": r"^(NET\s+TOPLAM|ODENE|[ÖO]DENE|TOPLAM(?!\s+K[DO]V))",
        "date_pattern": COMMON_DATE_PATTERNS,
        "name_cleanup": COMMON_NAME_CLEANUPS + [
            (r"^[0-9]{8,}[A-Z]?", ""),
        ],
    },
    "fsref": {
        "name": "FSREF CAN GIDA",
        "identifiers": [
            r"FSREF",
            r"CAN\s+GIDA",
        ],
        "layout": {
            "y_tolerance": 18,
            "header_y_max": 400,
            "footer_y_min": 9999,
        },
        "price_pattern": r"^.*\*([\d\.]+,\d{2}|[\d]+[\.,]\d{2})$",
        "skip_patterns": COMMON_SKIP_PATTERNS,
        "total_pattern": r"^(TOPLAM|TOP|EFT-[PF]OS)",
        "date_pattern": COMMON_DATE_PATTERNS,
        "name_cleanup": COMMON_NAME_CLEANUPS + [
            (r"^[=\-]+\s*", ""),
        ],
    },
    "buenas": {
        "name": "BUENAS / RESTORAN",
        "identifiers": [
            r"BUENAS",
        ],
        "layout": {
            "y_tolerance": 15,
            "header_y_max": 800,   # Header Y=700-780'e kadar uzanıyor
            "footer_y_min": 9999,
            "name_before_price": True,  # Ürün adı fiyat satırından önce geliyor
        },
        "price_pattern": r"^[\*x×](-?[\d\.]+,\d{2}|-?[\d]+[\.,]\d{2})$",
        "skip_patterns": COMMON_SKIP_PATTERNS + [
            r"^[0-9]{8,}[A-Z]",
        ],
        "total_pattern": r"^(NET\s+TOPLAM|ODENE|[ÖO]DENE|TOPLAM(?!\s+K[DO]V))",
        "date_pattern": COMMON_DATE_PATTERNS,
        "name_cleanup": COMMON_NAME_CLEANUPS + [
            (r"^[0-9]{8,}[A-Z]?", ""),
        ],
    },
    "cafegurup": {
        "name": "CAFEGURUP / Restoran",
        "identifiers": [
            r"CAFEGURUP",
            r"GASTRONOMI",
            r"SANAYIVE\s+TİCARET",
        ],
        "layout": {
            "y_tolerance": 18,
            "header_y_max": 400,
            "footer_y_min": 9999,
        },
        "price_pattern": r"^[\*x×]?(\d{1,3}(?:[.,]\d{3})*[.,]\d{2}|\d+[.,]\d{2})\s*(?:TL)?$",
        "skip_patterns": COMMON_SKIP_PATTERNS + [
            r"^[A-Z\s]+[İiI]\s*[Ss].*[Tt][iI]",
        ],
        "total_pattern": r"^(TOPLAM(?!\s*K[DO]V)|GENEL\s+TOPLAM|OPLAM)",
        "date_pattern": COMMON_DATE_PATTERNS,
        "name_cleanup": COMMON_NAME_CLEANUPS,
    },
}

# TODO: Genel olarak performans bence yeterli ancak preprocessing tamamen bilmediğim bir alan. Android'deki ClearScan uygulaması
# bütün preprocessing adımlarını yapıyor. Tamamen otomatik. Eğer Whatsapp üzerinden gönderilecekse telefon çekimindense bu uygulama
# üzerinden görsel alınırsa, bir ton ince ayara gerek kalmıyor. Bu preProcessing'leri ben yapana kadar bunları sözü edilen uygulamaya
# yükledim.
# TODO: Skip patternlerin biraz daha azaltılması lazım. Her saçmalığın skip olarak değil de farklı yöntemler ile 
# yok edilmesi gerekiyor. header ve footer'da verinin bırakılması gibi. Header tarihten başlıyor.
# TODO: Tüm bunları yaparken weight row ile birleşme olayı var ya. Onun patlamaması gerekiyor çünkü öyle bir şeyi 
# nasıl yaptığımı veya bir daha denersem nasıl yapacağımı bilmiyorum.
# TODO: Ya price'ın başında yıldız olmazsa??????? OCR artığı çok fazla sayı da var.

# ── OCR çıktısını parse et ────────────────────────────────────────────────────

def load_detections(ocr_json: dict) -> list[Detection]:
    """PaddleOCR JSON çıktısını Detection listesine çevir."""
    detections = []
    for item in ocr_json.get("detections", []):
        bbox, (text, confidence) = item[0], item[1]
        # bbox: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] (dörtgen köşeleri)
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        detections.append(Detection(
            text=text.strip(),
            confidence=confidence,
            x_min=min(xs),
            x_max=max(xs),
            y_min=min(ys),
            y_max=max(ys),
            y_center=(min(ys) + max(ys)) / 2,
            bbox=bbox
        ))
    return detections


# ── Market tespiti ────────────────────────────────────────────────────────────

def detect_store(detections: list[Detection]) -> Optional[str]:
    """Header bölgesindeki metinden marketi tespit et."""
    # Sadece üst %25'e bak (header)
    # Aslında ymax ile çelişiyor gibi ancak ymax'ın belirlenebilmesi için profilin, onun için de store isminin belirlenmesi lazım
    # yani ilk %25'e bakmadan header'in neresi olduğu bile belli değil.
    max_y = max(d.y_max for d in detections)
    header_texts = [d.text for d in detections if d.y_center < max_y * 0.25]

    for store_key, profile in STORE_PROFILES.items():
        for pattern in profile["identifiers"]:
            for text in header_texts:
                if re.search(pattern, text, re.IGNORECASE):
                    return store_key
    return None


# ── Tarih tespiti ─────────────────────────────────────────────────────────────

def extract_date(detections: list[Detection], profile: dict) -> tuple[Optional[str], Optional[float]]:
    patterns = profile.get("date_pattern")
    if not patterns:
        return None,None
    # Tum detections'da tarih ara (header bolgesine sinirlanma)
    for i, d in enumerate(detections):
        # Tarih pattern'iyle esleyen metinleri kontrol et
        for pattern in patterns: 
            m = re.search(pattern, d.text)
            if m:
                date_str = m.group(1)
                if DEBUG:
                    print(f"  [DATE_CHECK #{i}] '{d.text}' | Y:{d.y_center:.0f}")
                # DD.MM.YYYY veya DD-MM-YYYY -> YYYY-MM-DD
                parts = re.split(r'[-./]', date_str)
                if len(parts) == 2 and len(parts[1]) == 6:
                    # DD.MMYYYY (ayırıcı yok, ör. 13.122025) → ayır
                    parts = [parts[0], parts[1][:2], parts[1][2:]]
                if len(parts) == 3:
                    year = parts[2]
                    if len(year) == 2:
                        year = "20" + year  # "26" → "2026"
                    result = f"{year}-{parts[1]}-{parts[0]}"
                    if DEBUG:
                        print(f"    [+] ACCEPT: '{date_str}' -> {result}")
                    return result, d.y_max
                elif DEBUG:
                    print(f"    [-] SKIP: Split failed. Parts: {parts}")

    if DEBUG:
        print(f"  [DATE_CHECK] No date found")
    return None,None


# ── Satır gruplama ────────────────────────────────────────────────────────────

def _middle_third_bbox(bbox: list[list[float]]) -> list[list[float]]:
    """
    bbox = [TL, TR, BR, BL].
    Orta 1/3 bölgesinin 4 köşesini döndürür — check_detection'a geçmek için.
    """
    tl, tr, br, bl = bbox
    p_top_left  = [tl[0] + (bl[0] - tl[0]) / 3,     tl[1] + (bl[1] - tl[1]) / 3]
    p_top_right = [tr[0] + (br[0] - tr[0]) / 3,     tr[1] + (br[1] - tr[1]) / 3]
    p_bot_right = [tr[0] + 2 * (br[0] - tr[0]) / 3, tr[1] + 2 * (br[1] - tr[1]) / 3]
    p_bot_left  = [tl[0] + 2 * (bl[0] - tl[0]) / 3, tl[1] + 2 * (bl[1] - tl[1]) / 3]
    return [p_top_left, p_top_right, p_bot_right, p_bot_left]


def group_into_rows(detections: list[Detection], y_tolerance: float) -> list[list[Detection]]:
    """
    Yakın Y koordinatlı detection'ları aynı satıra grupla.

    Bant, ilk detection'ın tam yüksekliğinden oluşturulur; yeni detection'ların
    orta 1/3'ü bu bantla karşılaştırılır. Böylece:
      - Tam yükseklik bant: farklı boyutlu detection'ları (ör. fiyat vs. ürün adı) yakalar.
      - Orta 1/3 kontrol: bitişik satırların kaymasını önler.
    y_tolerance kesin kesim noktasıdır (>= ile).
    """
    cleaned = [d for d in detections if d.confidence >= 0.60]
    sorted_dets = sorted(cleaned, key=lambda d: d.y_center)

    rows = []
    current_row: list[Detection] = []
    band_m1 = band_b1 = band_m2 = band_b2 = 0.0
    overlap_threshold = 30

    for det in sorted_dets:
        if not current_row:
            current_row.append(det)
            tl, tr, br, bl = det.bbox
            band_m1, band_b1 = get_line_equation_from_two_points(tl, tr)
            band_m2, band_b2 = get_line_equation_from_two_points(bl, br)
            continue

        # Kesin y mesafesi kesimi (>= ile, = durumunda da yeni satır)
        if det.y_center - current_row[-1].y_center >= y_tolerance:
            rows.append(sorted(current_row, key=lambda d: d.x_min))
            current_row = [det]
            tl, tr, br, bl = det.bbox
            band_m1, band_b1 = get_line_equation_from_two_points(tl, tr)
            band_m2, band_b2 = get_line_equation_from_two_points(bl, br)
            continue

        # Orta 1/3 overlap kontrolü: bitişik satır sızmasını önler
        overlap = check_detection(band_m1, band_b1, band_m2, band_b2, _middle_third_bbox(det.bbox))
        if overlap >= overlap_threshold:
            current_row.append(det)
        else:
            rows.append(sorted(current_row, key=lambda d: d.x_min))
            current_row = [det]
            tl, tr, br, bl = det.bbox
            band_m1, band_b1 = get_line_equation_from_two_points(tl, tr)
            band_m2, band_b2 = get_line_equation_from_two_points(bl, br)

    if current_row:
        rows.append(sorted(current_row, key=lambda d: d.x_min))

    return rows


def get_line_equation_from_two_points(p1: list[float], p2:list[float]) -> tuple[float, float]:
    """Bounding box'ın alt kenarından (alt-sol ve alt-sağ noktaları) bir doğru denklemi (eğim, y-kesen) çıkarır."""
    x1, y1 = p1 # -sol
    x2, y2 = p2 # -sağ
    if x2 == x1: # Dikey doğru durumu
        return float('inf'), x1 # Eğim sonsuz, x-keseni x1
    m = (y2 - y1) / (x2 - x1)
    b = y1 - m * x1
    return m, b

def check_detection(m1: float, b1: float,  m2: float, b2:float, BsquareCoords: list[list[float]] ) -> float:
    
    # 1. Dikdörtgen koordinatları (x, y)
    # Örnek: B dikdörtgeni
    poly_b = Polygon(BsquareCoords)
    # 2. A dikdörtgeninden türetilen devasa KANAL (sağı Sonsuz Şerit Hilesi)
    channel_coords = [(0, (m1*0)+b1), (2e3, m1*2e3+b1), (2e3, m2*2e3+b2), (0, (m2*0+b2))]
    if channel_coords[0][1] > channel_coords[3][1]:
        temp = channel_coords[0] 
        channel_coords[0] = channel_coords[3]
        channel_coords[3] = temp
    if channel_coords[1][1] > channel_coords[2][1]:
        temp = channel_coords[1] 
        channel_coords[1] = channel_coords[2]
        channel_coords[2] = temp
    poly_channel = Polygon(channel_coords)
    # 3. Kesişim Alanını Hesapla
    intersection_area = poly_b.intersection(poly_channel).area
    total_b_area = poly_b.area
    # 4. Yüzdeyi Bul
    percentage = (intersection_area / total_b_area) * 100
    return percentage

# ── Ana parser ────────────────────────────────────────────────────────────────

def should_skip(text: str, skip_patterns: list[str]) -> bool:
    for pattern in skip_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def clean_name(name: str, cleanups: list[tuple]) -> str:
    for pattern, replacement in cleanups:
        name = re.sub(pattern, replacement, name).strip()
    return name


def parse_price(text: str, price_pattern: str) -> Optional[float]:
    cleaned = text.strip()
    for pattern, repl in PRICE_PREFIX_CLEANUP:
        cleaned = re.sub(pattern, repl, cleaned)
    m = re.match(price_pattern, cleaned)
    if m:
        price_str = m.group(1)
        if "," in price_str:
            # Türkçe format: 2.537,47 → 2537.47
            price_str = price_str.replace(".", "").replace(",", ".")
        elif price_str.count(".") >= 2:
            # OCR virgül→nokta hatası: 1.439.00 → gerçekte 1.439,00 → 1439.00
            parts = price_str.rsplit(".", 1)
            price_str = parts[0].replace(".", "") + "." + parts[1]
        # else: İngilizce format 14.62 — olduğu gibi bırak
        try:
            return float(price_str)
        except ValueError:
            return None
    return None


def parse_weight_line(text: str) -> Optional[tuple[float, float]]:
    """
    '0.74 kg X 19.75' veya '0.24 kg X 199.00' gibi satırları parse et.
    3 ADx125,40 TL/AD
    (miktar, birim_fiyat) döndür, değilse None.
    """
    m = re.search(r"(\d+[\.,]\d+)\s*kg\s*[Xx×]\s*(\d+[\.,]\d+)", text, re.IGNORECASE)
    if not m:
        m = re.search(r"(\d+)\s*AD\s*[Xx×]\s*(\d+[\.,]?\d*)\s*TL/AD", text, re.IGNORECASE)
    if m:
        qty   = float(m.group(1).replace(",", "."))
        price = float(m.group(2).replace(",", "."))
        return qty, price
    return None


def row_has_price(row, price_pattern: str) -> bool:
    return any(parse_price(d.text, price_pattern) is not None for d in row)


def merge_weight_rows(rows: list[list[Detection]], price_pattern: str) -> list[list[Detection]]:
    """
    Tartılı/çok birimli ürünler 2 veya 3 satır olarak gelebilir.

    Durum A — kg satırında fiyat YOK:
      2 satır (server OCR):
        Satır 1: "0.74 kg X 19.75"
        Satır 2: "PATATES %1."  *14.62  veya  ×101,97

      3 satır (mobile OCR):
        Satır 1: "0.74 kg X 19.75"
        Satır 2: "PATATES"
        Satır 3: *14.62

    Durum B — kg+fiyat AYNI satırda, isim sonraki satırda:
        Satır 1: "3ADX144,00TL/AD  *432,00"
        Satır 2: "Bira 3 adet"

    Durum C — kg+fiyat AYNI satırda, sonraki satırda isim+sürüklenmiş fiyat:
        Satır 1: "3ADX144,00TL/AD  *432,00"
        Satır 2: "=ESLGUTENSIZ BIRA  *199,90"  ← *199,90 bir sonraki ürüne ait
        Satır 3: "KOCAMAN MARINEHAMSI"          ← fiyatsız kaldı → enjekte edilir

    Fiyat prefix'i: *, × veya x (tüm market profilleri için)
    """
    result = []
    i = 0
    while i < len(rows):
        row = rows[i]
        row_text = " ".join(d.text for d in row)
        weight_info = parse_weight_line(row_text)

        if weight_info:
            qty, unit_price = weight_info
            tag = f"__WEIGHT__{qty}×{unit_price}__"

            if not row_has_price(row, price_pattern):
                # Durum A: kg satırında fiyat yok
                # Sonraki satırda fiyat var mı?
                if i + 1 < len(rows) and row_has_price(rows[i + 1], price_pattern):
                    # 2 satır: kg | isim+fiyat
                    merged = rows[i + 1].copy()
                    merged[0] = Detection(
                        text=tag + " " + merged[0].text,
                        confidence=merged[0].confidence,
                        x_min=merged[0].x_min, x_max=merged[0].x_max,
                        y_min=merged[0].y_min, y_max=merged[0].y_max,
                        y_center=merged[0].y_center,
                        bbox=merged[0].bbox
                    )
                    result.append(merged)
                    i += 2
                    continue
                elif i + 2 < len(rows) and row_has_price(rows[i + 2], price_pattern):
                    # 3 satır: kg | isim | fiyat
                    name_text = " ".join(d.text for d in rows[i + 1]).strip()
                    combined = (tag + " " + name_text) if name_text else tag
                    merged = rows[i + 2].copy()
                    merged[0] = Detection(
                        text=combined,
                        confidence=merged[0].confidence,
                        x_min=merged[0].x_min, x_max=merged[0].x_max,
                        y_min=merged[0].y_min, y_max=merged[0].y_max,
                        y_center=merged[0].y_center,
                        bbox=merged[0].bbox
                    )
                    result.append(merged)
                    i += 3
                    continue

            else:
                # Durum B (mobile): kg+fiyat aynı satırda, isim sonraki satırda
                if i + 1 < len(rows) and not row_has_price(rows[i + 1], price_pattern):
                    name_text = " ".join(d.text for d in rows[i + 1]).strip()
                    # kg satırındaki fiyat detection'ını koru, ismi ekle
                    merged = row.copy()
                    merged[0] = Detection(
                        text=tag + " " + name_text,
                        confidence=merged[0].confidence,
                        x_min=merged[0].x_min, x_max=merged[0].x_max,
                        y_min=merged[0].y_min, y_max=merged[0].y_max,
                        y_center=merged[0].y_center,
                        bbox=merged[0].bbox
                    )
                    result.append(merged)
                    i += 2
                    continue

                elif i + 1 < len(rows) and row_has_price(rows[i + 1], price_pattern):
                    # Durum C (mobile, stranded price): kg+fiyat aynı satırda,
                    # sonraki satırda hem isim hem de fiyat var — ama o fiyat
                    # aslında bir sonraki ürüne ait (OCR'ın satır gruplandırma
                    # hatası sonucu sürüklendi).
                    #
                    # Örnek:
                    #   ROW i  : "3ADx144,00TL/AD  *432,00"
                    #   ROW i+1: "=ESLGUTENSIZ BIRA  *199,90"  ← *199,90 sürüklendi
                    #   ROW i+2: "KOCAMAN MARINEHAMSI"         ← fiyatsız kaldı
                    #
                    # Çözüm:
                    #   - Bu ürün: tag + sonraki satırın ismi, fiyat = kg satırının fiyatı
                    #   - Sürüklenen *199,90'ı rows[i+2]'ye enjekte et
                    name_dets  = [d for d in rows[i + 1] if parse_price(d.text, price_pattern) is None]
                    freed_dets = [d for d in rows[i + 1] if parse_price(d.text, price_pattern) is not None]
                    name_text  = " ".join(d.text for d in name_dets).strip()

                    merged = row.copy()
                    merged[0] = Detection(
                        text=tag + (" " + name_text if name_text else ""),
                        confidence=merged[0].confidence,
                        x_min=merged[0].x_min, x_max=merged[0].x_max,
                        y_min=merged[0].y_min, y_max=merged[0].y_max,
                        y_center=merged[0].y_center,
                        bbox=merged[0].bbox
                    )
                    result.append(merged)

                    # Sürüklenen fiyat detection'larını bir sonraki satıra enjekte et
                    if freed_dets and i + 2 < len(rows):
                        rows[i + 2] = rows[i + 2] + freed_dets
                        if DEBUG:
                            print(f"  [DURUM C] Serbest birakilan fiyat {[d.text for d in freed_dets]} " f"-> rows[{i+2}] e enjekte edildi")
                    i += 2
                    continue

        result.append(row)
        i += 1
    return result


def merge_orphan_rows(
    rows: list[list[Detection]],
    price_pattern: str,
    skip_patterns: list[str] | None = None,
    total_pattern: str | None = None,
) -> list[list[Detection]]:
    """
    Fiyat-isim satırları ters sırada gelen durumları düzelt:

    Durum: %20*fiyat  →  ÜRÜN_ADI
    (Tankar yakıt fişi: önce KDV+fiyat satırı, sonra ürün adı)

    Tek detection'dan oluşan ve tamamı fiyat olan bir satır,
    ardından gelen fiyatsız satırla birleştirilir.
    Toplam veya skip satırları birleştirilmez.
    """
    result = []
    i = 0
    while i < len(rows):
        row = rows[i]
        # Bu satır tamamen fiyat mı? (tüm detection'lar fiyat, isim yok)
        if (row_has_price(row, price_pattern)
                and all(parse_price(d.text, price_pattern) is not None for d in row)):
            # Sonraki satırda fiyat yok mu? (isim satırı)
            if i + 1 < len(rows) and not row_has_price(rows[i + 1], price_pattern):
                next_text = " ".join(d.text for d in rows[i + 1])
                # Toplam veya skip satırıyla birleştirme — o satır kendi işlenir
                is_total = total_pattern and re.search(total_pattern, next_text, re.IGNORECASE)
                is_skip = skip_patterns and any(
                    re.search(p, next_text, re.IGNORECASE) for p in skip_patterns
                )
                if not is_total and not is_skip:
                    merged = rows[i + 1] + row
                    result.append(merged)
                    i += 2
                    continue
        result.append(row)
        i += 1
    return result


def parse_receipt(ocr_json: dict) -> Receipt:
    detections = load_detections(ocr_json)

    if DEBUG:
        print(f"\n[PARSE_RECEIPT] Toplam {len(detections)} detection")

    # Market tespit et
    store_key = detect_store(detections)
    if store_key is None:
        #raise ValueError("Market tespit edilemedi! Desteklenen marketler: " + ", ".join(STORE_PROFILES.keys()))
        print("Market tespit edilemedi! Desteklenen marketler: " + ", ".join(STORE_PROFILES.keys()))
        store_key = "migros"

    profile = STORE_PROFILES[store_key]
    layout = profile["layout"]
    print(f"[OK] Market tespit edildi: {profile['name']}")

    if DEBUG:
        print(f"\n[LAYOUT] header_y_max: {layout['header_y_max']}, footer_y_min: {layout['footer_y_min']}")

    # Tarih çıkar
    date, date_y = extract_date(detections, profile)
    if DEBUG:
        print(f"[DATE] Çıkarılan: {date}")
        
    # header_y_max: tarih profilin beklenen header bölgesindeyse kullan.
    # Ödeme bloğundaki ikinci tarih damgası (footer) header sınırını bozmamalı.
    profile_header = layout["header_y_max"]
    if date_y is not None and date_y <= profile_header:
        header_y_max = date_y
    else:
        header_y_max = profile_header

    # Ürün bölgesindeki detection'ları filtrele
    product_dets = [
        d for d in detections
        if header_y_max < d.y_center < layout["footer_y_min"]
    ]
    if DEBUG:
        print(f"\n[YENİ LAYOUT] header_y_max: {header_y_max}, footer_y_min: {layout['footer_y_min']}")
        
    if DEBUG:
        print(f"[PRODUCT_REGION] {len(product_dets)} detection secildi ({len(detections)} toplam)")
        if len(product_dets) == 0:
            print(f"  [*] Detayli check: Header altinda ({header_y_max}) ve footer ustunde ({layout['footer_y_min']}) olan detection yok")
        elif len(product_dets) != 0:
            # Product region'daki detection'lari listele (debug icin)
            for i, d in enumerate(product_dets):  # ilk 15'ini goster
                print(f"    prod[{i:2d}] XMax={d.x_max} YMax={d.y_max} Xmin={d.x_min} Ymin={d.y_min} | Y.Center={d.y_center:7.1f} X={d.x_min:6.1f} | {repr(d.text[:25])}")

    # Satırlara grupla
    rows = group_into_rows(product_dets, layout["y_tolerance"])
    if DEBUG:
        print(f"[ROWS] {len(rows)} satıra gruplandırıldı")

    # Tartılı ürün satırlarını birleştir
    rows = merge_weight_rows(rows, profile["price_pattern"])

    # Fiyat-isim sıralı satırları birleştir (ör. Tankar yakıt: %20*fiyat | ÜRÜN_ADI)
    rows = merge_orphan_rows(rows, profile["price_pattern"],
                              skip_patterns=profile["skip_patterns"],
                              total_pattern=profile["total_pattern"])


    items = []
    total = None
    pending_name_dets  = None  # isim var, fiyat yok → sonraki satırda fiyat gelince kullanılır
    pending_price_dets = None  # fiyat var, isim yok/skip → sonraki satırda isim gelince kullanılır
    for row_idx, row in enumerate(rows):
        row_text = " ".join(d.text for d in row)
        row_y = row[0].y_center if row else 0

        if DEBUG:
            print(f"\n  [ROW #{row_idx}] Y~{row_y:.0f} | '{row_text}'")

        # Toplam satırı mı?
        if re.search(profile["total_pattern"], row_text, re.IGNORECASE):
            if DEBUG:
                print(f"    [T] TOPLAM satiri (pattern: {profile['total_pattern']})")
            price = None
            for d in reversed(row):
                price = parse_price(d.text, profile["price_pattern"])
                if price:
                    total = price
                    break
            if total:
                break
            # Fiyat aynı satırda yoksa sonraki satıra bak (ör. TOP | *250,00)
            if row_idx + 1 < len(rows):
                next_row = rows[row_idx + 1]
                if len(next_row) == 1:
                    p = parse_price(next_row[0].text, profile["price_pattern"])
                    if p is not None:
                        total = p
                        break
            continue
            
        # Skip listesinde mi?
        skip_reason = None
        for pattern in profile["skip_patterns"]:
            if re.search(pattern, row_text, re.IGNORECASE):
                skip_reason = pattern
                break

        if skip_reason:
            if DEBUG:
                print(f"    [-] SKIP (pattern: {skip_reason})")
            continue
        
        # Fiyat ve isim detection'larını ayır
        first_price_idx=None
        # Listenin en sonundan (len-1) başına doğru (-1) adım adım (-1) gidiyoruz
        for i in range(len(row) - 1, -1, -1):
            if parse_price(row[i].text, profile["price_pattern"]) is not None:
                first_price_idx = i
                break 
        if first_price_idx is not None:
            # Bulduğumuz indekse kadar olanlar isim, o ve sonrası fiyattır
            name_dets = row[:first_price_idx]
            price_dets = row[first_price_idx:]
        else:
            # Hiç fiyat yoksa her şey isimdir
            name_dets = row
            price_dets = []


        if DEBUG:
            print(f"    [*] Price dets (): {[d.text for d in price_dets]}")
            print(f"    [*] Name dets (): {[d.text for d in name_dets]}")

        _INLINE_RE = r"^(.+?)\*(-?[\d\.]+,\d{2}|-?[\d]+[\.,]\d{2}|-?[\d]{3,})$"

        # Sadece name_dets varsa: içinde gömülü fiyat ara
        if not price_dets and name_dets:
            new_name_dets = []
            for d in name_dets:
                raw_t = d.text
                for _p, _r in PRICE_PREFIX_CLEANUP:
                    raw_t = re.sub(_p, _r, raw_t)
                m = re.match(_INLINE_RE, raw_t)
                if m:
                    new_name_dets.append(Detection(
                        text=m.group(1).strip(),
                        confidence=d.confidence, x_min=d.x_min, x_max=d.x_max,
                        y_min=d.y_min, y_max=d.y_max, y_center=d.y_center, bbox=d.bbox
                    ))
                    price_dets.append(Detection(
                        text="*" + m.group(2),
                        confidence=d.confidence, x_min=d.x_min, x_max=d.x_max,
                        y_min=d.y_min, y_max=d.y_max, y_center=d.y_center, bbox=d.bbox
                    ))
                    if DEBUG:
                        print(f"    [*] Inline ayırma (name→price): '{m.group(1).strip()}' / '*{m.group(2)}'")
                else:
                    new_name_dets.append(d)
            name_dets = new_name_dets

        # Sadece price_dets varsa: içinde gömülü isim ara
        if not name_dets and len(price_dets) == 1:
            raw_t = price_dets[0].text
            for _p, _r in PRICE_PREFIX_CLEANUP:
                raw_t = re.sub(_p, _r, raw_t)
            m = re.match(_INLINE_RE, raw_t)
            if m:
                d0 = price_dets[0]
                name_dets = [Detection(
                    text=m.group(1).strip(),
                    confidence=d0.confidence, x_min=d0.x_min, x_max=d0.x_max,
                    y_min=d0.y_min, y_max=d0.y_max, y_center=d0.y_center, bbox=d0.bbox
                )]
                if DEBUG:
                    print(f"    [*] Inline ayırma (price→name): '{m.group(1).strip()}'")

        # Hâlâ fiyat yok → pending_price varsa kullan, yoksa name'i pending_name yap
        if not price_dets:
            name_str = " ".join(d.text for d in name_dets).strip() if name_dets else ""
            valid_name = name_str and not should_skip(name_str, profile["skip_patterns"])
            if pending_price_dets is not None and valid_name:
                price_dets = pending_price_dets
                pending_price_dets = None
                if DEBUG:
                    print(f"    [P] Bekleyen fiyat kullanılıyor")
                # price var artık, aşağıya devam et
            else:
                if valid_name:
                    pending_name_dets = name_dets
                    if DEBUG:
                        print(f"    [P] Bekleyen isim kaydedildi: '{name_str}'")
                if DEBUG:
                    print(f"    [-] SKIP: price_dets boş")
                continue

        # Hâlâ isim yok ya da skip → pending_name varsa kullan, yoksa price'ı pending_price yap
        name_str = " ".join(d.text for d in name_dets).strip() if name_dets else ""
        if not name_dets or should_skip(name_str, profile["skip_patterns"]):
            if pending_name_dets is not None:
                if DEBUG:
                    print(f"    [P] Bekleyen isim kullanılıyor: '{' '.join(d.text for d in pending_name_dets)}'")
                name_dets = pending_name_dets
                pending_name_dets = None
            else:
                pending_price_dets = price_dets
                if DEBUG:
                    reason = "boş" if not name_dets else "skip pattern'iyle eşleşti"
                    print(f"    [P] Bekleyen fiyat kaydedildi ({reason}): {[d.text for d in price_dets]}")
                    print(f"    [-] SKIP: name_dets {reason}, fiyat bekletildi")
                continue
        else:
            pending_name_dets  = None
            pending_price_dets = None

        # Fiyat
        price = None
        for pd in reversed(price_dets):
            price = parse_price(pd.text, profile["price_pattern"])
            if price:
                break

        if price is None:
            if DEBUG:
                print(f"    [-] SKIP: Fiyat parse edilemedi")
            continue

        if DEBUG:
            print(f"    [*] Fiyat: {price:.2f} TL")

        # İsim — weight tag'i varsa çıkar ve formatlı isim yap
        raw_name = " ".join(d.text for d in name_dets).strip()

        weight_tag = re.match(r"__WEIGHT__([\d\.]+)×([\d\.]+)__\s*(.+)", raw_name)
        if weight_tag:
            qty        = float(weight_tag.group(1))
            unit_price = float(weight_tag.group(2))
            base_name  = weight_tag.group(3).strip()
            if should_skip(base_name, profile["skip_patterns"]):
                if DEBUG:
                    print(f"    [-] SKIP: Weight item ismi skip edildi: {base_name!r}")
                continue
            base_name  = clean_name(base_name, profile["name_cleanup"])
            display    = f"{base_name} ({qty}kg × {unit_price:.2f})"
            if DEBUG:
                print(f"    [*] Tartılı ürün: {display}")
        else:
            if should_skip(raw_name, profile["skip_patterns"]):
                if DEBUG:
                    print(f"    [-] SKIP: Isim skip pattern'iyle eslesti")
                continue
            display = clean_name(raw_name, profile["name_cleanup"])
            if DEBUG:
                print(f"    [*] İsim (cleaned): {display}")

        if not display:
            if DEBUG:
                print(f"    [-] SKIP: Isim bos")
            continue

        items.append(ReceiptItem(
            name=display,
            amount=price,
            raw_name=raw_name,
        ))
        if DEBUG:
            print(f"    [+] ACCEPT: {display} -> {price:.2f} TL")

    # Footer'dan toplam çıkar (BİM: footer'daki ilk *XXX.XX değeri)
    footer_dets = [
        d for d in detections
        if d.y_center >= layout["footer_y_min"]
    ]
    footer_rows = group_into_rows(footer_dets, layout["y_tolerance"])
    for frow in footer_rows:
        frow_text = " ".join(d.text for d in frow)
        # Skip pattern kontrol et
        if should_skip(frow_text, profile["skip_patterns"]):
            if DEBUG:
                print(f"[FOOTER] SKIP: {repr(frow_text[:50])}")
            continue
        if DEBUG:
            print(f"[FOOTER] ROW: {repr(frow_text[:50])}")
        if re.search(profile["total_pattern"], frow_text, re.IGNORECASE):
            # Reversed loop'ta en büyük fiyatı seç (KDV satırında birden fazla sayı olabilir)
            prices_in_row = []
            for d in reversed(frow):
                price = parse_price(d.text, profile["price_pattern"])
                if price:
                    prices_in_row.append(price)
            if prices_in_row:
                total = max(prices_in_row)  # Largest price (is typically the total)
                if total:
                    break

    return Receipt(
        store=profile["name"],
        date=date,
        items=items,
        total=total,
        raw_detections=detections,
    )


# ── Çıktı formatları ──────────────────────────────────────────────────────────

def print_summary(receipt: Receipt):
    print(f"\n{'=' * 50}")
    print(f"  {receipt.store}  |  {receipt.date or 'tarih yok'}")
    print(f"{'=' * 50}")
    for item in receipt.items:
        print(f"  {item.name:<35} {item.amount:>8.2f} TL")
    print(f"{'-' * 50}")
    calc = sum(i.amount for i in receipt.items)
    print(f"  {'Hesaplanan toplam':<35} {calc:>8.2f} TL")
    if receipt.total:
        print(f"  {'Fişteki toplam':<35} {receipt.total:>8.2f} TL")
        diff = abs(calc - receipt.total)
        if diff > 0.02:
            print(f"  [!] Fark: {diff:.2f} TL (KDV/indirim olabilir)")
        else:
            print(f"  [+] Tutarlar eşleşiyor")
    print(f"{'=' * 50}\n")

# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    global DEBUG

    if len(sys.argv) < 2:
        print("Kullanım: python parser.py <ocr_output.json> [--debug] [--mismatch-only]")
        sys.exit(1)

    DEBUG = "--debug" in sys.argv
    mismatch_only = "--mismatch-only" in sys.argv

    from snapshots import save_snapshot, check_snapshot
    from pathlib import Path
    import io

    def _process(ocr_path: Path):
        buf = io.StringIO() if mismatch_only else None
        old_stdout = sys.stdout
        if buf:
            sys.stdout = buf

        receipt = None
        try:
            print(ocr_path.name)
            with open(ocr_path, encoding="utf-8") as f:
                ocr_json = json.load(f)
            receipt = parse_receipt(ocr_json)
            print_summary(receipt)

            snap_diffs = check_snapshot(ocr_path, receipt)
            if snap_diffs:
                print("  [!] SNAPSHOT FARKI TESPIT EDILDI:")
                for diff in snap_diffs:
                    print(f"      - {diff}")
                print("  Snapshot guncellensin mi? [e/H] ", end="", flush=True)
                sys.stdout = old_stdout
                try:
                    answer = input().strip().lower()
                except (EOFError, KeyboardInterrupt):
                    answer = "h"
                if buf:
                    sys.stdout = buf
                if answer == "e":
                    save_snapshot(ocr_path, receipt)
                    print(f"  [snapshot] Guncellendi: {ocr_path.name}")
                else:
                    print(f"  [snapshot] Korundu.")
            else:
                saved = save_snapshot(ocr_path, receipt)
                if saved:
                    print(f"  [snapshot] Kaydedildi: {ocr_path.name}")
        finally:
            if buf:
                sys.stdout = old_stdout
                calc = sum(i.amount for i in receipt.items) if receipt else 0
                has_mismatch = (
                    receipt is None
                    or not receipt.total
                    or abs(calc - receipt.total) > 0.02
                )
                if has_mismatch:
                    print(buf.getvalue(), end="")

    if os.path.isdir(sys.argv[1]):
        for file in sorted(os.listdir(sys.argv[1])):
            _process(Path(sys.argv[1]) / file)
    else:
        _process(Path(sys.argv[1]))

if __name__ == "__main__":
    main()
