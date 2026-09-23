"""PowerPoint exporter for creating slide images/videos with animations.

Supports two export backends:
  1. PowerPoint COM (Windows only) — full animation support, single-threaded
  2. LibreOffice headless (cross-platform) — static slides only, multithreaded

The exporter auto-selects the best available backend. PowerPoint COM is
preferred when animations are detected; LibreOffice is used as a fallback
for static slides or when PowerPoint is not installed.
"""

import os
import subprocess
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional, Callable
from dataclasses import dataclass

from utils.logger import get_logger
from utils.helpers import retry, get_libreoffice_path

logger = get_logger("EXPORT")


@dataclass
class ExportedSlide:
    """Information about an exported slide."""
    index: int
    image_path: Optional[Path] = None
    video_path: Optional[Path] = None
    duration: float = 5.0  # Default duration in seconds
    has_animation: bool = False


class PPTXExporter:
    """Exports PowerPoint slides as images or videos with animations.

    Static slides are exported as PNG images (fast).
    Animated slides are exported as individual MP4 video clips via PowerPoint
    COM automation so entrance/exit animations, motion paths, and transitions
    are preserved in the final video.

    When PowerPoint is unavailable, falls back to LibreOffice headless for
    static PNG export (no animation support, but cross-platform and multithreaded).
    """

    def __init__(self, pptx_path: Path, output_dir: Path):
        self.pptx_path = Path(pptx_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._powerpoint = None
        self._presentation = None
        self._we_started_powerpoint = False  # track if we launched PowerPoint
        # Which backend export_slides_as_images ran: "powerpoint" (real renders,
        # animations detected) or "pillow" (the title-only fallback), None
        # before an export. Callers record it so a fallback render is never
        # mistaken for the real slide (vision features must skip it).
        self.backend: Optional[str] = None

    # ------------------------------------------------------------------
    # PowerPoint COM backend
    # ------------------------------------------------------------------

    @retry(max_attempts=3, base_delay=2.0, exceptions=(RuntimeError, OSError))
    def _init_powerpoint(self):
        """Initialize PowerPoint COM object for slide export.

        Uses win32com to automate PowerPoint via COM. CoInitialize is required
        because this may run in a background thread.
        If PowerPoint is already running, attach to it without quitting it later.
        Retries automatically on COM failures (flaky on some Windows machines).
        """
        try:
            import win32com.client
            import pythoncom
            pythoncom.CoInitialize()
            # Try to attach to an already-running PowerPoint instance first
            try:
                self._powerpoint = win32com.client.GetActiveObject("PowerPoint.Application")
                self._we_started_powerpoint = False  # don't quit on cleanup
            except Exception:
                self._powerpoint = win32com.client.Dispatch("PowerPoint.Application")
                # Suppress the PowerPoint window completely
                try:
                    import ctypes
                    user32 = ctypes.windll.user32
                    # Hide immediately before it renders
                    hwnd = self._powerpoint.HWND
                    if hwnd:
                        user32.ShowWindow(hwnd, 0)  # SW_HIDE
                        user32.MoveWindow(hwnd, -32000, -32000, 1, 1, False)
                except Exception:
                    pass
                # COM requires Visible=True but we've hidden the window
                self._powerpoint.Visible = True
                try:
                    self._powerpoint.WindowState = 2  # ppWindowMinimized
                except Exception:
                    pass
                self._we_started_powerpoint = True  # we started it, we'll quit it
        except Exception as e:
            raise RuntimeError(f"Failed to initialize PowerPoint: {e}")

    @retry(max_attempts=3, base_delay=2.0, exceptions=(RuntimeError, OSError))
    def _open_presentation(self):
        """Open the presentation in PowerPoint."""
        if self._powerpoint is None:
            self._init_powerpoint()

        try:
            self._presentation = self._powerpoint.Presentations.Open(
                str(self.pptx_path.absolute()),
                ReadOnly=True,
                Untitled=False,
                WithWindow=False
            )
        except Exception as e:
            raise RuntimeError(f"Failed to open presentation: {e}")

    def _slide_has_animation(self, slide) -> bool:
        """Check if a slide has shape-level animations.

        Returns True only if the slide has actual shape animations
        (entrance, exit, emphasis, motion path) in the MainSequence.

        Slide transitions (fade, dissolve, wipe, etc.) are NOT counted
        as animations since they don't affect slide content and our
        video creator handles transitions separately.
        """
        try:
            # Check shape animations via the TimeLine
            timeline = slide.TimeLine
            if timeline and timeline.MainSequence and timeline.MainSequence.Count > 0:
                return True
        except Exception:
            pass

        return False

    def _export_slide_as_video(self, slide_index: int) -> Optional[Path]:
        """Export a single animated slide as an MP4 video clip.

        Uses PowerPoint's SaveCopyAs to export just the target slide:
        1. Creates a temp copy of the presentation with only the target slide
        2. Exports that single-slide presentation as MP4
        3. Returns the path to the exported video
        """
        import time

        try:
            video_dir = self.output_dir / "animations"
            video_dir.mkdir(exist_ok=True)
            video_path = video_dir / f"slide_{slide_index + 1:03d}_anim.mp4"

            # Create a temporary copy with just this slide
            temp_dir = Path(tempfile.mkdtemp(prefix="pptx_anim_"))
            temp_pptx = temp_dir / "single_slide.pptx"

            try:
                # Save a copy of the full presentation
                self._presentation.SaveCopyAs(str(temp_pptx.absolute()))

                # Open the copy and delete all slides except the target
                temp_pres = self._powerpoint.Presentations.Open(
                    str(temp_pptx.absolute()),
                    ReadOnly=False,
                    Untitled=False,
                    WithWindow=False,
                )

                # Delete slides in reverse order (1-indexed) to keep target
                target_1based = slide_index + 1
                for i in range(temp_pres.Slides.Count, 0, -1):
                    if i != target_1based:
                        temp_pres.Slides(i).Delete()

                # Export as MP4 (ppSaveAsMP4 = 39)
                temp_mp4 = temp_dir / "output.mp4"
                temp_pres.SaveAs(str(temp_mp4.absolute()), 39)

                # Wait for PowerPoint to finish rendering
                timeout = 120
                start = time.time()
                while not temp_mp4.exists() or temp_mp4.stat().st_size < 1000:
                    if time.time() - start > timeout:
                        logger.warning("Timeout waiting for slide %d video export", slide_index + 1)
                        break
                    time.sleep(0.5)
                    # Check if PowerPoint is still exporting
                    try:
                        if not self._powerpoint.Presentations.Count:
                            break
                    except Exception:
                        break

                temp_pres.Close()

                if temp_mp4.exists() and temp_mp4.stat().st_size > 1000:
                    shutil.copy2(temp_mp4, video_path)
                    logger.info("Exported slide %d animation -> %s", slide_index + 1, video_path.name)
                    return video_path
                else:
                    logger.error("Failed to export slide %d as video", slide_index + 1)
                    return None

            finally:
                # Clean up temp files
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception:
                    pass

        except Exception as e:
            logger.error("Error exporting slide %d as video: %s", slide_index + 1, e)
            return None

    def _export_via_powerpoint(
        self, progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[ExportedSlide]:
        """Export slides using PowerPoint COM automation."""
        try:
            self._open_presentation()
            exported = []
            slide_count = self._presentation.Slides.Count

            # First pass: detect which slides have animations
            animated_indices = set()
            for i in range(1, slide_count + 1):
                slide = self._presentation.Slides(i)
                if self._slide_has_animation(slide):
                    animated_indices.add(i - 1)  # 0-indexed

            if animated_indices:
                logger.info("Detected animations on slides: %s", sorted(i + 1 for i in animated_indices))

            for i in range(1, slide_count + 1):
                slide = self._presentation.Slides(i)
                image_path = self.output_dir / f"slide_{i:03d}.png"
                slide_idx = i - 1

                # Always export PNG (used for preview/thumbnail)
                slide.Export(str(image_path.absolute()), "PNG")

                video_path = None
                has_anim = slide_idx in animated_indices
                if has_anim:
                    video_path = self._export_slide_as_video(slide_idx)

                exported.append(ExportedSlide(
                    index=slide_idx,
                    image_path=image_path,
                    video_path=video_path,
                    has_animation=has_anim,
                ))

                if progress_callback:
                    progress_callback(i, slide_count)

            return exported

        finally:
            self._cleanup()

    # ------------------------------------------------------------------
    # LibreOffice headless backend (cross-platform, multithreaded)
    # ------------------------------------------------------------------

    def _export_via_libreoffice(
        self, progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[ExportedSlide]:
        """Export slides as PNG images using LibreOffice headless mode.

        LibreOffice is multithreaded and does not require COM serialization,
        so multiple exports can run in parallel without blocking.
        No animation support — all slides are exported as static images.
        """
        soffice = get_libreoffice_path()
        if not soffice:
            raise RuntimeError("LibreOffice not found")

        # Use a unique temp profile to allow parallel LibreOffice instances
        temp_profile = Path(tempfile.mkdtemp(prefix="lo_profile_"))

        try:
            temp_out = Path(tempfile.mkdtemp(prefix="lo_export_"))

            # Step 1: Convert PPTX to PDF (preserves all slides)
            cmd = [
                soffice,
                "--headless",
                "--norestore",
                f"-env:UserInstallation=file:///{temp_profile.as_posix()}",
                "--convert-to", "pdf",
                "--outdir", str(temp_out),
                str(self.pptx_path.absolute()),
            ]

            logger.info("Exporting slides via LibreOffice: %s", self.pptx_path.name)
            creation_flags = 0x08000000 if os.name == "nt" else 0
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300,
                creationflags=creation_flags,
            )

            if result.returncode != 0:
                logger.error("LibreOffice export failed: %s", result.stderr[:500])
                raise RuntimeError(f"LibreOffice export failed: {result.stderr[:200]}")

            # Find the PDF
            pdf_files = list(temp_out.glob("*.pdf"))
            if not pdf_files:
                raise RuntimeError("LibreOffice did not produce PDF output")

            pdf_path = pdf_files[0]

            # Step 2: Convert PDF pages to PNGs
            # Try PyMuPDF (fitz) first, fall back to Pillow+pdf2image
            from core.pptx_reader import PPTXReader
            reader = PPTXReader(self.pptx_path)
            reader.load()
            slide_count = reader.slide_count

            png_paths = self._pdf_to_pngs(pdf_path, temp_out, slide_count)

            exported = []
            for idx in range(slide_count):
                image_path = self.output_dir / f"slide_{idx + 1:03d}.png"

                if idx < len(png_paths) and png_paths[idx].exists():
                    shutil.copy2(png_paths[idx], image_path)
                else:
                    from PIL import Image
                    img = Image.new("RGB", (1920, 1080), (0, 0, 0))
                    img.save(str(image_path))
                    logger.warning("No PNG for slide %d, using placeholder", idx + 1)

                exported.append(ExportedSlide(
                    index=idx,
                    image_path=image_path,
                    video_path=None,
                    has_animation=False,
                ))

                if progress_callback:
                    progress_callback(idx + 1, slide_count)

            logger.info("LibreOffice exported %d slides as static PNGs", len(exported))
            return exported

        finally:
            # Clean up temp directories
            for d in [temp_profile]:
                try:
                    shutil.rmtree(d, ignore_errors=True)
                except Exception:
                    pass
            # Keep temp_out cleanup separate in case it fails
            try:
                if 'temp_out' in locals():
                    shutil.rmtree(temp_out, ignore_errors=True)
            except Exception:
                pass

    def _pdf_to_pngs(self, pdf_path: Path, output_dir: Path,
                      expected_count: int) -> List[Path]:
        """Convert a multi-page PDF to individual PNG files.

        Tries PyMuPDF (fitz) first for best quality, falls back to
        Pillow or ffmpeg if unavailable.
        """
        png_paths = []

        # Try PyMuPDF (best quality, fastest)
        try:
            import fitz  # PyMuPDF
            # Suppress noisy MuPDF structure tree warnings
            fitz.TOOLS.mupdf_warnings(False)
            doc = fitz.open(str(pdf_path))
            for page_num in range(len(doc)):
                page = doc[page_num]
                # Render at 2x for 1920px width
                zoom = 1920 / (page.rect.width or 720)
                mat = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat)
                out_path = output_dir / f"page_{page_num + 1:03d}.png"
                pix.save(str(out_path))
                png_paths.append(out_path)
            doc.close()
            logger.info("PDF split via PyMuPDF: %d pages", len(png_paths))
            return png_paths
        except ImportError:
            pass
        except Exception as e:
            logger.warning("PyMuPDF failed: %s, trying Pillow", e)

        # Try Pillow (needs pdf2image or poppler)
        try:
            # Pillow can open PDF but only first page without poppler
            # Use subprocess with ffmpeg as fallback
            pass
        except Exception:
            pass

        # Try ffmpeg — the RESOLVED path, never the bare name (trap 3): a host
        # that relies on the bundled binary has no "ffmpeg" on PATH.
        from utils.config import FFMPEG_PATH
        if not FFMPEG_PATH:
            return png_paths
        try:
            for page_num in range(expected_count):
                out_path = output_dir / f"page_{page_num + 1:03d}.png"
                cmd = [
                    FFMPEG_PATH, "-y",
                    "-i", str(pdf_path),
                    "-vf", f"select=eq(n\\,{page_num})",
                    "-vframes", "1",
                    "-s", "1920x1080",
                    str(out_path),
                ]
                creation_flags = 0x08000000 if os.name == "nt" else 0
                subprocess.run(cmd, capture_output=True, timeout=30,
                               creationflags=creation_flags)
                if out_path.exists():
                    png_paths.append(out_path)
            if png_paths:
                logger.info("PDF split via ffmpeg: %d pages", len(png_paths))
                return png_paths
        except Exception as e:
            logger.warning("ffmpeg PDF split failed: %s", e)

        return png_paths

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def export_slides_as_images(
        self,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> List[ExportedSlide]:
        """Export all slides — static slides as PNG, animated slides as MP4 + PNG.

        Uses PowerPoint COM on Windows (best quality, supports animations).
        Falls back to Pillow if PowerPoint is not available.
        """
        self.backend = None
        if os.name == "nt":
            try:
                exported = self._export_via_powerpoint(progress_callback)
                self.backend = "powerpoint"
                return exported
            except RuntimeError as e:
                logger.warning("PowerPoint COM failed: %s — using Pillow fallback", e)

        self.backend = "pillow"
        return self._export_via_pillow(progress_callback)

    def _export_via_pillow(
        self, progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[ExportedSlide]:
        """Minimal fallback: create basic slide images using python-pptx shapes + Pillow.

        This produces simple rendered slides without full PowerPoint fidelity,
        but works on any platform with no external dependencies.
        """
        from PIL import Image, ImageDraw, ImageFont
        from core.pptx_reader import PPTXReader

        logger.info("Using Pillow fallback for slide export (no PowerPoint or LibreOffice)")
        reader = PPTXReader(self.pptx_path)
        reader.load()

        exported = []
        for idx in range(reader.slide_count):
            image_path = self.output_dir / f"slide_{idx + 1:03d}.png"
            slide = reader.get_slide(idx)

            # Create a basic slide image with title text
            img = Image.new("RGB", (1920, 1080), (255, 255, 255))
            draw = ImageDraw.Draw(img)

            # Try to render the title
            title = slide.title if slide and slide.title else f"Slide {idx + 1}"
            try:
                font = ImageFont.truetype("arial.ttf", 48)
            except (IOError, OSError):
                font = ImageFont.load_default()

            # Center the title text
            bbox = draw.textbbox((0, 0), title, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            x = (1920 - tw) // 2
            y = (1080 - th) // 2
            draw.text((x, y), title, fill=(0, 0, 0), font=font)

            img.save(str(image_path))

            exported.append(ExportedSlide(
                index=idx,
                image_path=image_path,
                video_path=None,
                has_animation=False,
            ))

            if progress_callback:
                progress_callback(idx + 1, reader.slide_count)

        logger.info("Pillow fallback exported %d slides", len(exported))
        return exported

    def _cleanup(self):
        """Clean up PowerPoint COM resources.

        Only closes our presentation — never quits PowerPoint if it was already
        running (that would close any other open presentations the user has).
        Each step is wrapped in a bare except because cleanup must not raise.
        """
        try:
            if self._presentation:
                self._presentation.Close()
                self._presentation = None
        except Exception:
            pass

        try:
            # Only quit PowerPoint if we were the ones who launched it
            if self._powerpoint and self._we_started_powerpoint:
                self._powerpoint.Quit()
            self._powerpoint = None
        except Exception:
            pass

        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:
            pass
