"""
Fiş Parser - Koordinat tabanlı, çoklu market desteği
Kullanım: python parser.py ocr_output.json [--hledger] [--debug]
"""

import json
import re
import sys
from dataclasses import dataclass, field
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
    r"^TOPLAM?\s+KDV",         # "TOPLAM KDV" satırı (asıl toplam değil)
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
    r"^[BI]:[\d]+",            # B:706 S:9638 gibi
    r"^\d{4,6}\*+\d{4}$",     # kart numarası
    r"^%\d+\.?$",             # %1. ve %1 — KDV oranı (noktalı veya noktasız)
    r"^\$\d*\.?$",            # $0. — mobile OCR gürültüsü
    r"^[\$各\\]",           # 各1 $c}$ gibi saçma karakterler
    r"^\d+\.$",               # sadece "1."
    r"^\d+[\.,]\d{2}$",       # sadece sayı (KDV tablo satırları)
    r"^\d+[\.,]\d{2}\s+\d+[\.,]\d{2}",  # KDV tablo satırı
    r"^\([\d）]+\)$",
    r"^AFATOPLAM",  # Ara toplam (Tankar)
    r"^ARATOPLAM",
    r"^TOP$",  # Sadece "TOP" label'ı (TOPLAM label değil)
    r"^KDV$",  # KDV satırı (TOPLAM değil)
    r"^SN:",
]
STORE_PROFILES = {
    "bim": {
        "name": "BİM",
        "identifiers": [
            r"BIM BIRLESIK",
            r"BİM BİRLEŞİK",
            r"BIM A\.S",
        ],
        "layout": {
            # Aynı satır toleransı (piksel)
            "y_tolerance": 18,
            # Header bölgesi: bu Y'nin altından itibaren ürünler başlar
            "header_y_max": 640,
            # Footer bölgesi: bu Y'den sonrası toplam/KDV/banka bilgisi
            "footer_y_min": 1550,
        },
        "price_pattern": r"^\*?(\d+[\.,]\d{2})$",
        "skip_patterns": COMMON_SKIP_PATTERNS + [
            r"^\([\d）]+\)$"          # (1） gibi
        ],
        "total_pattern": r"^(Odenecek KDV Dahil|TOPLAM(?!\s+KDV)|KRED[i|İ|I] KARTI)",
        "date_pattern": r"(\d{2}\.\d{2}\.\d{4})\s*\d{2}:\d{2}",  # boşluksuz da yakala
        # Ürün adı temizleme
        "name_cleanup": [
            (r"\s+[\$%]\d*\.?\s*$", ""),        # sondaki $0. %1. %0. %20
            (r"\s+\b\d{1}\b\s*$", ""),              # sondaki tek rakam: "PATATES 1" → "PATATES"
            (r"\s+\$\\.*?\$\s*$", ""),          # $\1c}$ gibi LaTeX artığı
            (r"\s+[各]\d*\s*$", ""),               # 各1 gibi Çince OCR gürültüsü
            (r"\s+\\?\d+\.\s*$", ""),           # sondaki OCR artığı: \11. gibi
            (r"^(\d+[\.,]\d+)\s*kg\s*[Xx×]\s*(\d+[\.,]\d+)\s+", r"(\1kg × \2) "),  # öndeki kg bilgisi
            (r"\s{2,}", " "),                        # çift boşluk
        ],
    },
    "migros": {
        "name": "Migros",
        "identifiers": [
            r"MIGROS",
            r"MİGROS",
        ],
        "layout": {
            "y_tolerance": 18,
            "header_y_max": 500,
            "footer_y_min": 9999,  # henüz bilinmiyor
        },
        "price_pattern": r"^\*?(\d+[\.,]\d{2})$",
        "skip_patterns": COMMON_SKIP_PATTERNS + [
            r"^\([\d）]+\)$"          # (1） gibi
        ],
        "total_pattern": r"^TOPLAM",
        "date_pattern": r"(\d{2}\.\d{2}\.\d{4})",
        "name_cleanup": [],
    },
    "tankar": {
        "name": "Tankar",
        "identifiers": [
            r"TANKAR",
            r"TANKRR"
        ],
        "layout": {
            "y_tolerance": 25,  # Standart row tolerance
            "header_y_max": 330,  # Ürünler Y>650'de başlıyor
            "footer_y_min": 1190,  # KDV Y~799 include et, TOPLAM Y=843 include et
        },
        "price_pattern": r"^.*\*([\d\.]+,\d{2}|[\d]+[\.,]\d{2}|[\d]{3,})$",  # 2537.47, 250,00, 250 (3+ digit)
        "skip_patterns": COMMON_SKIP_PATTERNS + [
            r"^SN:"
        ],
        "total_pattern": r"^TOPLAM|^TOP|^K.KARTI|^EFT-[P|F]OS",  # TOPLAM, TOP satır başında, veya TOP ortada
        "date_pattern": r"(\d{2}-\d{2}-\d{4})",
        "name_cleanup": [],
    }
}

