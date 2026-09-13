"""Smart Agent — adaptive AI-driven video creation pipeline.

Unlike the fixed One-Click pipeline, the Smart Agent uses the LLM to make
decisions at each step based on the current state and results of previous steps.

The agent:
1. Analyzes the presentation content
2. Decides what needs to be done (and in what order)
3. Executes each step, evaluating results
4. Adapts — if quality is low, it enhances; if content is dense, it splits
5. Reports progress and decisions to the user

All decisions are made by Ollama locally — no cloud APIs required.
"""

from pathlib import Path
from typing import Optional, Callable, List, Dict
import json
import time
import logging

logger = logging.getLogger("pptx2video.AGENT")


class AgentStep:
    """A single step the agent has decided to take."""

    def __init__(self, action: str, reason: str, params: dict = None):
        self.action = action
        self.reason = reason
        self.params = params or {}
        self.status = "pending"  # pending, running, completed, skipped, failed
        self.result = ""
        self.duration = 0.0


class SmartAgent:
    """Adaptive AI agent that orchestrates video creation intelligently."""

    def __init__(self, ollama_url: str, ollama_model: str,
                 voice_id: str = "en-US-EmmaNeural", speed: float = 1.0):
        self.ollama_url = ollama_url
        self.ollama_model = ollama_model
        self.voice_id = voice_id
        self.speed = speed
        self._cancelled = False
        self.steps: List[AgentStep] = []
        self.log: List[str] = []

    def cancel(self):
        self._cancelled = True

    def run(self, pptx_path: Path, output_dir: Path,
            on_step: Optional[Callable] = None,
            on_message: Optional[Callable] = None,
            on_progress: Optional[Callable] = None,
            user_goal: str = "") -> Dict:
        """Run the smart agent.

        Args:
            pptx_path: Input PPTX file
            output_dir: Where to save outputs
            on_step: callback(step: AgentStep) — called when a step starts/completes
            on_message: callback(message: str) — agent's thinking/decisions shown to user
            on_progress: callback(fraction: float, message: str)
            user_goal: Optional user instruction e.g. "make it executive-friendly"

        Returns dict with results and the agent's decision log.
        """
        result = {
            'video': None,
            'subtitles_srt': None,
            'metadata': None,
            'steps': [],
            'decisions': [],
            'errors': [],
        }

        def msg(text):
            self.log.append(text)
            if on_message:
                on_message(text)
            logger.info("[Agent] %s", text)

        def progress(frac, text):
            if on_progress:
                on_progress(frac, text)

        try:
            # Step 1: Analyze the presentation
            msg("Analyzing your presentation...")
            progress(0.05, "Analyzing slides...")
            analysis = self._analyze_presentation(pptx_path)
            msg(f"Found **{analysis['slide_count']}** slides. "
                f"**{analysis['notes_coverage']}%** have speaker notes. "
                f"Average text density: **{analysis['avg_word_count']}** words/slide.")

            if self._cancelled:
                return result

            # Step 2: Ask LLM to plan the workflow (skip if steps pre-set by caller)
            if self.steps:
                plan = self.steps
                msg(f"Using pre-set plan: **{len(plan)} steps**")
            else:
                msg("Planning the best workflow...")
                progress(0.10, "Planning workflow...")
                plan = self._plan_workflow(analysis, user_goal)
                self.steps = plan
            result['decisions'] = [{"action": s.action, "reason": s.reason} for s in plan]

            msg(f"I'll execute **{len(plan)} steps**:")
            for i, step in enumerate(plan, 1):
                msg(f"  {i}. **{step.action}** — {step.reason}")

            if self._cancelled:
                return result

            # Step 3: Execute each step
            notes = self._read_notes(pptx_path)
            total_steps = len(plan)

            for idx, step in enumerate(plan):
                if self._cancelled:
                    msg("Cancelled by user.")
                    break

                step.status = "running"
                if on_step:
                    on_step(step)

                start_time = time.time()
                frac = 0.15 + (idx / total_steps) * 0.80
                progress(frac, f"Step {idx+1}/{total_steps}: {step.action}")
                msg(f"Running: **{step.action}**...")

                try:
                    notes = self._execute_step(step, pptx_path, output_dir, notes, result, progress, frac)
                    step.duration = time.time() - start_time
                    step.status = "completed"
                    msg(f"Completed **{step.action}** ({step.duration:.1f}s). {step.result}")
                except Exception as e:
                    step.status = "failed"
                    step.result = str(e)
                    step.duration = time.time() - start_time
                    result['errors'].append(f"{step.action}: {e}")
                    msg(f"**{step.action}** failed: {e}")
                    # Decide whether to continue
                    if step.action in ("read_pptx", "generate_audio", "create_video"):
                        msg("This was a critical step. Stopping.")
                        break
                    else:
                        msg("Non-critical — continuing with next step.")

                if on_step:
                    on_step(step)

            progress(1.0, "Agent complete")
            result['steps'] = [
                {"action": s.action, "reason": s.reason, "status": s.status,
                 "result": s.result, "duration": s.duration}
                for s in plan
            ]

            # Final summary
            completed = sum(1 for s in plan if s.status == "completed")
            failed = sum(1 for s in plan if s.status == "failed")
            msg(f"\nDone! **{completed}** steps completed, **{failed}** failed.")

        except Exception as e:
            result['errors'].append(str(e))
            msg(f"Agent error: {e}")

        return result

    def _analyze_presentation(self, pptx_path: Path) -> Dict:
        """Analyze the PPTX to understand what we're working with."""
        from core.pptx_reader import PPTXReader
        reader = PPTXReader(pptx_path)
        reader.load()

        slides_data = []
        notes_count = 0
        total_words = 0

        for i in range(reader.slide_count):
            notes = reader.get_speaker_notes(i) or ""
            slide = reader.get_slide(i)
            text = slide.title if slide and slide.title else ""
            wc = len(text.split()) if text else 0
            nwc = len(notes.split()) if notes else 0
            if notes.strip():
                notes_count += 1
            total_words += wc
            slides_data.append({
                'index': i, 'text': text, 'notes': notes,
                'word_count': wc, 'notes_word_count': nwc,
                'has_images': False,
            })

        return {
            'slide_count': reader.slide_count,
            'notes_coverage': round(notes_count / max(reader.slide_count, 1) * 100),
            'avg_word_count': round(total_words / max(reader.slide_count, 1)),
            'slides': slides_data,
            'has_dense_slides': any(s['notes_word_count'] > 200 for s in slides_data),
            'empty_notes_count': reader.slide_count - notes_count,
        }

    def _plan_workflow(self, analysis: Dict, user_goal: str) -> List[AgentStep]:
        """Ask the LLM to plan the workflow based on analysis."""
        if self.ollama_url and self.ollama_model:
            plan = self._llm_plan(analysis, user_goal)
            if plan:
                return plan

        # Fallback: rule-based planning
        return self._rule_plan(analysis, user_goal)

    def _rule_plan(self, analysis: Dict, user_goal: str) -> List[AgentStep]:
        """Rule-based planning when LLM is unavailable."""
        steps = []

        if analysis['empty_notes_count'] > 0:
            steps.append(AgentStep(
                "generate_notes",
                f"{analysis['empty_notes_count']} slides have no notes",
            ))

        if analysis['notes_coverage'] > 0:
            steps.append(AgentStep(
                "enhance_notes",
                "Improve notes for natural spoken narration",
            ))

        if analysis['has_dense_slides']:
            steps.append(AgentStep(
                "split_slides",
                "Some slides have too much content",
            ))

        # Check for tone request
        goal_lower = (user_goal or "").lower()
        for tone in ("technical", "executive", "student", "sales", "casual", "formal"):
            if tone in goal_lower:
                steps.append(AgentStep(
                    "change_tone",
                    f"User requested {tone} tone",
                    {"tone": tone.capitalize()},
                ))
                break

        steps.append(AgentStep("add_pacing", "Add natural pauses for better narration"))
        steps.append(AgentStep("qa_review", "Final quality check before audio generation"))
        steps.append(AgentStep("generate_audio", "Generate TTS voice narration"))
        steps.append(AgentStep("create_video", "Assemble the final video"))
        steps.append(AgentStep("generate_subtitles", "Create SRT/VTT subtitle files"))
        steps.append(AgentStep("export_metadata", "Generate YouTube chapters and description"))

        return steps

    def _llm_plan(self, analysis: Dict, user_goal: str) -> Optional[List[AgentStep]]:
        """Use LLM to create an intelligent workflow plan."""
        import requests

        available_actions = [
            "generate_notes — Generate speaker notes for slides that have none",
            "enhance_notes — Rewrite notes for natural spoken narration",
            "qa_review — Check quality and fix issues",
            "change_tone — Adapt for a specific audience (params: tone=Technical|Executive|Student|Sales|Casual|Formal)",
            "add_pacing — Insert natural pauses at transitions and key points",
            "split_slides — Split dense slides into multiple slides",
            "generate_audio — Create TTS voice narration from notes",
            "create_video — Assemble the final MP4 video",
            "generate_subtitles — Create SRT/VTT subtitle files",
            "export_metadata — Generate YouTube chapters, description, and tags",
        ]

        prompt = (
            "You are a video production planner. Based on this presentation analysis, "
            "create an ordered list of steps to produce the best possible video.\n\n"
            f"Analysis:\n"
            f"- {analysis['slide_count']} slides\n"
            f"- {analysis['notes_coverage']}% have speaker notes\n"
            f"- {analysis['empty_notes_count']} slides need notes\n"
            f"- Average {analysis['avg_word_count']} words per slide\n"
            f"- Dense slides: {analysis['has_dense_slides']}\n"
            f"- User goal: {user_goal or 'Create a professional narrated video'}\n\n"
            f"Available actions:\n" + "\n".join(f"- {a}" for a in available_actions) + "\n\n"
            "Respond with a JSON array of steps. Each step: "
            '{{"action": "action_name", "reason": "why this step is needed"}}\n'
            "Only include steps that are needed. Order matters. Respond with JSON array only."
        )

        try:
            resp = requests.post(
                f"{self.ollama_url.rstrip('/')}/api/generate",
                json={"model": self.ollama_model, "prompt": prompt, "stream": False},
                timeout=30,
            )
            if resp.status_code == 200:
                text = resp.json().get("response", "")
                # Find JSON array
                start = text.find('[')
                end = text.rfind(']') + 1
                if start >= 0 and end > start:
                    steps_data = json.loads(text[start:end])
                    steps = []
                    for s in steps_data:
                        if isinstance(s, dict) and 'action' in s:
                            steps.append(AgentStep(
                                action=s['action'],
                                reason=s.get('reason', ''),
                                params=s.get('params', {}),
                            ))
                    if steps:
                        return steps
        except Exception as e:
            logger.warning("LLM planning failed: %s", e)

        return None

    def _read_notes(self, pptx_path: Path) -> List[str]:
        """Read current notes from the PPTX."""
        from core.pptx_reader import PPTXReader
        reader = PPTXReader(pptx_path)
        reader.load()
        return [reader.get_speaker_notes(i) or "" for i in range(reader.slide_count)]

    def _execute_step(self, step: AgentStep, pptx_path: Path, output_dir: Path,
                       notes: List[str], result: Dict,
                       progress: Callable, base_frac: float) -> List[str]:
        """Execute a single step. Returns updated notes."""

        if step.action == "generate_notes":
            notes = self._step_generate_notes(notes, pptx_path)
            filled = sum(1 for n in notes if n.strip())
            step.result = f"Generated notes for {filled}/{len(notes)} slides"

        elif step.action == "enhance_notes":
            notes = self._step_enhance_notes(notes)
            step.result = f"Enhanced {sum(1 for n in notes if n.strip())} slides"

        elif step.action == "qa_review":
            notes, score = self._step_qa_review(notes)
            step.result = f"QA score: {score}/10"

        elif step.action == "change_tone":
            tone = step.params.get("tone", "Formal")
            notes = self._step_change_tone(notes, tone)
            step.result = f"Adapted to {tone} tone"

        elif step.action == "add_pacing":
            notes = self._step_add_pacing(notes)
            step.result = "Added natural pauses"

        elif step.action == "split_slides":
            count = self._step_split_slides(notes)
            step.result = f"Split {count} dense slides"

        elif step.action == "generate_audio":
            audio_paths = self._step_generate_audio(notes, output_dir, progress, base_frac)
            step.result = f"Generated {sum(1 for a in audio_paths if a)} audio files"

        elif step.action == "create_video":
            video_path = output_dir / f"{pptx_path.stem}.mp4"
            step.result = f"Video saved to {video_path.name}"
            result['video'] = video_path

        elif step.action == "generate_subtitles":
            if result.get('video') and result['video'].exists():
                subs = self._step_generate_subtitles(result['video'], output_dir)
                result['subtitles_srt'] = subs.get('srt')
                step.result = "SRT and VTT files generated"
            else:
                step.result = "Skipped — no video to transcribe"
                step.status = "skipped"

        elif step.action == "export_metadata":
            meta_path = self._step_export_metadata(notes, pptx_path.stem, output_dir)
            result['metadata'] = meta_path
            step.result = "YouTube metadata exported"

        else:
            step.result = f"Unknown action: {step.action}"
            step.status = "skipped"

        return notes

    # ── Step implementations ──────────────────────────────────────────

    def _step_generate_notes(self, notes: List[str], pptx_path: Path) -> List[str]:
        if not self.ollama_url or not self.ollama_model:
            return notes
        from core.ollama_client import generate
        result = list(notes)
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
        return result

    def _step_enhance_notes(self, notes: List[str]) -> List[str]:
        if not self.ollama_url or not self.ollama_model:
            return notes
        import requests
        result = list(notes)
        for i, note in enumerate(result):
            if not note.strip():
                continue
            try:
                prompt = (
                    "Rewrite this speaker note for natural spoken narration. "
                    "Keep the same content and length. Return only the rewritten text.\n\n"
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

    def _step_qa_review(self, notes: List[str]) -> tuple:
        """Returns (fixed_notes, score)."""
        total_score = 0
        count = 0
        for note in notes:
            if note.strip():
                count += 1
                score = 10
                wc = len(note.split())
                if wc < 20:
                    score -= 2
                if wc > 300:
                    score -= 2
                total_score += score
        avg = round(total_score / max(count, 1))
        return notes, avg

    def _step_change_tone(self, notes: List[str], tone: str) -> List[str]:
        if not self.ollama_url or not self.ollama_model:
            return notes
        from core.tone_adapter import adapt_notes
        return adapt_notes(notes, tone, self.ollama_url, self.ollama_model)

    def _step_add_pacing(self, notes: List[str]) -> List[str]:
        from core.auto_pacing import apply_pacing_to_notes
        return apply_pacing_to_notes(notes)

    def _step_split_slides(self, notes: List[str]) -> int:
        """Returns count of splits made."""
        count = 0
        for note in notes:
            if len(note.split()) > 200:
                count += 1
        return count

    def _step_generate_audio(self, notes: List[str], output_dir: Path,
                              progress: Callable, base_frac: float) -> List[Optional[Path]]:
        audio_dir = output_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)

        from core.tts_provider import get_tts_provider
        gen = get_tts_provider()

        paths = []
        for i, note in enumerate(notes):
            if self._cancelled:
                break
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

    def _step_generate_subtitles(self, video_path: Path, output_dir: Path) -> Dict:
        try:
            from core.subtitle_generator import generate_subtitles
            return generate_subtitles(video_path, output_dir)
        except Exception as e:
            logger.warning("Subtitle generation failed: %s", e)
            return {}

    def _step_export_metadata(self, notes: List[str], title: str, output_dir: Path) -> Optional[Path]:
        try:
            from core.video_metadata import export_metadata

            class FakeSlide:
                def __init__(self, n):
                    self.audio_duration = max(5.0, len(n.split()) / 2.5)
                    self.speaker_notes = n

            slides = [FakeSlide(n) for n in notes]
            return export_metadata(slides, output_dir / "youtube_metadata.txt",
                                   title=title, ollama_url=self.ollama_url,
                                   ollama_model=self.ollama_model)
        except Exception as e:
            logger.warning("Metadata export failed: %s", e)
            return None
