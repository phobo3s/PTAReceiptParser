import cv2
import numpy as np
from pathlib import Path

def order_points(pts):
    """Sort coordinates: top-left, top-right, bottom-right, bottom-left."""
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    
    return rect

def four_point_transform(image, pts):
    """Applies perspective transform to map a 4-point polygon to a flat rectangle."""
    rect = order_points(pts)
    (tl, tr, br, bl) = rect

    widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
    widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
    maxWidth = max(int(widthA), int(widthB))

    heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
    heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
    maxHeight = max(int(heightA), int(heightB))

    dst = np.array([
        [0, 0],
        [maxWidth - 1, 0],
        [maxWidth - 1, maxHeight - 1],
        [0, maxHeight - 1]], dtype="float32")

    M = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(image, M, (maxWidth, maxHeight))
    return warped

def deskew(image):
    """Detects text orientation and rotates the image to make it perfectly horizontal."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.bitwise_not(gray)
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    
    coords = np.column_stack(np.where(thresh > 0))
    angle = cv2.minAreaRect(coords)[-1]

    # Handle OpenCV angle normalization mapping
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    # Prevent extreme rotations (e.g. if the image is already vertical)
    if abs(angle) > 15:
        return image

    (h, w) = image.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return rotated

def preProcessImage(image_path: Path):
    """
    Main pipeline: Reads image -> Perspective Transform -> Deskew -> Adaptive Threshold -> Save
    """
    print(f"[DEBUG] Started processing: {image_path.name}")
    
    # 1. Read the image
    image = cv2.imread(str(image_path))
    if image is None:
        print(f"[ERROR] Could not read image: {image_path}")
        return

    orig = image.copy()
    
    # 2. Attempt to find receipt boundaries (4 points)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(blurred, 75, 200)

    cnts, _ = cv2.findContours(edged.copy(), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    cnts = sorted(cnts, key=cv2.contourArea, reverse=True)[:5]
    
    screenCnt = None
    for c in cnts:
        peri = cv2.arcLength(c, True)
        # Approximate the contour to handle slight curves/imperfections
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) == 4:
            screenCnt = approx
            break

    # Apply perspective transform if a rectangular receipt is detected
    if screenCnt is not None:
        print("[DEBUG] Found 4 corners. Applying Perspective Transform.")
        warped = four_point_transform(orig, screenCnt.reshape(4, 2))
    else:
        print("[DEBUG] Could not detect clean corners. Proceeding with the original image.")
        warped = orig

    # 3. Deskew to fix text slant
    print("[DEBUG] Deskewing the image.")
    deskewed = deskew(warped)

    # 4. Adaptive Thresholding to flatten light gradients and remove shadows
    print("[DEBUG] Applying Adaptive Thresholding.")
    gray_warped = cv2.cvtColor(deskewed, cv2.COLOR_BGR2GRAY)
    
    # blockSize=21 and C=10 are standard sweet spots for receipt thermal prints
    final_thresh = cv2.adaptiveThreshold(
        gray_warped, 255, 
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
        cv2.THRESH_BINARY, 
        21, 10
    )

    # 5. Directory management and saving
    out_dir = Path(".processedReceipts")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    out_path = out_dir / image_path.name
    cv2.imwrite(str(out_path), final_thresh)
    print(f"[DEBUG] Success! Saved to: {out_path}\n")
    return True 

# --- EXECUTION ---
if __name__ == "__main__":
    receipts_dir = Path("Receipts")  # Büyük R
    if not receipts_dir.exists():
        print(f"[ERROR] Directory '{receipts_dir}' not found.")
    else:
        # Loop through all jpg/jpeg/png files in the directory
        for img_file in receipts_dir.glob("*.[jJpP]*"):
            preProcessImage(img_file)