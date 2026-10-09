from pathlib import Path
from pypdf import PdfReader
import pypdfium2 as pdfium

pdf = Path(r"C:\Users\ethan\Downloads\CCC Curriculum Prep (1).pdf")
reader = PdfReader(str(pdf))
print(f"PAGES={len(reader.pages)}")
for i, page in enumerate(reader.pages, 1):
    text = page.extract_text() or ""
    print(f"\n===== PAGE {i} =====\n{text}")

out = Path("tmp/pdfs")
out.mkdir(parents=True, exist_ok=True)
doc = pdfium.PdfDocument(str(pdf))
page = doc[5]
bitmap = page.render(scale=2.0)
bitmap.to_pil().save(out / "curriculum-page-6.png")
