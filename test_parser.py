import unittest
import json
from parser import Detection, group_into_rows, parse_receipt, STORE_PROFILES

class TestReceiptParser(unittest.TestCase):

    def test_group_into_rows_straight_lines(self):
        detections = [
            Detection(text="Item A", confidence=0.9, x_min=10, x_max=50, y_min=100, y_max=110, y_center=105),
            Detection(text="Price A", confidence=0.9, x_min=150, x_max=190, y_min=100, y_max=110, y_center=105),
            Detection(text="Item B", confidence=0.9, x_min=10, x_max=50, y_min=120, y_max=130, y_center=125),
            Detection(text="Price B", confidence=0.9, x_min=150, x_max=190, y_min=120, y_max=130, y_center=125),
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
            Detection(text="Item A", confidence=0.9, x_min=10, x_max=50, y_min=100, y_max=110, y_center=105),
            Detection(text="Price A", confidence=0.9, x_min=155, x_max=195, y_min=103, y_max=113, y_center=108), # Slightly skewed
            Detection(text="Item B", confidence=0.9, x_min=15, x_max=55, y_min=118, y_max=128, y_center=123), # Next row
            Detection(text="Price B", confidence=0.9, x_min=160, x_max=200, y_min=121, y_max=131, y_center=126), # Slightly skewed
        ]
        # y_tolerance ve overlap_threshold ile test
        rows = group_into_rows(detections, y_tolerance=5, overlap_threshold=0.5)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[0]), 2)
        self.assertEqual(rows[0][0].text, "Item A")
        self.assertEqual(rows[0][1].text, "Price A")
        self.assertEqual(len(rows[1]), 2)
        self.assertEqual(rows[1][0].text, "Item B")
        self.assertEqual(rows[1][1].text, "Price B")

    def test_group_into_rows_low_confidence_filtered(self):
        detections = [
            Detection(text="Item A", confidence=0.9, x_min=10, x_max=50, y_min=100, y_max=110, y_center=105),
            Detection(text="Noise", confidence=0.5, x_min=20, x_max=60, y_min=102, y_max=112, y_center=107), # Low confidence
            Detection(text="Price A", confidence=0.9, x_min=150, x_max=190, y_min=100, y_max=110, y_center=105),
        ]
        rows = group_into_rows(detections, y_tolerance=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 2)
        self.assertEqual(rows[0][0].text, "Item A")
        self.assertEqual(rows[0][1].text, "Price A")

    def test_parse_receipt_bim_sample_1(self):
        # .ocr_cache/WhatsApp Image 2026-03-27 at 09.05.32.json dosyasını yükle
        with open(".ocr_cache/WhatsApp Image 2026-03-27 at 09.05.32.json", "r", encoding="utf-8") as f:
            ocr_json = json.load(f)
        
        receipt = parse_receipt(ocr_json)
        
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.store, "BİM")
        self.assertEqual(receipt.date, "2026-03-26")
        self.assertAlmostEqual(receipt.total, 333.07, places=2)
        self.assertEqual(len(receipt.items), 10) # Önceki çalıştırma 10 item tespit etmişti
        
        # İlk öğeyi kontrol et
        self.assertEqual(receipt.items[0].name, "KEKCiK.KAP30G PiNGUI")
        self.assertAlmostEqual(receipt.items[0].amount, 26.00, places=2)

        # Tartılı ürünü kontrol et
        self.assertEqual(receipt.items[6].name, "PATATES (0.74kg × 19.75)")
        self.assertAlmostEqual(receipt.items[6].amount, 14.62, places=2)

    def test_parse_receipt_tankar_sample_1(self):
        # .ocr_cache/WhatsApp Image 2026-03-27 at 09.05.48.json dosyasını yükle
        with open(".ocr_cache/WhatsApp Image 2026-03-27 at 09.05.48.json", "r", encoding="utf-8") as f:
            ocr_json = json.load(f)
        
        receipt = parse_receipt(ocr_json)
        
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.store, "Tankar")
        self.assertEqual(receipt.date, "2026-03-11")
        self.assertAlmostEqual(receipt.total, 250.00, places=2)
        self.assertEqual(len(receipt.items), 1) 
        self.assertEqual(receipt.items[0].name, "YIKAMA(OTOMATIK)")
        self.assertAlmostEqual(receipt.items[0].amount, 250.00, places=2)

    def test_parse_receipt_tankar_sample_2(self):
        # .ocr_cache/WhatsApp Image 2026-03-27 at 09.05.57.json dosyasını yükle
        with open(".ocr_cache/WhatsApp Image 2026-03-27 at 09.05.57.json", "r", encoding="utf-8") as f:
            ocr_json = json.load(f)
        
        receipt = parse_receipt(ocr_json)
        
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.store, "Tankar")
        self.assertEqual(receipt.date, "2026-03-09")
        self.assertAlmostEqual(receipt.total, 2537.47, places=2)
        self.assertEqual(len(receipt.items), 1) 
        self.assertEqual(receipt.items[0].name, "MOTORIN SVPD (38.4LTX × 2537.00)")
        self.assertAlmostEqual(receipt.items[0].amount, 2537.00, places=2) # Etiketden gelen fiyatı kontrol et

    def test_parse_receipt_bim_sample_2(self):
        # .ocr_cache/WhatsApp Image 2026-04-07 at 08.45.16.json dosyasını yükle
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