# TODO: header_y_max ise bir noktadan sonra baş ağrıtacak gibi. Bunun da belli bir anahtar kelimeye bağlanması mükün mü?
# Belki tarih detection ile başlatılabilir header sonu.
# TODO: Genel olarak performans bence yeterli ancak preprocessing tamamen bilmediğim bir alan. Android'deki ClearScan uygulaması
# bütün preprocessing adımlarını yapıyor. Tamamen otomatik. Eğer Whatsapp üzerinden gönderilecekse telefon çekimindense bu uygulama
# üzerinden görsel alınırsa, bir ton ince ayara gerek kalmıyor. Bu preProcessing'leri ben yapana kadar bunları sözü edilen uygulamaya
# yükledim.
# TODO: Skip patternlerin biraz daha azaltılması lazım. Her saçmalığın skip olarak değil de farklı yöntemler ile 
# yok edilmesi gerekiyor. header ve footer'da verinin bırakılması gibi.
# TODO: Tüm bunları yaparken weight row ile birleşme olayı var ya. Onun patlamaması gerekiyor çünkü öyle bir şeyi 
# nasıl yaptığımı veya bir daha denersem nasıl yapacağımı bilmiyorum.

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
    # Sadece üst %20'ye bak (header)
    max_y = max(d.y_max for d in detections)
    header_texts = [d.text for d in detections if d.y_center < max_y * 0.25]

    for store_key, profile in STORE_PROFILES.items():
        for pattern in profile["identifiers"]:
            for text in header_texts:
                if re.search(pattern, text, re.IGNORECASE):
                    return store_key
    return None


# ── Tarih tespiti ─────────────────────────────────────────────────────────────

def extract_date(detections: list[Detection], profile: dict) -> Optional[str]:
    pattern = profile.get("date_pattern")
    if not pattern:
        return None
    # Tum detections'da tarih ara (header bolgesine sinirlanma)
    for i, d in enumerate(detections):
        # Tarih pattern'iyle esleyen metinleri kontrol et
        m = re.search(pattern, d.text)
        if m:
            date_str = m.group(1)

            if DEBUG:
                print(f"  [DATE_CHECK #{i}] '{d.text}' | Y:{d.y_center:.0f}")

            # DD.MM.YYYY veya DD-MM-YYYY -> YYYY-MM-DD
            parts = re.split(r'[-.]', date_str)
            if len(parts) == 3:
                result = f"{parts[2]}-{parts[1]}-{parts[0]}"
                if DEBUG:
                    print(f"    [+] ACCEPT: '{date_str}' -> {result}")
                return result
            elif DEBUG:
                print(f"    [-] SKIP: Split failed. Parts: {parts}")

    if DEBUG:
        print(f"  [DATE_CHECK] No date found")
    return None


# ── Satır gruplama ────────────────────────────────────────────────────────────

