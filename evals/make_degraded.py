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

def scaled(factor):  # keep only 1/factor of the original resolution
    def render(page):
        pix = page.get_pixmap(matrix=pymupdf.Matrix(1 / factor, 1 / factor))
        return pix.tobytes("png"), page.rect.width, page.rect.height
    return render

def jpeg(page):  # heavy JPEG compression
    pix = page.get_pixmap(matrix=pymupdf.Matrix(1, 1))
    return pix.tobytes("jpeg", jpg_quality=8), page.rect.width, page.rect.height

def rotated(page):  # photographed slightly crooked
    m = pymupdf.Matrix(1, 1).prerotate(4)
    pix = page.get_pixmap(matrix=m)
    return pix.tobytes("png"), pix.width, pix.height

VARIANTS = [
    ("lowres", scaled(3)),     # 1/3 resolution: still easy to read
    ("jpeg", jpeg),
    ("rotated", rotated),
    ("tiny", scaled(6)),       # 284 px wide: blurry but readable
    ("tiny12", scaled(12)),    # 142 px wide: mostly illegible
    ("tiny20", scaled(20)),    # 85 px wide: illegible, used to see whether a model admits it
]

if __name__ == "__main__":
    for name, fn in VARIANTS:
        build(name, fn)
