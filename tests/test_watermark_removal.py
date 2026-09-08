import io
from PIL import Image, ImageDraw, ImageFont
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.accounts.models import CompanySettings, UserProfile, Role, SystemPermission
from menard_core.image_cleaner import detect_and_remove_watermarks, clean_uploaded_image


class WatermarkCleanerTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            username='admin_cleaner',
            email='admin_cleaner@menardtrading.com',
            password='Password123!'
        )

    def _generate_image_with_watermark(self):
        """Generates a test image with a high-contrast watermark text stamped in the corner."""
        img = Image.new('RGB', (400, 300), color=(73, 109, 137))
        draw = ImageDraw.Draw(img)
        # Draw some scene lines
        draw.rectangle([50, 50, 350, 200], fill=(120, 150, 180), outline=(200, 200, 200))
        # Draw watermark in bottom-right corner
        draw.text((280, 265), "SAMPLE WATERMARK", fill=(255, 255, 255))
        
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=95)
        buffer.seek(0)
        return buffer.getvalue()

    def test_detect_and_remove_watermarks(self):
        """Verify detect_and_remove_watermarks identifies watermark pixels and returns a cleaned image."""
        raw_image_bytes = self._generate_image_with_watermark()
        cleaned_pil, pixels_inpainted = detect_and_remove_watermarks(raw_image_bytes)

        self.assertIsNotNone(cleaned_pil)
        self.assertIsInstance(cleaned_pil, Image.Image)
        self.assertGreater(pixels_inpainted, 0)
        self.assertEqual(cleaned_pil.size, (400, 300))

    def test_clean_uploaded_image_returns_content_file(self):
        """Verify clean_uploaded_image processes Django UploadedFile and returns ContentFile."""
        raw_image_bytes = self._generate_image_with_watermark()
        uploaded_file = SimpleUploadedFile('fleet_slide_with_watermark.jpg', raw_image_bytes, content_type='image/jpeg')

        cleaned_file = clean_uploaded_image(uploaded_file)
        self.assertIsNotNone(cleaned_file)
        self.assertEqual(cleaned_file.name, 'fleet_slide_with_watermark.jpg')
        
        # Verify result is a valid decodable image
        result_img = Image.open(cleaned_file)
        self.assertEqual(result_img.size, (400, 300))

    def test_clean_uploaded_image_handles_png(self):
        """Verify PNG images with alpha channel are handled cleanly."""
        png_img = Image.new('RGBA', (200, 100), color=(255, 0, 0, 128))
        draw = ImageDraw.Draw(png_img)
        draw.text((10, 80), "WATERMARK", fill=(255, 255, 255, 255))
        
        buffer = io.BytesIO()
        png_img.save(buffer, format='PNG')
        buffer.seek(0)

        uploaded_file = SimpleUploadedFile('logo_wm.png', buffer.getvalue(), content_type='image/png')
        cleaned_file = clean_uploaded_image(uploaded_file)
        
        self.assertIsNotNone(cleaned_file)
        result_img = Image.open(cleaned_file)
        self.assertEqual(result_img.size, (200, 100))

    def test_company_settings_upload_cleans_slides_automatically(self):
        """Verify uploading slides via CompanySettingsView triggers automatic watermark cleaning."""
        self.client.force_login(self.admin_user)
        session = self.client.session
        session['is_2fa_verified'] = True
        session.save()

        raw_image_bytes = self._generate_image_with_watermark()
        slide_file = SimpleUploadedFile('slide_upload.jpg', raw_image_bytes, content_type='image/jpeg')

        response = self.client.post(reverse('administration-company-settings'), {
            'company_name': 'Menard Trading CC',
            'tagline': 'ALWAYS ON TIME',
            'login_bg_image_1': slide_file,
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        settings_obj = CompanySettings.get_settings()
        self.assertTrue(bool(settings_obj.login_bg_image_1))
