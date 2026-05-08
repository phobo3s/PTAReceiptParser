---
component_id: 2
component_name: Spatial & Semantic Parser
---

# Spatial & Semantic Parser

## Component Description

The core transformation engine that converts unstructured OCR bounding boxes into structured receipt objects. It uses geometric algorithms to reconstruct rows and columns, and integrates LLM logic (Claude API) to handle complex layouts or ambiguous text extraction.

---

## Key References:

### c:\PTAReceiptParser\parser.py (lines 42-47)
```
    store: str
    date: Optional[str]
    items: list[ReceiptItem]
    total: Optional[float]
    raw_detections: list[Detection]

```

### c:\PTAReceiptParser\parser.py (lines 222-271)
```
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
```

### c:\PTAReceiptParser\llm_parser.py (lines 147-180)
```
def parse_with_llm(
    ocr_json: dict,
    api_key: Optional[str] = None,
    cache_key: Optional[str] = None,
) -> Receipt:
    """
    OCR JSON → Receipt (LLM tabanlı).

    cache_key: .parse_llm_cache/{cache_key}.json — None ise cache kullanılmaz.
    api_key: ANTHROPIC_API_KEY env'den de alınabilir.
    """
    if api_key is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY gerekli (env veya parametre).")

    detections = load_detections(ocr_json)

    # Cache kontrolü
    cache_file: Optional[Path] = None
    if cache_key:
        CACHE_DIR.mkdir(exist_ok=True)
        cache_file = CACHE_DIR / f"{cache_key}.json"
        if cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            return _build_receipt(data, detections)

    text = _ocr_to_text(ocr_json)
    data = _call_claude(text, api_key)

    if cache_file:
        cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    return _build_receipt(data, detections)
```


## Source Files:

- `llm_parser.py`
- `parser.py`

