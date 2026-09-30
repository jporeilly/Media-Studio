"""Q&A Generator — auto-generate anticipated questions and answers from presentation content.

Uses Ollama to analyze slide content and generate likely audience questions
with prepared answers, exportable as a companion document.
"""

from pathlib import Path
from typing import List, Dict, Optional, Callable
import logging
from core.ollama_client import request_body

logger = logging.getLogger("mediastudio.QA_GEN")


def generate_qa(
    slide_notes: List[str],
    ollama_url: str,
    ollama_model: str,
    num_questions: int = 10,
    on_progress: Optional[Callable] = None,
) -> List[Dict]:
    """Generate anticipated Q&A from presentation content.

    Args:
        slide_notes: List of speaker notes
        ollama_url: Ollama server URL
        ollama_model: Model to use
        num_questions: Number of Q&A pairs to generate
        on_progress: callback(fraction, message)

    Returns list of dicts: {'question': str, 'answer': str, 'slide_ref': int}
    """
    import requests

    if on_progress:
        on_progress(0.1, "Analyzing presentation content...")

    # Combine notes into a content summary
    combined = "\n\n".join(
        f"Slide {i+1}: {note}"
        for i, note in enumerate(slide_notes)
        if note.strip()
    )

    # Truncate to fit context
    if len(combined) > 4000:
        combined = combined[:4000] + "..."

    prompt = (
        f"Based on this presentation content, generate exactly {num_questions} likely audience questions "
        f"with prepared answers. For each, note which slide is most relevant.\n\n"
        f"Presentation content:\n{combined}\n\n"
        f"Format each Q&A as:\n"
        f"Q: [question]\n"
        f"A: [answer]\n"
        f"Slide: [number]\n\n"
        f"Generate {num_questions} Q&A pairs:"
    )

    if on_progress:
        on_progress(0.3, "Generating Q&A pairs...")

    try:
        resp = requests.post(
            f"{ollama_url.rstrip('/')}/api/generate",
            json=request_body(ollama_model, prompt=prompt),
            timeout=60,
        )

        if resp.status_code != 200:
            logger.error("Ollama returned %d", resp.status_code)
            return []

        text = resp.json().get("response", "")
        qa_pairs = _parse_qa_response(text, len(slide_notes))

        if on_progress:
            on_progress(1.0, f"Generated {len(qa_pairs)} Q&A pairs")

        return qa_pairs

    except Exception as e:
        logger.error("Q&A generation failed: %s", e)
        return []


def _parse_qa_response(text: str, num_slides: int) -> List[Dict]:
    """Parse the AI response into structured Q&A pairs."""
    import re

    pairs = []
    current_q = ""
    current_a = ""
    current_slide = 0

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        if line.upper().startswith("Q:") or line.upper().startswith("QUESTION:"):
            # Save previous pair
            if current_q and current_a:
                pairs.append({
                    'question': current_q,
                    'answer': current_a,
                    'slide_ref': current_slide,
                })
            current_q = re.sub(r'^Q:\s*|^QUESTION:\s*', '', line, flags=re.IGNORECASE).strip()
            current_a = ""
            current_slide = 0
        elif line.upper().startswith("A:") or line.upper().startswith("ANSWER:"):
            current_a = re.sub(r'^A:\s*|^ANSWER:\s*', '', line, flags=re.IGNORECASE).strip()
        elif line.upper().startswith("SLIDE:"):
            try:
                num = re.search(r'\d+', line)
                if num:
                    s = int(num.group())
                    current_slide = min(s, num_slides)
            except ValueError:
                pass
        elif current_a:
            current_a += " " + line

    # Don't forget last pair
    if current_q and current_a:
        pairs.append({
            'question': current_q,
            'answer': current_a,
            'slide_ref': current_slide,
        })

    return pairs


def export_qa_document(qa_pairs: List[Dict], output_path: Path, title: str = "") -> Path:
    """Export Q&A pairs to a formatted text file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    if title:
        lines.append(f"Q&A Document: {title}")
        lines.append("=" * 60)
        lines.append("")

    lines.append(f"Anticipated Questions & Answers ({len(qa_pairs)} items)")
    lines.append("-" * 60)
    lines.append("")

    for i, qa in enumerate(qa_pairs, 1):
        lines.append(f"{i}. Q: {qa['question']}")
        lines.append(f"   A: {qa['answer']}")
        if qa.get('slide_ref'):
            lines.append(f"   (Reference: Slide {qa['slide_ref']})")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path
