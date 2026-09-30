"""Smart Slide Splitter — AI-powered slide content redistribution.

Detects slides with too much content and splits them into 2-3 slides
with redistributed notes and suggested titles.
"""

from typing import List, Dict, Optional
import logging
from core.ollama_client import request_body

logger = logging.getLogger("mediastudio.SPLIT")


def analyze_for_splitting(slides_data: List[Dict],
                           max_words_per_slide: int = 40,
                           max_notes_words: int = 200) -> List[Dict]:
    """Identify slides that should be split.

    Args:
        slides_data: list of dicts with 'index', 'text', 'notes', 'word_count', 'notes_word_count'
        max_words_per_slide: max words on the slide itself
        max_notes_words: max words in speaker notes

    Returns list of dicts: {'index': int, 'reason': str, 'suggested_splits': int}
    """
    candidates = []
    for slide in slides_data:
        wc = slide.get('word_count', 0)
        nwc = slide.get('notes_word_count', 0)

        if wc > max_words_per_slide * 2 or nwc > max_notes_words * 2:
            candidates.append({
                'index': slide['index'],
                'reason': f"Very dense ({wc} slide words, {nwc} notes words)",
                'suggested_splits': 3,
                'word_count': wc,
                'notes_word_count': nwc,
            })
        elif wc > max_words_per_slide or nwc > max_notes_words:
            candidates.append({
                'index': slide['index'],
                'reason': f"Dense ({wc} slide words, {nwc} notes words)",
                'suggested_splits': 2,
                'word_count': wc,
                'notes_word_count': nwc,
            })

    return candidates


def split_notes(notes: str, num_parts: int = 2,
                ollama_url: str = "", ollama_model: str = "") -> List[str]:
    """Split long speaker notes into multiple parts.

    Uses AI if available, otherwise splits at paragraph/sentence boundaries.
    """
    if ollama_url and ollama_model:
        result = _ai_split(notes, num_parts, ollama_url, ollama_model)
        if result:
            return result

    return _rule_split(notes, num_parts)


def _rule_split(notes: str, num_parts: int) -> List[str]:
    """Split notes at paragraph or sentence boundaries."""
    # Try paragraph split first
    paragraphs = [p.strip() for p in notes.split('\n\n') if p.strip()]

    if len(paragraphs) >= num_parts:
        # Distribute paragraphs evenly
        chunk_size = max(1, len(paragraphs) // num_parts)
        parts = []
        for i in range(0, len(paragraphs), chunk_size):
            part = '\n\n'.join(paragraphs[i:i + chunk_size])
            if part.strip():
                parts.append(part)
        # Merge extras into last part
        while len(parts) > num_parts:
            parts[-2] = parts[-2] + '\n\n' + parts[-1]
            parts.pop()
        return parts

    # Fall back to sentence split
    import re
    sentences = re.split(r'(?<=[.!?])\s+', notes)
    if len(sentences) < num_parts:
        return [notes]  # Can't split further

    chunk_size = max(1, len(sentences) // num_parts)
    parts = []
    for i in range(0, len(sentences), chunk_size):
        part = ' '.join(sentences[i:i + chunk_size])
        if part.strip():
            parts.append(part)
    while len(parts) > num_parts:
        parts[-2] = parts[-2] + ' ' + parts[-1]
        parts.pop()
    return parts


def _ai_split(notes: str, num_parts: int, ollama_url: str, ollama_model: str) -> Optional[List[str]]:
    """Use AI to intelligently split notes at logical boundaries."""
    import requests

    prompt = (
        f"Split the following text into exactly {num_parts} parts at natural topic boundaries. "
        f"Each part should be a complete, coherent section. "
        f"Separate parts with the marker '---SPLIT---'. "
        f"Return only the split text, no commentary.\n\n{notes}"
    )

    try:
        resp = requests.post(
            f"{ollama_url.rstrip('/')}/api/generate",
            json=request_body(ollama_model, prompt=prompt),
            timeout=30,
        )
        if resp.status_code == 200:
            result = resp.json().get("response", "")
            parts = [p.strip() for p in result.split("---SPLIT---") if p.strip()]
            if len(parts) == num_parts:
                return parts
    except Exception:
        pass
    return None


def suggest_slide_titles(notes_parts: List[str],
                          ollama_url: str = "", ollama_model: str = "") -> List[str]:
    """Suggest titles for split slide parts using AI."""
    if not ollama_url or not ollama_model:
        return [f"Part {i+1}" for i in range(len(notes_parts))]

    import requests

    titles = []
    for part in notes_parts:
        try:
            prompt = (
                "Generate a short slide title (3-6 words) for this content. "
                "Return only the title, nothing else.\n\n"
                f"{part[:200]}"
            )
            resp = requests.post(
                f"{ollama_url.rstrip('/')}/api/generate",
                json=request_body(ollama_model, prompt=prompt),
                timeout=10,
            )
            if resp.status_code == 200:
                title = resp.json().get("response", "").strip().strip('"').strip("'")
                if title and len(title) < 60:
                    titles.append(title)
                    continue
        except Exception:
            pass
        titles.append(f"Part {len(titles)+1}")

    return titles