def group_into_rows(detections: list[Detection], y_tolerance: float) -> list[list[Detection]]:
    """Yakın Y koordinatlı detection'ları aynı satıra grupla.
    Karşılaştırma için satırın ortalama y'sini kullan — zincirleme birleşmeyi önler.
    """
    cleaned = []
    for det in detections:
        # 1. Çok düşük güvenli okumaları at (Örn: 0.45 olan '8' gibi tipikleri)
        # %60 da emin değilsen gelme yani.
        if det.confidence < 0.60: 
            continue
        cleaned.append(det)
    sorted_dets = sorted(cleaned, key=lambda d: d.y_center)
    rows = []
    row_overlap_ratios = []
    current_row = []
    current_row_y = 0.0
    current_row_min_y = float('inf')
    current_row_max_y = float('-inf')
    overlap_threshold = 0.40 # arbitrary number???
    overlap_ratio = 0.0
    for det in sorted_dets:
        #if det.text == "0.45 kg X 89.00":
        #    print("bla")
        if not current_row:
            current_row.append(det)
            current_row_min_y = det.y_min
            current_row_max_y = det.y_max
        else:
            """
            # Mevcut detection ile current_row arasındaki dikey kesişimi hesapla
            intersection_min_y = max(det.y_min, current_row_min_y)
            intersection_max_y = min(det.y_max, current_row_max_y)
            intersection_length = abs(intersection_max_y - intersection_min_y)
            
            det_height = det.y_max - det.y_min
            current_row_height = current_row_max_y - current_row_min_y

            # Kesişim oranını hem detection'ın yüksekliğine hem de mevcut satırın yüksekliğine göre kontrol et
            # İkili kontrol, kısmen üst üste binen farklı boyutlardaki kutuları daha iyi yönetir
            overlap_ratio_det = intersection_length / det_height if det_height > 0 else 0
            overlap_ratio_row = intersection_length / current_row_height if current_row_height > 0 else 0
            
            # Y-center farkı tolerans içinde mi diye de kontrol edelim (opsiyonel ama faydalı olabilir)
            y_center_diff = abs(det.y_center - (current_row_min_y + current_row_max_y) / 2)
            if (overlap_ratio_det >= overlap_threshold or overlap_ratio_row >= overlap_threshold) and y_center_diff <= y_tolerance:
                current_row.append(det)
                current_row_min_y = min(current_row_min_y, det.y_min)
                current_row_max_y = max(current_row_max_y, det.y_max)
            else:
                rows.append(sorted(current_row, key=lambda d: d.x_min))
                current_row = [det]
                current_row_min_y = det.y_min
                current_row_max_y = det.y_max
            """                
                
            m1,b1 = get_line_equation_from_two_points(current_row[0].bbox[0],current_row[0].bbox[1])
            m2,b2 = get_line_equation_from_two_points(current_row[0].bbox[2],current_row[0].bbox[3])
            overlap = check_detection(m1,b1,m2,b2, det.bbox)
            if (overlap >= 30):
                overlap_ratio = overlap
                current_row.append(det)
                current_row_min_y = min(current_row_min_y, det.y_min)
                current_row_max_y = max(current_row_max_y, det.y_max)
            else:
                rows.append(sorted(current_row, key=lambda d: d.x_min))
                current_row = [det]
                current_row_min_y = det.y_min
                current_row_max_y = det.y_max
            
    if current_row:
        rows.append(sorted(current_row, key=lambda d: d.x_min))
        row_overlap_ratios.append(overlap_ratio)
    
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
    # 2. A dikdörtgeninden türetilen devasa KANAL (Sonsuz Şerit Hilesi)
    channel_coords = [(0, m1*0+b1), (2e3, m1*2e3+b1), (2e3, m2*2e3+b2), (0, m2*0+b2)]
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
    m = re.match(price_pattern, text.strip())
    if m:
        price_str = m.group(1)
        # Iki format destekle:
        # Turkce: 2.537,47 (nokta=binde ayirici, virgül=ondalik)
        # Ingilizce: 14.62 (nokta=ondalik)
        if "," in price_str:
            # Turkce format: 2.537,47 → 2537.47
            price_str = price_str.replace(".", "").replace(",", ".")
        else:
            # Ingilizce format: 14.62 (keep as-is)
            pass
        try:
            return float(price_str)
        except ValueError:
            return None
    return None


def parse_weight_line(text: str) -> Optional[tuple[float, float]]:
    """
    '0.74 kg X 19.75' veya '0.24 kg X 199.00' gibi satırları parse et.
    (miktar, birim_fiyat) döndür, değilse None.
    """
    m = re.search(r"(\d+[\.,]\d+)\s*kg\s*[Xx×]\s*(\d+[\.,]\d+)", text, re.IGNORECASE)
    if m:
        qty   = float(m.group(1).replace(",", "."))
        price = float(m.group(2).replace(",", "."))
        return qty, price
    return None


def row_has_price(row):
    return any(re.match(r"^\*?\d+[\.,]\d{2}$", d.text) for d in row)


