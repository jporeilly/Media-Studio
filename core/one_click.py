"""One-Click Video — automated end-to-end video creation pipeline.

Chains: Generate Notes → Enhance → QA Fix → Generate Audio → Generate Video → Subtitles → Metadata
User uploads PPTX, clicks one button, gets a complete video with subtitles and YouTube metadata.
"""

from pathlib import Path
from typing import Optional, Callable
import logging

logger = logging.getLogger("pptx2video.ONECLICK")


def auto_detect_settings(pptx_path: Path, ollama_url: str = "",
                          ollama_model: str = "") -> dict:
    """Let the LLM analyze the presentation and recommend optimal settings.

    Returns dict with recommended: tone, voice, speed, animation_style, music_mood.
    """
    defaults = {
        'tone': 'Formal',
        'voice': 'en-US-EmmaNeural',
        'speed': 1.0,
        'animation_style': 'ken_burns_zoom_in',
        'music_mood': 'calm professional corporate background music',
        'reasoning': 'Using defaults (no AI available)',
    }

    if not ollama_url or not ollama_model:
        return defaults

    try:
        from core.pptx_reader import PPTXReader
        import requests
        import json

        reader = PPTXReader(pptx_path)
        reader.load()

        content_preview = []
        for i in range(min(5, reader.slide_count)):
            notes = reader.get_slide_notes(i) or ""
            slide = reader.get_slide(i)
            text = slide.title if slide and slide.title else ""
            content_preview.append(f"Slide {i+1}: {text} | {notes[:100]}")

        prompt = (
            "Analyze this presentation and recommend settings. Respond in JSON only.\n\n"
            "Slides:\n" + "\n".join(content_preview) + "\n\n"
            "Respond with JSON:\n"
            '{"tone": "Technical|Executive|Student|Sales|Casual|Formal",\n'
            ' "voice_gender": "female|male",\n'
            ' "speed": 0.9-1.1,\n'
            ' "music_mood": "short description of background music style",\n'
            ' "reasoning": "one sentence why"}'
        )

        resp = requests.post(
            f"{ollama_url.rstrip('/')}/api/generate",
            json={"model": ollama_model, "prompt": prompt, "stream": False},
            timeout=15,
        )

        if resp.status_code == 200:
            text = resp.json().get("response", "")
            start = text.find('{')
            end = text.rfind('}') + 1
            if start >= 0 and end > start:
                parsed = json.loads(text[start:end])
                gender = parsed.get('voice_gender', 'female')
                voice = 'en-US-GuyNeural' if gender == 'male' else 'en-US-EmmaNeural'
                return {
                    'tone': parsed.get('tone', 'Formal'),
                    'voice': voice,
                    'speed': float(parsed.get('speed', 1.0)),
                    'animation_style': defaults['animation_style'],
                    'music_mood': parsed.get('music_mood', defaults['music_mood']),
                    'reasoning': parsed.get('reasoning', ''),
                }
    except Exception as e:
        logger.warning("Auto-detect failed: %s", e)

    return defaults


