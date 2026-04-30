"""
Fiş Parser - Koordinat tabanlı, çoklu market desteği
Kullanım: python parser.py ocr_output.json [--hledger journal.hledger] [--excel muhasebe.xlsm] [--sheet SheetAdı] [--debug] [--mismatch-only]
"""

import json
import re
import sys
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
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


# ── Market profilleri (stores.toml'dan yuklenir) ─────────────────────────────

def _load_config() -> tuple[dict, list]:
    """stores.toml'dan market profillerini ve evrensel ayarları yükle.
    Döndürür: (STORE_PROFILES, PRICE_PREFIX_CLEANUP)
    """
    stores_file = Path(__file__).parent / "stores.toml"
    with open(stores_file, "rb") as f:
        data = tomllib.load(f)

    common = data.get("common", {})
    common_skip     = common.get("skip_patterns", [])
    common_date     = common.get("date_patterns", [])
    common_cleanup  = [(p, r) for p, r in common.get("name_cleanup", [])]
    price_prefix    = [(p, r) for p, r in common.get("price_prefix_cleanup", [])]

    profiles = {}
    for key, s in data["store"].items():
        profiles[key] = {
            "name": s["name"],
            "identifiers": s["identifiers"],
            "layout": {
                "y_tolerance": s["y_tolerance"],
                "header_y_max": s["header_y_max"],
                "footer_y_min": s.get("footer_y_min", 9999),
            },
            "price_pattern": s["price_pattern"],
            "skip_patterns": common_skip + s.get("skip_patterns", []),
            "total_pattern": s["total_pattern"],
            "date_pattern": common_date,
            "name_cleanup": common_cleanup + [
                (p, r) for p, r in s.get("name_cleanup", [])
            ],
            "parse_mode": s.get("parse_mode", "normal"),
        }
    return profiles, price_prefix


STORE_PROFILES, PRICE_PREFIX_CLEANUP = _load_config()


# ── OCR düzeltme sözlüğü (corrections.toml) ──────────────────────────────────

def _load_corrections() -> dict[str, str]:
    """corrections.toml varsa yükle, yoksa boş dict döndür."""
    path = Path(__file__).parent / "corrections.toml"
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return {c["wrong"]: c["right"] for c in data.get("correction", [])}