def merge_weight_rows(rows: list[list[Detection]]) -> list[list[Detection]]:
    """
    BİM'de tartılı ürünler 2 veya 3 satır olarak gelebilir:

    2 satır (server OCR):
      Satır 1: "0.74 kg X 19.75"
      Satır 2: "PATATES %1."  *14.62

    3 satır (mobile OCR):
      Satır 1: "0.74 kg X 19.75"
      Satır 2: "PATATES"
      Satır 3: *14.62

    Her iki durumu da yakala, isim = "PATATES (0.74kg × 19.75)"
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

            if not row_has_price(row):
                # Durum A: kg satırında fiyat yok
                # Sonraki satırda fiyat var mı?
                if i + 1 < len(rows) and row_has_price(rows[i + 1]):
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
                elif i + 2 < len(rows) and row_has_price(rows[i + 2]):
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
                if i + 1 < len(rows) and not row_has_price(rows[i + 1]):
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
        raise ValueError("Market tespit edilemedi! Desteklenen marketler: " + ", ".join(STORE_PROFILES.keys()))

    profile = STORE_PROFILES[store_key]
    layout = profile["layout"]
    print(f"[OK] Market tespit edildi: {profile['name']}")

    if DEBUG:
        print(f"\n[LAYOUT] header_y_max: {layout['header_y_max']}, footer_y_min: {layout['footer_y_min']}")

    # Tarih çıkar
    date = extract_date(detections, profile)
    if DEBUG:
        print(f"[DATE] Çıkarılan: {date}")

    # Ürün bölgesindeki detection'ları filtrele
    product_dets = [
        d for d in detections
        if layout["header_y_max"] < d.y_center < layout["footer_y_min"]
    ]

    if DEBUG:
        print(f"[PRODUCT_REGION] {len(product_dets)} detection secildi ({len(detections)} toplam)")
        if len(product_dets) == 0:
            print(f"  [*] Detayli check: Header altinda ({layout['header_y_max']}) ve footer ustunde ({layout['footer_y_min']}) olan detection yok")
        elif len(product_dets) != 0:
            # Product region'daki detection'lari listele (debug icin)
            for i, d in enumerate(product_dets):  # ilk 15'ini goster
                print(f"    prod[{i:2d}] XMax={d.x_max} YMax={d.y_max} Xmin={d.x_min} Ymin={d.y_min} | Y.Center={d.y_center:7.1f} X={d.x_min:6.1f} | {repr(d.text[:25])}")

    # Satırlara grupla
    rows = group_into_rows(product_dets, layout["y_tolerance"])
    if DEBUG:
        print(f"[ROWS] {len(rows)} satıra gruplandırıldı")

    # Tartılı ürün satırlarını birleştir
    rows = merge_weight_rows(rows)

    items = []
    total = None
    for row_idx, row in enumerate(rows):
        row_text = " ".join(d.text for d in row)
        row_y = row[0].y_center if row else 0

        if DEBUG:
            print(f"\n  [ROW #{row_idx}] Y~{row_y:.0f} | '{row_text}'")

        # Toplam satırı mı?
        if re.search(profile["total_pattern"], row_text, re.IGNORECASE):
            if DEBUG:
                print(f"    [T] TOPLAM satiri (pattern: {profile['total_pattern']})")
            for d in reversed(row):
                price = parse_price(d.text, profile["price_pattern"])
                if price:
                    total = price
                    break
            if price:
                break
            else:
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

        if not price_dets or not name_dets:
            if DEBUG:
                reason = "price_dets boş" if not price_dets else "name_dets boş"
                print(f"    [-] SKIP: {reason}")
            continue

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


def to_hledger(receipt: Receipt, account_prefix: str = "expenses:food") -> str:
    """hledger journal formatında çıktı üret (taslak)."""
    date = receipt.date or "0000-00-00"
    lines = [f"{date} {receipt.store}"]
    for item in receipt.items:
        # Basit kategori tahmini - ileride kural tabanlı yapılabilir
        account = f"{account_prefix}"
        lines.append(f"    {account:<45}  {item.amount:.2f} TRY  ; {item.name}")
    lines.append(f"    liabilities:creditcard")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    global DEBUG

    if len(sys.argv) < 2:
        print("Kullanım: python parser.py <ocr_output.json> [--hledger] [--debug]")
        sys.exit(1)

    DEBUG = "--debug" in sys.argv

    with open(sys.argv[1], encoding="utf-8") as f:
        ocr_json = json.load(f)

    receipt = parse_receipt(ocr_json)
    print_summary(receipt)

    if "--hledger" in sys.argv:
        print("── hledger taslak ──────────────────────────────")
        print(to_hledger(receipt))
        print()

if __name__ == "__main__":
    main()
