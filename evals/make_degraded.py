"""Build image-only PDFs with degraded copies of Recipe.pdf's pages (kept under data/, gitignored)."""
import pymupdf
from pathlib import Path

src = pymupdf.open("Recipe.pdf")
out = Path("data/experiments/degraded"); out.mkdir(parents=True, exist_ok=True)

def build(name, render):
    doc = pymupdf.open()
    for page in src:
        data, w, h = render(page)
        new = doc.new_page(width=w, height=h)
        new.insert_image(new.rect, stream=data, keep_proportion=False)
    doc.save(out / f"Recipe_{name}.pdf")
    print(name, "->", out / f"Recipe_{name}.pdf", [round(p.rect.width) for p in doc])

def lowres(page):  # one third of the original resolution
    pix = page.get_pixmap(matrix=pymupdf.Matrix(1/3, 1/3))
    return pix.tobytes("png"), page.rect.width, page.rect.height

def jpeg(page):  # heavy JPEG compression
    pix = page.get_pixmap(matrix=pymupdf.Matrix(1, 1))
    return pix.tobytes("jpeg", jpg_quality=8), page.rect.width, page.rect.height

def rotated(page):  # photographed slightly crooked
    m = pymupdf.Matrix(1, 1).prerotate(4)
    pix = page.get_pixmap(matrix=m)
    return pix.tobytes("png"), pix.width, pix.height

for name, fn in [("lowres", lowres), ("jpeg", jpeg), ("rotated", rotated)]:
    build(name, fn)
