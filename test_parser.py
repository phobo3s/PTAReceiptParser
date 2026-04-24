import unittest
import json
from parser import Detection, group_into_rows, parse_receipt, STORE_PROFILES

def make_det(text, confidence, x_min, x_max, y_min, y_max, y_center):
    bbox = [[x_min, y_min], [x_max, y_min], [x_max, y_max], [x_min, y_max]]
    return Detection(text=text, confidence=confidence,
                     x_min=x_min, x_max=x_max,
                     y_min=y_min, y_max=y_max, y_center=y_center, bbox=bbox)

class TestReceiptParser(unittest.TestCase):

    def test_group_into_rows_straight_lines(self):
        detections = [
            make_det("Item A",  0.9, 10,  50,  100, 110, 105),
            make_det("Price A", 0.9, 150, 190, 100, 110, 105),
            make_det("Item B",  0.9, 10,  50,  120, 130, 125),
            make_det("Price B", 0.9, 150, 190, 120, 130, 125),
        ]
        rows = group_into_rows(detections, y_tolerance=5)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[0]), 2)
        self.assertEqual(rows[0][0].text, "Item A")
        self.assertEqual(rows[0][1].text, "Price A")
        self.assertEqual(len(rows[1]), 2)
        self.assertEqual(rows[1][0].text, "Item B")
        self.assertEqual(rows[1][1].text, "Price B")

    def test_group_into_rows_skewed_lines_overlap(self):
        detections = [
            make_det("Item A",  0.9, 10,  50,  100, 110, 105),
            make_det("Price A", 0.9, 155, 195, 103, 113, 108),
            make_det("Item B",  0.9, 15,  55,  118, 128, 123),
            make_det("Price B", 0.9, 160, 200, 121, 131, 126),
        ]
        rows = group_into_rows(detections, y_tolerance=5)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[0]), 2)
        self.assertEqual(rows[0][0].text, "Item A")
        self.assertEqual(rows[0][1].text, "Price A")
        self.assertEqual(len(rows[1]), 2)
        self.assertEqual(rows[1][0].text, "Item B")
        self.assertEqual(rows[1][1].text, "Price B")

    def test_group_into_rows_low_confidence_filtered(self):
        detections = [
            make_det("Item A",  0.9, 10,  50,  100, 110, 105),
            make_det("Noise",   0.5, 20,  60,  102, 112, 107),
            make_det("Price A", 0.9, 150, 190, 100, 110, 105),
        ]
        rows = group_into_rows(detections, y_tolerance=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 2)
        self.assertEqual(rows[0][0].text, "Item A")
        self.assertEqual(rows[0][1].text, "Price A")

    def test_parse_receipt_bim_sample_1(self):
        with open(".ocr_cache/WhatsApp Image 2026-03-27 at 09.05.32.json", "r", encoding="utf-8") as f:
            ocr_json = json.load(f)

        receipt = parse_receipt(ocr_json)

        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.store, "BİM")
        self.assertEqual(receipt.date, "2026-03-26")
        self.assertAlmostEqual(receipt.total, 333.07, places=2)
        self.assertEqual(len(receipt.items), 9)

        # OCR 'i→İ' düzeltmesi uygulandı; son 'I' OCR tarafından düşürüldü
        self.assertEqual(receipt.items[0].name, "KEKCİK.KAP30G PİNGU")
        self.assertAlmostEqual(receipt.items[0].amount, 26.00, places=2)

        # Tartılı ürün: indeks 5, trailing KDV kodu temizlendi
        self.assertEqual(receipt.items[5].name, "PATATES (0.74kg × 19.75)")
        self.assertAlmostEqual(receipt.items[5].amount, 14.62, places=2)

    def test_parse_receipt_tankar_sample_1(self):
        # YIKAMA(OTOMATİK) fişi — WhatsApp Image 2026-04-09 at 15.52.24
        with open(".ocr_cache/WhatsApp Image 2026-04-09 at 15.52.24.json", "r", encoding="utf-8") as f:
            ocr_json = json.load(f)

        receipt = parse_receipt(ocr_json)

        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.store, "Tankar")
        self.assertEqual(receipt.date, "2026-03-11")
        self.assertAlmostEqual(receipt.total, 250.00, places=2)
        self.assertEqual(len(receipt.items), 1)
        # OCR 'OTOMATİK' yerine 'OIOMATIK' okuyor (uppercase I, i→İ uygulanmıyor)
        self.assertEqual(receipt.items[0].name, "YIKAMA(OIOMATIK)")
        self.assertAlmostEqual(receipt.items[0].amount, 250.00, places=2)

    def test_parse_receipt_tankar_sample_2(self):
        # MOTORIN fişi — WhatsApp Image 2026-04-09 at 15.28.49
        with open(".ocr_cache/WhatsApp Image 2026-04-09 at 15.28.49.json", "r", encoding="utf-8") as f:
            ocr_json = json.load(f)

        receipt = parse_receipt(ocr_json)

        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.store, "Tankar")
        self.assertEqual(receipt.date, "2026-03-09")
        self.assertAlmostEqual(receipt.total, 2537.47, places=2)
        self.assertEqual(len(receipt.items), 1)
        self.assertEqual(receipt.items[0].name, "MOTORINSVPD")
        self.assertAlmostEqual(receipt.items[0].amount, 2537.47, places=2)

    def test_parse_receipt_bim_sample_2(self):
        with open(".ocr_cache/WhatsApp Image 2026-04-07 at 08.45.16.json", "r", encoding="utf-8") as f:
            ocr_json = json.load(f)

        receipt = parse_receipt(ocr_json)

        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.store, "BİM")
        self.assertEqual(receipt.date, "2026-04-06")
        self.assertAlmostEqual(receipt.total, 87.50, places=2)
        self.assertEqual(len(receipt.items), 1)
        self.assertEqual(receipt.items[0].name, "YUMURTA 10 LU 63-73G")
        self.assertAlmostEqual(receipt.items[0].amount, 87.50, places=2)

if __name__ == '__main__':
    unittest.main()
