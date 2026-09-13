"""PDF reader for importing presentations as slide images.

Converts PDF pages to images and creates a PPTXReader-compatible interface
so PDFs can be used in the same pipeline as PowerPoint files.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from utils.logger import get_logger

logger = get_logger("PDF")


@dataclass
class PDFSlideInfo:
    """Information about a single PDF page treated as a slide."""
    index: int
    speaker_notes: str = ""
    title: Optional[str] = None
    body_text: Optional[str] = None
    image_path: Optional[Path] = None


class PDFReader:
    """Reads PDF files and extracts pages as slide images.

    Requires either pdf2image (poppler) or PyMuPDF (fitz) to be installed.
    Falls back gracefully if neither is available.
    """

    def __init__(self, pdf_path: Path):
        self.pdf_path = Path(pdf_path)
        self._slides_info: List[PDFSlideInfo] = []

    def load(self, output_dir: Optional[Path] = None) -> bool:
        """Load the PDF and convert pages to images."""
        if output_dir is None:
            output_dir = self.pdf_path.parent / f"{self.pdf_path.stem}_pages"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Try PyMuPDF first (faster, no external deps)
        try:
            return self._load_with_fitz(output_dir)
        except ImportError:
            pass

        # Try pdf2image (requires poppler)
        try:
            return self._load_with_pdf2image(output_dir)
        except ImportError:
            pass

        logger.error("Neither PyMuPDF (fitz) nor pdf2image is installed")
        return False

    def _load_with_fitz(self, output_dir: Path) -> bool:
        """Load PDF using PyMuPDF."""
        import fitz  # PyMuPDF

        doc = fitz.open(str(self.pdf_path))
        self._slides_info = []

        for i, page in enumerate(doc):
            # Render page as image (300 DPI for good quality)
            mat = fitz.Matrix(2, 2)  # 2x zoom ≈ 144 DPI
            pix = page.get_pixmap(matrix=mat)
            img_path = output_dir / f"page_{i + 1:03d}.png"
            pix.save(str(img_path))

            # Extract text from page
            text = page.get_text().strip()
            lines = text.split("\n") if text else []
            title = lines[0] if lines else None
            body = "\n".join(lines[1:]).strip() if len(lines) > 1 else None

            self._slides_info.append(PDFSlideInfo(
                index=i,
                title=title,
                body_text=body,
                image_path=img_path,
            ))

        doc.close()
        return len(self._slides_info) > 0

    def _load_with_pdf2image(self, output_dir: Path) -> bool:
        """Load PDF using pdf2image (requires poppler)."""
        from pdf2image import convert_from_path

        images = convert_from_path(str(self.pdf_path), dpi=150)
        self._slides_info = []

        for i, img in enumerate(images):
            img_path = output_dir / f"page_{i + 1:03d}.png"
            img.save(str(img_path), "PNG")
            self._slides_info.append(PDFSlideInfo(
                index=i,
                image_path=img_path,
            ))

        return len(self._slides_info) > 0

    @property
    def slide_count(self) -> int:
        return len(self._slides_info)

    @property
    def slides(self) -> List[PDFSlideInfo]:
        return self._slides_info

    def get_slide(self, index: int) -> Optional[PDFSlideInfo]:
        if 0 <= index < len(self._slides_info):
            return self._slides_info[index]
        return None

    def get_speaker_notes(self, index: int) -> str:
        """PDFs don't have speaker notes — always returns empty string."""
        slide = self.get_slide(index)
        return slide.speaker_notes if slide else ""

    def has_speaker_notes(self) -> bool:
        return any(s.speaker_notes for s in self._slides_info)