class OneClickPipeline:
    """Orchestrates the full video creation pipeline."""

    def __init__(self, ollama_url: str = "", ollama_model: str = "",
                 voice_id: str = "en-US-EmmaNeural", speed: float = 1.0,
                 projects_base: Path = None, file_item=None):
        self.ollama_url = ollama_url
        self.ollama_model = ollama_model
        self.voice_id = voice_id
        self.speed = speed
        self.projects_base = projects_base
        self.file_item = file_item  # Reuse existing FileItem with session paths
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self, pptx_path: Path, output_dir: Path,
            on_progress: Optional[Callable] = None,
            skip_enhance: bool = False,
            skip_qa: bool = False,
            skip_subtitles: bool = False,
            skip_metadata: bool = False,
            generate_notes: bool = True) -> dict:
        """Run the full pipeline.

        Args:
            pptx_path: Input PPTX file
            output_dir: Where to save outputs
            on_progress: callback(fraction, stage, message)
            skip_enhance: Skip AI enhancement step
            skip_qa: Skip QA review step
            skip_subtitles: Skip subtitle generation
            skip_metadata: Skip YouTube metadata generation
            generate_notes: Auto-generate notes if slides have none

        Returns dict with:
            'video': Path to output video
            'subtitles_srt': Path to SRT file (if generated)
            'subtitles_vtt': Path to VTT file (if generated)
            'metadata': Path to YouTube metadata file (if generated)
            'stages_completed': list of stage names
            'errors': list of error messages
        """
        result = {
            'video': None,
            'subtitles_srt': None,
            'subtitles_vtt': None,
            'metadata': None,
            'stages_completed': [],
            'errors': [],
        }

        stages = self._build_stage_list(generate_notes, skip_enhance, skip_qa,
                                         skip_subtitles, skip_metadata)
        total = len(stages)

        def _progress(stage_idx, frac_within, msg):
            if on_progress:
                overall = (stage_idx + frac_within) / total
                on_progress(overall, stages[stage_idx], msg)

        for i, stage in enumerate(stages):
            if self._cancelled:
                result['errors'].append("Cancelled by user")
                break

            try:
                _progress(i, 0.0, f"Starting: {stage}...")

                if stage == "Read PPTX":
                    result['_notes'] = self._read_pptx(pptx_path)

                elif stage == "Generate Notes":
                    result['_notes'] = self._generate_notes(
                        pptx_path, result.get('_notes', []),
                        lambda f, m: _progress(i, f, m))

                elif stage == "Enhance Notes":
                    result['_notes'] = self._enhance_notes(
                        result.get('_notes', []),
                        lambda f, m: _progress(i, f, m))

                elif stage == "QA Review & Fix":
                    result['_notes'] = self._qa_fix(
                        result.get('_notes', []),
                        lambda f, m: _progress(i, f, m))

                elif stage == "Generate Audio":
                    result['_audio_paths'] = self._generate_audio(
                        result.get('_notes', []), output_dir,
                        lambda f, m: _progress(i, f, m))

                elif stage == "Create Video":
                    result['video'] = self._create_video(
                        pptx_path, result.get('_notes', []),
                        result.get('_audio_paths', []), output_dir,
                        lambda f, m: _progress(i, f, m))

                elif stage == "Generate Subtitles":
                    video = result.get('video')
                    if video and Path(video).exists():
                        subs = self._generate_subtitles(
                            video, output_dir,
                            lambda f, m: _progress(i, f, m))
                        result['subtitles_srt'] = subs.get('srt')
                        result['subtitles_vtt'] = subs.get('vtt')
                    else:
                        logger.info("Skipping subtitles — no video file found")

                elif stage == "Export Metadata":
                    if result.get('_notes'):
                        result['metadata'] = self._export_metadata(
                            result['_notes'], pptx_path.stem, output_dir)

                result['stages_completed'].append(stage)
                _progress(i, 1.0, f"Done: {stage}")

            except Exception as e:
                logger.error("Stage '%s' failed: %s", stage, e)
                result['errors'].append(f"{stage}: {e}")
                # Continue to next stage unless it's a critical one
                if stage in ("Read PPTX", "Generate Audio", "Create Video"):
                    break

        return result

    def _build_stage_list(self, gen_notes, skip_enhance, skip_qa,
                           skip_subs, skip_meta):
        stages = ["Read PPTX"]
        if gen_notes:
            stages.append("Generate Notes")
        if not skip_enhance:
            stages.append("Enhance Notes")
        if not skip_qa:
            stages.append("QA Review & Fix")
        stages.append("Generate Audio")
        stages.append("Create Video")
        if not skip_subs:
            stages.append("Generate Subtitles")
        if not skip_meta:
            stages.append("Export Metadata")
        return stages

    def _read_pptx(self, pptx_path):
        from core.pptx_reader import PPTXReader
        reader = PPTXReader(pptx_path)
        return [reader.get_slide_notes(i) or "" for i in range(reader.slide_count)]

    def _generate_notes(self, pptx_path, existing_notes, on_progress):
        """Generate notes for slides that don't have any."""
        if not self.ollama_url or not self.ollama_model:
            return existing_notes

        from core.ollama_client import generate
        result = list(existing_notes)
        empty_count = sum(1 for n in result if not n.strip())

        if empty_count == 0:
            return result

        done = 0
        for i, note in enumerate(result):
            if not note.strip():
                try:
                    prompt = (
                        f"Generate clear, engaging speaker notes for Slide {i+1}. "
                        "Write as if speaking to an audience. Keep it concise (2-4 sentences)."
                    )
                    generated = generate(
                        prompt=prompt,
                        model=self.ollama_model,
                        base_url=self.ollama_url,
                    )
                    if generated:
                        result[i] = generated
                except Exception:
                    pass
                done += 1
                on_progress(done / empty_count, f"Generated notes for {done}/{empty_count} empty slides")

        return result

    def _enhance_notes(self, notes, on_progress):
        if not self.ollama_url or not self.ollama_model:
            return notes

        import requests
        result = list(notes)
        for i, note in enumerate(result):
            if not note.strip():
                continue
            on_progress(i / len(result), f"Enhancing slide {i+1}/{len(result)}")
            try:
                prompt = (
                    "Rewrite this speaker note for natural spoken narration. "
                    "Keep the same content and length, but make it flow naturally when read aloud. "
                    "Do not add greetings or sign-offs. Return only the rewritten text.\n\n"
                    f"{note}"
                )
                resp = requests.post(
                    f"{self.ollama_url.rstrip('/')}/api/generate",
                    json={"model": self.ollama_model, "prompt": prompt, "stream": False},
                    timeout=30,
                )
                if resp.status_code == 200:
                    enhanced = resp.json().get("response", "").strip()
                    if enhanced and len(enhanced) > 20:
                        result[i] = enhanced
            except Exception:
                pass
        return result

    def _qa_fix(self, notes, on_progress):
        """Run QA review and auto-fix issues using Ollama."""
        if not self.ollama_url or not self.ollama_model:
            # Fallback: basic cleanup only
            result = list(notes)
            for i, note in enumerate(result):
                on_progress(i / len(result), f"QA check slide {i+1}/{len(result)}")
                if note.strip() and len(note.strip()) < 10:
                    result[i] = ""
            return result

        from core.ollama_client import generate

        result = list(notes)
        slides_with_notes = [(i, n) for i, n in enumerate(result) if n.strip()]
        total = len(slides_with_notes)

        if total == 0:
            return result

        for done, (i, note) in enumerate(slides_with_notes):
            on_progress(done / total, f"QA reviewing slide {i+1} ({done+1}/{total})")
            try:
                prompt = (
                    "Review and fix this speaker note for a presentation. "
                    "Fix any grammar, spelling, punctuation, tone inconsistencies, "
                    "and flow issues. Make it sound natural when read aloud. "
                    "Return ONLY the corrected text, nothing else.\n\n"
                    f"{note}"
                )
                fixed = generate(
                    prompt=prompt,
                    model=self.ollama_model,
                    base_url=self.ollama_url,
                )
                if fixed and len(fixed.strip()) > 10:
                    result[i] = fixed.strip()
            except Exception as e:
                logger.warning("QA fix failed for slide %d: %s", i+1, e)

        on_progress(1.0, f"QA complete: {total} slides reviewed")
        return result

    def _generate_audio(self, notes, output_dir, on_progress):
        audio_dir = output_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)

        from core.tts_provider import get_tts_provider
        gen = get_tts_provider()

        paths = []
        for i, note in enumerate(notes):
            on_progress(i / len(notes), f"Generating audio {i+1}/{len(notes)}")
            if not note.strip():
                paths.append(None)
                continue
            out = audio_dir / f"slide_{i+1:03d}.mp3"
            try:
                gen.generate_audio(text=note, voice_id=self.voice_id,
                                   output_path=out, speed=self.speed)
                paths.append(out)
            except Exception as e:
                logger.warning("Audio gen failed for slide %d: %s", i+1, e)
                paths.append(None)
        return paths

    def _create_video(self, pptx_path, notes, audio_paths, output_dir, on_progress):
        """Create the video using the full VideoProcessor pipeline.

        The VideoProcessor handles slide export, TTS audio generation, and
        video assembly. We just need to ensure notes are saved to the project
        so the processor can generate audio from them.
        """
        on_progress(0.0, "Creating video...")
        from services.processing import VideoProcessor
        from services.file_item import FileItem

        output_dir.mkdir(parents=True, exist_ok=True)

        # Reuse existing FileItem (has correct session paths) or create new
        if self.file_item:
            fi = self.file_item
        else:
            fi = FileItem(pptx_path, projects_base=self.projects_base)
            if not fi.load():
                raise RuntimeError(f"Failed to load {pptx_path.name}")

        # Apply the generated/enhanced notes to the project
        pm = fi.project_manager
        if pm and pm.state:
            for i, note in enumerate(notes):
                if i < len(pm.state.slides) and note.strip():
                    pm.state.slides[i].speaker_notes = note
                    pm.state.slides[i].needs_regeneration = True  # Force audio regen
            pm.save()

        # Copy any already-generated audio into the project's audio dir
        if pm and audio_paths:
            import shutil
            pm.audio_dir.mkdir(parents=True, exist_ok=True)
            for i, src_path in enumerate(audio_paths):
                if src_path and Path(src_path).exists():
                    dest = pm.audio_dir / f"slide_{i + 1:03d}_audio.mp3"
                    if not dest.exists():
                        shutil.copy2(str(src_path), str(dest))

        # VideoProcessor generates TTS for any slides still missing audio,
        # exports slide images, and assembles the final MP4
        processor = VideoProcessor(
            voice_id=self.voice_id,
            speed=self.speed,
        )

        def _progress_cb(frac, msg):
            on_progress(frac, msg)

        count = processor.process_files([fi], output_dir, _progress_cb)

        video_path = output_dir / f"{pptx_path.stem}.mp4"
        if video_path.exists():
            on_progress(1.0, f"Video created: {video_path.name}")
            return video_path

        # Try to find any generated video
        mp4s = sorted(output_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        if mp4s:
            on_progress(1.0, f"Video created: {mp4s[0].name}")
            return mp4s[0]

        raise RuntimeError(f"Video generation completed ({count} files) but no MP4 found")

    def _generate_subtitles(self, video_path, output_dir, on_progress):
        on_progress(0.0, "Generating subtitles...")
        from core.subtitle_generator import generate_subtitles
        return generate_subtitles(video_path, output_dir, on_progress=on_progress)

    def _export_metadata(self, notes, title, output_dir):
        from core.video_metadata import export_metadata

        # Create minimal slide data for metadata
        slides_data = []
        for i, note in enumerate(notes):
            class FakeSlide:
                audio_duration = 30.0
                speaker_notes = note
            slides_data.append(FakeSlide())

        return export_metadata(slides_data, output_dir / "youtube_metadata.txt",
                               title=title, ollama_url=self.ollama_url,
                               ollama_model=self.ollama_model)
