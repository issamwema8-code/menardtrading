import os
import io
import pypdfium2 as pdfium
from xhtml2pdf import pisa
from PIL import Image

def render_svg_to_clean_png(svg_path, png_path):
    if not os.path.exists(svg_path):
        print(f"Skipping {svg_path} (not found)")
        return
    with open(svg_path, 'r', encoding='utf-8') as f:
        svg_code = f.read()
    
    html = f"""<!DOCTYPE html>
<html>
<head>
  <style>
    @page {{ size: 800pt 400pt; margin: 0; }}
    body {{ margin: 0; padding: 0; background: transparent; }}
  </style>
</head>
<body>
  <div>
    {svg_code}
  </div>
</body>
</html>"""
    pdf_io = io.BytesIO()
    pisa.CreatePDF(html, dest=pdf_io)
    pdf_io.seek(0)
    pdf = pdfium.PdfDocument(pdf_io)
    page = pdf[0]
    bitmap = page.render(scale=3)
    img = bitmap.to_pil()
    
    # Transparent conversion for near-white background
    img = img.convert('RGBA')
    datas = img.getdata()
    new_data = []
    for item in datas:
        # Check if pixel is white / background
        if item[0] >= 250 and item[1] >= 250 and item[2] >= 250:
            new_data.append((255, 255, 255, 0))
        else:
            new_data.append(item)
    img.putdata(new_data)
    
    bbox = img.getbbox()
    if bbox:
        w, h = img.size
        bbox = (max(0, bbox[0]-4), max(0, bbox[1]-4), min(w, bbox[2]+4), min(h, bbox[3]+4))
        img = img.crop(bbox)
        
    img.save(png_path, 'PNG')
    print(f"Generated {png_path} - Size: {img.size}")

if __name__ == '__main__':
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    images_dir = os.path.join(base_dir, 'static', 'images')
    
    render_svg_to_clean_png(
        os.path.join(images_dir, 'menard_emblem.svg'),
        os.path.join(images_dir, 'menard_emblem.png')
    )
    render_svg_to_clean_png(
        os.path.join(images_dir, 'menard_logo.svg'),
        os.path.join(images_dir, 'menard_logo.png')
    )
    render_svg_to_clean_png(
        os.path.join(images_dir, 'menard_icon.svg'),
        os.path.join(images_dir, 'menard_icon.png')
    )
