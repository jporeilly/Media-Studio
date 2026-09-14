"""FileItem — represents a loaded PowerPoint file in the application."""

from pathlib import Path
from typing import Optional

from core.pptx_reader import PPTXReader
from core.project_manager import ProjectManager, ProjectStateError, get_project_dir
from utils.config import CONFIG_DIR


# File suffixes that denote an imported (re-voice) video project rather than a
# deck. Single source of truth for the sidebar, session restore and processing.
VIDEO_SUFFIXES = (".mp4", ".avi", ".mkv", ".mov", ".webm")


class FileItem:
    """Represents a loaded PowerPoint file with its reader and project state."""

    def __init__(self, path: Path, projects_base: Path = None):
        self.path = path
        self._projects_base = projects_base or (CONFIG_DIR / "projects")
        self.reader: Optional[PPTXReader] = None
        self.slide_count = 0
        self.enabled = True
        self.project_manager: Optional[ProjectManager] = None
        self.has_project = False

    def load(self) -> bool:
        """Load the presentation and check for an existing project."""
        self.reader = PPTXReader(self.path)
        if self.reader.load():
            self.slide_count = self.reader.slide_count
            project_dir = get_project_dir(self.path, self._projects_base)
            if (project_dir / "project.json").exists():
                self.project_manager = ProjectManager(project_dir)
                self.project_manager.load()
                self.has_project = True
            return True
        return False

    def load_pdf(self) -> bool:
        """Load a PDF file, converting pages to slide images.

        An existing project (``project.json``) is kept - its notes, overrides
        and undo history are what the slide editor writes, and generate calls
        this on every run - and only the page images are refreshed. The
        project is created fresh only when there is none, or when its slide
        count no longer matches the PDF's pages; one that exists but cannot
        be read raises ``ProjectStateError`` rather than being overwritten.
        """
        try:
            from core.pdf_reader import PDFReader
            pdf_reader = PDFReader(self.path)
            project_dir = get_project_dir(self.path, self._projects_base)
            images_dir = project_dir / "images"
            if pdf_reader.load(output_dir=images_dir):
                self.slide_count = pdf_reader.slide_count
                self.project_manager = ProjectManager(project_dir)
                if self.project_manager.project_file.exists():
                    self.project_manager.load()
                state = self.project_manager.state
                if state is None or len(state.slides) != self.slide_count:
                    slide_notes = [""] * self.slide_count
                    self.project_manager.create_project(
                        pptx_path=self.path,
                        slide_notes=slide_notes,
                        voice_id="",
                    )
                for slide_info in pdf_reader.slides:
                    if slide_info.image_path:
                        self.project_manager.update_slide_image(slide_info.index, slide_info.image_path)
                self.has_project = True
                self.reader = pdf_reader
                return True
        except ProjectStateError:
            raise
        except Exception as e:
            print(f"Error loading PDF: {e}")
        return False

    def load_by_suffix(self) -> bool:
        """Load this item by file type: deck, PDF, or imported video project.

        Single dispatch point for every path that re-opens a known file
        (session restore, the Recent list) so a new file type is handled in
        one place instead of per caller.
        """
        suffix = self.path.suffix.lower()
        if suffix in VIDEO_SUFFIXES:
            return self.load_existing_video_project()
        if suffix == ".pdf":
            return self.load_pdf()
        return self.load()

    def load_existing_video_project(self) -> bool:
        """Re-open a previously imported video project from its project.json.

        Video projects have no PPTX reader; their slides are keyframe images
        stored by ``load_video_project``. Resolves the project directory under
        this item's own projects base (per-user in enterprise), never the
        global one.

        Returns:
            True if a project.json was found and loaded.
        """
        project_dir = get_project_dir(self.path, self._projects_base)
        if not (project_dir / "project.json").exists():
            return False
        self.project_manager = ProjectManager(project_dir)
        self.project_manager.load()
        if not self.project_manager.state:
            return False
        self.slide_count = len(self.project_manager.state.slides)
        self.has_project = True
        return True

    def load_video_project(
        self,
        slide_notes: list[str],
        keyframe_paths: list,
        voice_id: str = "",
        section_times: list = None,
        section_segments: list = None,
    ) -> bool:
        """Create a project from an imported video with transcribed text and keyframes.

        Args:
            slide_notes: Transcribed text per section (becomes editable speaker notes).
            keyframe_paths: Extracted keyframe images (becomes slide images).
            voice_id: Initial voice ID for TTS.
            section_times: List of (start_time, end_time) tuples for each section.

        Returns:
            True if project was created successfully.
        """
        try:
            from pathlib import Path as _P
            n = min(len(slide_notes), len(keyframe_paths))
            if n == 0:
                return False

            self.slide_count = n
            project_dir = get_project_dir(self.path, self._projects_base)
            images_dir = project_dir / "images"
            images_dir.mkdir(parents=True, exist_ok=True)

            import shutil
            copied_images = []
            for i, kf in enumerate(keyframe_paths[:n]):
                dest = images_dir / f"slide_{i + 1:03d}.png"
                shutil.copy2(_P(kf), dest)
                copied_images.append(dest)

            self.project_manager = ProjectManager(project_dir)
            self.project_manager.create_project(
                pptx_path=self.path,
                slide_notes=slide_notes[:n],
                voice_id=voice_id,
            )

            # Store source video path for re-voicing
            self.project_manager.state.source_video_path = str(self.path)

            for i, img in enumerate(copied_images):
                self.project_manager.update_slide_image(i, img)
                if section_times and i < len(section_times):
                    self.project_manager.state.slides[i].original_start_time = section_times[i][0]
                    self.project_manager.state.slides[i].original_end_time = section_times[i][1]
                if section_segments and i < len(section_segments):
                    self.project_manager.state.slides[i].original_segments = section_segments[i]

            self.project_manager.save()
            self.has_project = True
            print(f"Video project created: {n} slides from {self.path.name}")
            return True
        except Exception as e:
            print(f"Error creating video project: {e}")
            return False