CORRECTIONS: dict[str, str] = _load_corrections()

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
    """PaddleOCR JSON çıktısını Detection listesine çevir.
    corrections.toml'daki düzeltmeler burada exact-match ile uygulanır.
    """
    detections = []
    for item in ocr_json.get("detections", []):
        bbox, (text, confidence) = item[0], item[1]
        # bbox: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] (dörtgen köşeleri)
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        text = text.strip()
        # OCR düzeltmesi: exact match
        if text in CORRECTIONS:
            text = CORRECTIONS[text]
        detections.append(Detection(
            text=text,
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
                    # Imkansiz tarih degerlerini reddet (orn. "33-34-35-36"den gelen 33/34/2035)
                    try:
                        day_v, mon_v = int(parts[0]), int(parts[1])
                        if not (1 <= day_v <= 31 and 1 <= mon_v <= 12):
                            if DEBUG:
                                print(f"    [-] INVALID: day={day_v} month={mon_v}")
                            continue
                    except ValueError:
                        continue
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
        #if det.y_center - current_row[-1].y_center >= y_tolerance:
        #    rows.append(sorted(current_row, key=lambda d: d.x_min))
        #    current_row = [det]
        #    tl, tr, br, bl = det.bbox
        #    band_m1, band_b1 = get_line_equation_from_two_points(tl, tr)
        #    band_m2, band_b2 = get_line_equation_from_two_points(bl, br)
        #    continue

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

    # OCR hatası: son nokta virgül yerine yazılmış olabilir (2.777.63 → 2.777,63)
    # Pattern match'ten önce her iki versiyonu dene
    candidates = [cleaned]
    dot_fixed = re.sub(r'\.(\d{2})$', r',\1', cleaned)
    if dot_fixed != cleaned:
        candidates.append(dot_fixed)

    for candidate in candidates:
        m = re.match(price_pattern, candidate)
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
                continue
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


def _try_inline_split(
    d: Detection,
    price_pattern: str,
) -> tuple[Optional[Detection], Optional[Detection]]:
    """
    'ÜRÜN ADI *19,90' veya 'NET TOPLAM: *2.777,63' gibi tek bir detection'ı
    isim ve fiyat parçalarına ayır.

    PRICE_PREFIX_CLEANUP normalleştirmesi sonrası '*' üzerinden böler,
    sağ parçayı parse_price() ile doğrular (double-dot OCR fix dahil).
    Başarısız olursa (None, None) döndürür.
    """
    cleaned = d.text.strip()
    for p, r in PRICE_PREFIX_CLEANUP:
        cleaned = re.sub(p, r, cleaned)

    star_idx = cleaned.rfind('*')
    if star_idx < 0:
        return None, None

    left  = cleaned[:star_idx].strip()
    right = cleaned[star_idx:]          # '*' dahil

    if parse_price(right, price_pattern) is None:
        return None, None

    price_det = Detection(
        text=right, confidence=d.confidence,
        x_min=d.x_min, x_max=d.x_max,
        y_min=d.y_min, y_max=d.y_max, y_center=d.y_center, bbox=d.bbox,
    )
    if left:
        name_det = Detection(
            text=left, confidence=d.confidence,
            x_min=d.x_min, x_max=d.x_max,
            y_min=d.y_min, y_max=d.y_max, y_center=d.y_center, bbox=d.bbox,
        )
        return name_det, price_det
    return None, price_det


def split_row_into_name_price(
    row: list[Detection],
    price_pattern: str,
) -> tuple[list[Detection], list[Detection]]:
    """
    Bir satırı (name_dets, price_dets) olarak ayır.

    Aşama 1 — Standart split:
      Sağdan sola tara, parse_price() ile doğrudan fiyat bul.
      Fiyat detection'ından soluna kadar olan kısım isimdir.

    Aşama 2 — name_dets'te inline ara:
      price_dets boşsa her name detection'ında _try_inline_split dene.
      (Örn: 'ÜRÜN ADI *19,90' tek blob halinde gelmiş)

    Aşama 3 — price_dets'te inline ara:
      name_dets boşsa ve price tek detectionsa _try_inline_split dene.
      (Örn: OCR 'NET TOPLAM: *2.777,63' gibi karıştırmış)
    """
    # Aşama 1
    first_price_idx = None
    for i in range(len(row) - 1, -1, -1):
        if parse_price(row[i].text, price_pattern) is not None:
            first_price_idx = i
            break

    if first_price_idx is not None:
        name_dets = list(row[:first_price_idx])
        price_dets = list(row[first_price_idx:])
    else:
        name_dets = list(row)
        price_dets = []

    # Aşama 2
    if not price_dets and name_dets:
        new_name_dets: list[Detection] = []
        for d in name_dets:
            name_part, price_part = _try_inline_split(d, price_pattern)
            if price_part is not None:
                if name_part:
                    new_name_dets.append(name_part)
                price_dets.append(price_part)
            else:
                new_name_dets.append(d)
        name_dets = new_name_dets

    # Aşama 3
    if not name_dets and len(price_dets) == 1:
        name_part, price_part = _try_inline_split(price_dets[0], price_pattern)
        if name_part and price_part:
            name_dets = [name_part]
            price_dets = [price_part]

    return name_dets, price_dets


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


def merge_two_line_rows(
    rows: list[list[Detection]],
    price_pattern: str,
    total_pattern: str | None = None,
) -> list[list[Detection]]:
    """
    Metro gibi iki satırlı ürün formatını birleştir:

    Durum: ÜRÜN_ADI  →  (barkod/miktar bilgisi) *fiyat
    1. satır: saf isim, fiyat yok
    2. satır: sona yakın fiyat var (barkod/KDV gibi gürültü olabilir)

    Fiyatsız satır tespit edilince, hemen ardından gelen satırdan
    sadece son fiyat detection'ı alınır ve isim satırına eklenir.
    2. satırın geri kalanı (barkod vb.) göz ardı edilir.
    Toplam satırları birleştirilmez.
    """
    result = []
    i = 0
    while i < len(rows):
        row = rows[i]
        row_text = " ".join(d.text for d in row)
        is_total = total_pattern and re.search(total_pattern, row_text, re.IGNORECASE)

        # Bu satırda fiyat yok ve toplam değil → bir sonraki satırdan fiyat çek
        if not is_total and not row_has_price(row, price_pattern) and i + 1 < len(rows):
            next_row = rows[i + 1]
            next_text = " ".join(d.text for d in next_row)
            next_is_total = total_pattern and re.search(total_pattern, next_text, re.IGNORECASE)

            if not next_is_total and row_has_price(next_row, price_pattern):
                # Sadece son fiyat detection'ını al (barkod/gürültü bırak)
                price_det = None
                for d in reversed(next_row):
                    if parse_price(d.text, price_pattern) is not None:
                        price_det = d
                        break
                if price_det is not None:
                    if DEBUG:
                        print(f"  [TWO_LINE] isim='{row_text[:40]}' + fiyat='{price_det.text}'")
                    result.append(row + [price_det])
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

    # İki satırlı format: 1. satır isim, 2. satırın sonu fiyat (ör. Metro)
    if profile.get("parse_mode") == "two_line":
        rows = merge_two_line_rows(rows, profile["price_pattern"],
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
            # split_row_into_name_price: tek blob 'NET TOPLAM: *2.777,63' durumunu da yakalar
            _, price_dets_t = split_row_into_name_price(row, profile["price_pattern"])
            for pd in reversed(price_dets_t):
                price = parse_price(pd.text, profile["price_pattern"])
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

        # Fiyat ve isim detection'larını ayır (inline split dahil)
        name_dets, price_dets = split_row_into_name_price(row, profile["price_pattern"])

        if DEBUG:
            print(f"    [*] Name dets: {[d.text for d in name_dets]}")
            print(f"    [*] Price dets: {[d.text for d in price_dets]}")

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

    import argparse
    import io

    ap = argparse.ArgumentParser(
        description="Fiş parser — OCR JSON'dan fiş çıkarır, isteğe bağlı hledger/Excel günceller."
    )
    ap.add_argument("input", nargs="?", help="OCR JSON dosyası veya klasör (verilmezse config'deki ocr_cache kullanılır)")
    ap.add_argument("--hledger", metavar="JOURNAL", help="hledger journal dosyası (güncelleme için)")
    ap.add_argument("--excel", metavar="EXCEL", help="Excel dosyası (güncelleme için)")
    ap.add_argument("--sheet", metavar="SHEET", help="Excel sheet adı (--excel ile kullanılır)")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--mismatch-only", action="store_true")
    ap.add_argument("--force", action="store_true", help="Daha önce işlenmiş fişleri tekrar güncelle")
    args = ap.parse_args()

    DEBUG = args.debug
    mismatch_only = args.mismatch_only
    force = args.force

    journal_path = Path(args.hledger) if args.hledger else None
    excel_path   = Path(args.excel)   if args.excel   else None
    sheet_name   = args.sheet

    # Kuralları bir kez yükle (hledger veya excel aktifse)
    rules = None
    if journal_path or excel_path:
        from rules import load_rules
        from config import RULES_FILE, RULES_LEARNED
        rules = load_rules(RULES_FILE)
        if RULES_LEARNED.exists():
            rules = load_rules(RULES_LEARNED) + rules

    from config import OCR_CACHE_DIR
    from snapshots import save_snapshot, check_snapshot, totals_match
    from processed import is_processed, mark_processed

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
            if snap_diffs and totals_match(receipt):
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

        if receipt is None:
            return None

        ocr_name = ocr_path.name
        hledger_pending = None  # (ocr_name, tx, new_lines)
        excel_pending   = None  # (ocr_name, receipt, categorized)

        # ── hledger önizleme + onay toplama ───────────────────────────────────
        if journal_path:
            from update_journal import (
                parse_journal, find_matching_transaction,
                build_new_transaction, preview, categorize_items,
            )
            already = is_processed(ocr_name, "hledger")
            if already and not force:
                print(f"  ⚠️  hledger: zaten işlendi ({already.get('updated_at', '?')}, "
                      f"{already.get('total', '?')} TL) — atlanıyor")
            else:
                categorized  = categorize_items(receipt, rules)
                transactions = parse_journal(journal_path)
                tx           = find_matching_transaction(receipt, transactions)
                if tx is None:
                    print(f"  ❌ hledger: eşleşen transaction bulunamadı "
                          f"({receipt.date}  {receipt.total:.2f} TL)")
                else:
                    new_lines = build_new_transaction(tx, categorized, receipt)
                    preview(new_lines)
                    print("\nJournal güncellensin mi? [e/H] ", end="")
                    try:
                        answer = input().strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        answer = "h"
                    if answer == "e":
                        hledger_pending = (ocr_name, tx, new_lines)

        # ── Excel önizleme + onay toplama ─────────────────────────────────────
        if excel_path:
            from update_journal import categorize_items
            from update_excel import preview_excel, read_excel_to_accounts
            from rules import DEFAULT_ACCOUNT
            already = is_processed(ocr_name, "excel")
            if already and not force:
                print(f"  ⚠️  Excel: zaten işlendi ({already.get('updated_at', '?')}, "
                      f"{already.get('total', '?')} TL) — atlanıyor")
            else:
                categorized = categorize_items(receipt, rules)

                # Default atanan kalemleri Excel'deki mevcut to-account ile doldur
                excel_accounts = read_excel_to_accounts(excel_path, receipt, sheet_name)
                if excel_accounts:
                    categorized = [
                        (item, excel_accounts.get(round(item.amount, 2), account)
                               if account == DEFAULT_ACCOUNT else account)
                        for item, account in categorized
                    ]

                preview_excel(categorized, receipt)
                print("\nExcel güncellensin mi? [e/H] ", end="")
                try:
                    answer = input().strip().lower()
                except (EOFError, KeyboardInterrupt):
                    answer = "h"
                if answer == "e":
                    excel_pending = (ocr_name, receipt, categorized)

        return hledger_pending, excel_pending

    # ── Dosyaları topla ────────────────────────────────────────────────────────
    input_path = Path(args.input) if args.input else OCR_CACHE_DIR
    if not input_path.exists():
        print(f"❌ Path bulunamadı: {input_path.resolve()}")
        print(f"   İpucu: OCR cache için config.toml'daki ocr_cache değeri: {OCR_CACHE_DIR}")
        sys.exit(1)
    if input_path.is_dir():
        ocr_files = sorted(
            p for p in input_path.iterdir()
            if p.suffix.lower() == ".json" and p.name != "processed.json"
        )
    else:
        ocr_files = [input_path]

    # Faz 1: parse + önizle + onay topla
    pending_hledger: list = []  # [(ocr_name, tx, new_lines), ...]
    pending_excel:   list = []  # [(ocr_name, receipt, categorized), ...]

    for ocr_file in ocr_files:
        result = _process(ocr_file)
        if result is None:
            continue
        h, e = result
        if h:
            pending_hledger.append(h)
        if e:
            pending_excel.append(e)

    # Faz 2: toplu güncelleme
    if pending_hledger:
        from update_journal import update_journal
        print(f"\n── hledger güncelleniyor ({len(pending_hledger)} transaction) ──")
        for ocr_name, tx, new_lines in pending_hledger:
            update_journal(journal_path, tx, new_lines)
            total_str = f"{tx.total:.2f} TL" if tx.total else "?"
            print(f"  ✓ {tx.date}  {total_str}")
            mark_processed(ocr_name, "hledger", {
                "store":   tx.description,
                "date":    tx.date,
                "total":   tx.total,
                "tx_line": tx.start_line + 1,
            })
        print(f"  Toplam {len(pending_hledger)} transaction güncellendi → {journal_path}")

    if pending_excel:
        from update_excel import update_excel_batch
        print(f"\n── Excel güncelleniyor ({len(pending_excel)} fiş) ──")
        receipts_and_cats = [(r, c) for _, r, c in pending_excel]
        results = update_excel_batch(excel_path, receipts_and_cats, sheet_name)
        ok_count = 0
        for (ocr_name, receipt, _), from_row in zip(pending_excel, results):
            if from_row is not None:
                ok_count += 1
                mark_processed(ocr_name, "excel", {
                    "store":  receipt.store,
                    "date":   receipt.date,
                    "total":  receipt.total,
                    "items":  len(receipt.items),
                    "sheet":  sheet_name or "active",
                    "row":    from_row,
                })
        print(f"  Toplam {ok_count}/{len(pending_excel)} fiş güncellendi → {excel_path}")

if __name__ == "__main__":
    main()
