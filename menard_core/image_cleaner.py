import logging
import io
import os
from PIL import Image
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)

# Attempt to import cv2 and numpy; fall back gracefully if unavailable
try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    logger.warning("OpenCV (cv2) or NumPy is not installed. Watermark removal will be skipped.")


def detect_and_remove_watermarks(image_input):
    """
    Intelligently detects and removes watermarks (stock photo text stamps, corner logos,
    semi-transparent overlays, timestamps) from an image array or PIL Image using
    computer vision morphological filtering and Navier-Stokes / Telea inpainting.

    :param image_input: cv2 numpy array (BGR/BGRA) or PIL Image or bytes
    :return: (cleaned_pil_image, pixels_inpainted_count)
    """
    if not CV2_AVAILABLE:
        if isinstance(image_input, Image.Image):
            return image_input, 0
        elif isinstance(image_input, str):
            return Image.open(image_input), 0
        elif hasattr(image_input, 'read'):
            image_input.seek(0)
            return Image.open(image_input), 0
        return None, 0

    # 1. Standardize input to cv2 BGR/BGRA array
    img = None
    has_alpha = False
    alpha_channel = None

    if isinstance(image_input, np.ndarray):
        img = image_input.copy()
    elif isinstance(image_input, str):
        img = cv2.imread(image_input, cv2.IMREAD_UNCHANGED)
    elif hasattr(image_input, 'read'):
        image_input.seek(0)
        file_bytes = image_input.read()
        nparr = np.frombuffer(file_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
    elif isinstance(image_input, bytes):
        nparr = np.frombuffer(image_input, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
    elif isinstance(image_input, Image.Image):
        # Convert PIL to cv2
        pil_mode = image_input.mode
        if pil_mode == 'RGBA':
            has_alpha = True
            rgba = np.array(image_input)
            img = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)
        else:
            rgb = np.array(image_input.convert('RGB'))
            img = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    if img is None:
        raise ValueError("Could not decode image for watermark cleaning.")

    # Separate Alpha channel if present
    if len(img.shape) == 3 and img.shape[2] == 4:
        has_alpha = True
        bgr = img[:, :, :3]
        alpha_channel = img[:, :, 3]
    elif len(img.shape) == 3:
        bgr = img
    elif len(img.shape) == 2:
        bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    else:
        bgr = img

    h, w, _ = bgr.shape
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    watermark_mask = np.zeros((h, w), dtype=np.uint8)

    # 2. Corner & Border Region High-Contrast Contour Scan (Stamps, Copyrights, Logos)
    # Check bottom strip (bottom 18% of the image)
    bottom_h = max(int(h * 0.18), 10)
    bottom_roi = gray[h - bottom_h:h, :]

    sobelx = cv2.Sobel(bottom_roi, cv2.CV_64F, 1, 0, ksize=3)
    sobely = cv2.Sobel(bottom_roi, cv2.CV_64F, 0, 1, ksize=3)
    mag = np.sqrt(sobelx**2 + sobely**2)
    mag = np.uint8(np.clip(mag, 0, 255))

    _, thresh = cv2.threshold(mag, 55, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    thresh_cleaned = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(thresh_cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        x_c, y_c, w_c, h_c = cv2.boundingRect(c)
        area = cv2.contourArea(c)
        if 20 < area < (w * bottom_h * 0.25) and h_c < bottom_h * 0.8:
            aspect = w_c / max(h_c, 1)
            if 0.1 < aspect < 20:
                cv2.drawContours(watermark_mask[h - bottom_h:h, :], [c], -1, 255, -1)

    # 3. Semi-Transparent / Light & Dark Text Watermark Filter
    # Top-hat highlights bright text overlays on darker background
    kernel_tophat = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel_tophat)
    _, tophat_mask = cv2.threshold(tophat, 40, 255, cv2.THRESH_BINARY)

    # Black-hat highlights dark text overlays on brighter background
    blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel_tophat)
    _, blackhat_mask = cv2.threshold(blackhat, 40, 255, cv2.THRESH_BINARY)

    high_freq_text = cv2.bitwise_or(tophat_mask, blackhat_mask)

    # Watermarks are predominantly placed in the 4 corners and along edges
    corners_mask = np.zeros((h, w), dtype=np.uint8)
    corners_mask[:int(h * 0.15), int(w * 0.70):] = 255  # Top Right
    corners_mask[:int(h * 0.15), :int(w * 0.30)] = 255  # Top Left
    corners_mask[int(h * 0.80):, int(w * 0.60):] = 255  # Bottom Right
    corners_mask[int(h * 0.80):, :int(w * 0.40)] = 255  # Bottom Left

    corner_watermarks = cv2.bitwise_and(high_freq_text, corners_mask)
    watermark_mask = cv2.bitwise_or(watermark_mask, corner_watermarks)

    # 4. Inpainting
    dilation_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    watermark_mask = cv2.dilate(watermark_mask, dilation_kernel, iterations=1)
    pixels_cleaned = int(cv2.countNonZero(watermark_mask))

    if pixels_cleaned > 0:
        cleaned_bgr = cv2.inpaint(bgr, watermark_mask, inpaintRadius=3, flags=cv2.INPAINT_TELEA)
    else:
        cleaned_bgr = bgr

    # 5. Reconstruct RGB / RGBA PIL Image
    if has_alpha and alpha_channel is not None:
        cleaned_rgb = cv2.cvtColor(cleaned_bgr, cv2.COLOR_BGR2RGB)
        cleaned_pil = Image.fromarray(cleaned_rgb)
        alpha_pil = Image.fromarray(alpha_channel)
        cleaned_pil.putalpha(alpha_pil)
    else:
        cleaned_rgb = cv2.cvtColor(cleaned_bgr, cv2.COLOR_BGR2RGB)
        cleaned_pil = Image.fromarray(cleaned_rgb)

    return cleaned_pil, pixels_cleaned


def clean_uploaded_image(uploaded_file, default_format=None, quality=95):
    """
    Takes a Django UploadedFile (or File-like object), executes intelligent
    watermark removal, and returns a Django ContentFile with the cleaned image data,
    preserving the original filename and mime format.

    :param uploaded_file: Django UploadedFile instance
    :param default_format: Optional image format ('JPEG', 'PNG', 'WEBP')
    :param quality: Output compression quality (default: 95)
    :return: Django ContentFile or the original uploaded_file if processing is bypassed
    """
    if not uploaded_file:
        return uploaded_file

    filename = getattr(uploaded_file, 'name', 'cleaned_image.jpg')
    ext = os.path.splitext(filename)[1].lower()

    # Determine format
    img_format = default_format
    if not img_format:
        if ext in ['.png']:
            img_format = 'PNG'
        elif ext in ['.webp']:
            img_format = 'WEBP'
        else:
            img_format = 'JPEG'

    try:
        # Detect and clean watermarks
        cleaned_pil, pixels_count = detect_and_remove_watermarks(uploaded_file)
        if cleaned_pil is None:
            uploaded_file.seek(0)
            return uploaded_file

        # Save to buffer
        output_buffer = io.BytesIO()
        if img_format == 'JPEG':
            if cleaned_pil.mode in ('RGBA', 'P', 'LA'):
                cleaned_pil = cleaned_pil.convert('RGB')
            cleaned_pil.save(output_buffer, format='JPEG', quality=quality, optimize=True)
        elif img_format == 'PNG':
            cleaned_pil.save(output_buffer, format='PNG', optimize=True)
        elif img_format == 'WEBP':
            cleaned_pil.save(output_buffer, format='WEBP', quality=quality)
        else:
            cleaned_pil.save(output_buffer, format=img_format, quality=quality)

        output_buffer.seek(0)
        content_file = ContentFile(output_buffer.getvalue(), name=filename)
        logger.info(f"Cleaned uploaded image '{filename}' ({pixels_count} watermark pixels inpainted).")
        return content_file

    except Exception as e:
        logger.error(f"Error while cleaning image '{filename}': {e}. Returning original file.")
        if hasattr(uploaded_file, 'seek'):
            uploaded_file.seek(0)
        return uploaded_file
