"""Source-document ingestion — turn any uploaded file into the page-image
list the rest of the pipeline already understands.

StudyFetch accepts PDF, PowerPoint, Word, YouTube, lecture audio, and
photos of handwritten notes; we need to be at parity (or better) here
because input flexibility is the *first* thing a prospective user tries.
We normalise everything down to a list of page images — once we have
those, the existing scan-to-video pipeline does the rest unchanged.

Supported today:
    - .pdf            via PyMuPDF (fitz). Each page rendered at 150 DPI.
    - .png/.jpg/.jpeg passes through.

Sketched (need extra system deps; documented stubs raise NotImplemented):
    - .pptx           via python-pptx + Pillow page-render
    - .docx           via docx2pdf → pdf_to_images
    - YouTube URL     via yt-dlp + frame extraction
    - .mp3/.wav       via Whisper transcript → synthetic 'page' with the
                      transcript text rendered for the vision call

The Lesson schema doesn't care where the page came from — it just sees
a textbook-style image — so adding a new ingest path is purely about
producing PIL Images, not about touching pedagogy / render / web."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def ingest(source: Path, work_dir: Path | None = None) -> list[Path]:
    """Dispatch on file extension. Returns a list of page image paths
    in reading order."""
    source = source.resolve()
    work_dir = work_dir or Path(tempfile.mkdtemp(prefix="padhai_ingest_"))

    ext = source.suffix.lower()
    if ext == ".pdf":
        return pdf_to_images(source, work_dir)
    if ext in (".png", ".jpg", ".jpeg", ".webp"):
        return [source]
    if ext == ".pptx":
        return _pptx_to_images(source, work_dir)
    if ext == ".docx":
        return _docx_to_images(source, work_dir)
    raise ValueError(
        f"unsupported source type {ext!r}. Supported: .pdf, .png, .jpg, "
        ".jpeg, .webp (.pptx and .docx coming soon)"
    )


def pdf_to_images(pdf_path: Path, work_dir: Path, dpi: int = 150) -> list[Path]:
    """Render each PDF page as a PNG at `dpi`. ~50ms per page on
    a single CPU; for an NCERT chapter of 12 pages that's under a second.

    Uses PyMuPDF (pip install PyMuPDF) which bundles its own renderer so
    we don't need poppler/ghostscript on the host. Optional dep — fails
    fast with an install hint if not present."""
    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        raise RuntimeError(
            "PyMuPDF required for PDF ingest (pip install PyMuPDF)"
        ) from e

    work_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc, start=1):
            pix = page.get_pixmap(dpi=dpi)
            out_path = work_dir / f"{pdf_path.stem}_p{i:03d}.png"
            pix.save(out_path)
            out.append(out_path)
    return out


def _pptx_to_images(pptx_path: Path, work_dir: Path) -> list[Path]:
    """Convert each slide to PNG. Today: shells out to LibreOffice
    (`soffice --headless --convert-to pdf`) then pdf_to_images. Requires
    LibreOffice on the host."""
    if shutil.which("soffice") is None:
        raise RuntimeError(
            "PPTX ingest needs LibreOffice on the host (apt install libreoffice)"
        )
    work_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["soffice", "--headless", "--convert-to", "pdf",
         "--outdir", str(work_dir), str(pptx_path)],
        check=True, capture_output=True,
    )
    pdf_path = work_dir / f"{pptx_path.stem}.pdf"
    return pdf_to_images(pdf_path, work_dir)


def _docx_to_images(docx_path: Path, work_dir: Path) -> list[Path]:
    """Same shape — DOCX → PDF (LibreOffice) → images."""
    if shutil.which("soffice") is None:
        raise RuntimeError(
            "DOCX ingest needs LibreOffice on the host"
        )
    work_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["soffice", "--headless", "--convert-to", "pdf",
         "--outdir", str(work_dir), str(docx_path)],
        check=True, capture_output=True,
    )
    pdf_path = work_dir / f"{docx_path.stem}.pdf"
    return pdf_to_images(pdf_path, work_dir)
