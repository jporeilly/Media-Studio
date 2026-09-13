"""YouTube metadata generator — chapters, descriptions, and tags from slide data."""

import math
from pathlib import Path
from typing import List, Optional

from utils.logger import get_logger

logger = get_logger("META")


def _format_timestamp(seconds: float) -> str:
    """Format seconds into M:SS or H:MM:SS timestamp."""
    total = max(0, int(math.floor(seconds)))
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _chapter_title_from_notes(notes: str, index: int) -> str:
    """Derive a short chapter title from speaker notes."""
    if not notes or not notes.strip():
        return f"Slide {index + 1}"
    first_line = notes.strip().split("\n")[0].strip()
    # Truncate to ~50 chars at a word boundary
    if len(first_line) > 50:
        truncated = first_line[:50].rsplit(" ", 1)[0]
        first_line = truncated if truncated else first_line[:50]
    # Remove trailing punctuation that looks odd as a title
    first_line = first_line.rstrip(".,;:!?")
    return first_line or f"Slide {index + 1}"


def generate_youtube_chapters(
    slides: list,
    transition_pause: float = 0.8,
    slide_infos: Optional[list] = None,
) -> str:
    """Generate YouTube chapter timestamps from slide data.

    Each slide becomes a chapter. Format:
    0:00 Introduction
    1:23 Key Features
    5:45 Architecture Overview

    Args:
        slides: list of SlideRenderState objects (have audio_duration, speaker_notes)
        transition_pause: pause between slides in seconds
        slide_infos: optional list of SlideInfo objects (have .title) from PPTXReader
    Returns:
        Formatted chapter string ready to paste into YouTube description
    """
    if not slides:
        return ""

    lines = []
    current_time = 0.0

    for i, slide in enumerate(slides):
        # Prefer SlideInfo.title if available, fall back to notes
        title = None
        if slide_infos and i < len(slide_infos):
            title = getattr(slide_infos[i], "title", None)
            if title:
                title = title.strip()
        if not title:
            title = _chapter_title_from_notes(slide.speaker_notes, i)

        timestamp = _format_timestamp(current_time)
        lines.append(f"{timestamp} {title}")

        # Advance by audio duration + transition pause
        duration = slide.audio_duration or 0.0
        current_time += duration + transition_pause

    return "\n".join(lines)


def generate_video_description(
    slides: list,
    title: str = "",
    ollama_url: str = "",
    ollama_model: str = "",
    transition_pause: float = 0.8,
    slide_infos: Optional[list] = None,
) -> str:
    """Generate an AI-powered video description with summary, key topics, and tags.

    If Ollama is available, uses AI to generate a professional description.
    Otherwise, generates a basic description from slide notes.

    Returns a formatted string with:
    - Title
    - Summary (2-3 sentences)
    - Key Topics (bulleted list)
    - Chapters (timestamps)
    - Tags (comma-separated)
    """
    chapters = generate_youtube_chapters(slides, transition_pause, slide_infos)

    # Collect all notes content for context
    all_notes = []
    for s in slides:
        if s.speaker_notes and s.speaker_notes.strip():
            all_notes.append(s.speaker_notes.strip())
    notes_text = "\n\n".join(all_notes)

    # Try AI-powered description via Ollama
    ai_description = ""
    if ollama_url and ollama_model and notes_text:
        ai_description = _generate_ai_description(
            notes_text, title, ollama_url, ollama_model
        )

    # Build the output
    parts = []

    if title:
        parts.append(title)
        parts.append("")

    if ai_description:
        parts.append(ai_description)
        parts.append("")
    elif notes_text:
        # Fallback: use first 2-3 sentences from notes as summary
        summary = _extract_summary(notes_text)
        if summary:
            parts.append(summary)
            parts.append("")

    # Key topics from slide titles/notes
    topics = _extract_topics(slides, slide_infos)
    if topics:
        parts.append("Key Topics:")
        for topic in topics:
            parts.append(f"  - {topic}")
        parts.append("")

    # Chapters
    if chapters:
        parts.append("Chapters:")
        parts.append(chapters)
        parts.append("")

    # Tags
    tags = _extract_tags(slides, slide_infos, title)
    if tags:
        parts.append("Tags: " + ", ".join(tags))

    return "\n".join(parts)


def export_metadata(
    slides: list,
    output_path: Path,
    title: str = "",
    ollama_url: str = "",
    ollama_model: str = "",
    transition_pause: float = 0.8,
    slide_infos: Optional[list] = None,
) -> Path:
    """Export complete video metadata to a .txt file.

    Combines chapters + description + tags.
    Returns the path to the exported file.
    """
    content = generate_video_description(
        slides=slides,
        title=title,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        transition_pause=transition_pause,
        slide_infos=slide_infos,
    )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    logger.info("Exported video metadata to %s", output_path)
    return output_path


def _generate_ai_description(
    notes_text: str, title: str, ollama_url: str, ollama_model: str
) -> str:
    """Use Ollama to generate a professional video description."""
    from core import ollama_client

    try:
        if not ollama_client.check_connection(ollama_url):
            logger.debug("Ollama not reachable, skipping AI description")
            return ""

        prompt = (
            "Based on the following video narration script, write a concise YouTube "
            "video description (2-3 sentences). Focus on what the viewer will learn. "
            "Return ONLY the description text, no headings or labels.\n\n"
        )
        if title:
            prompt += f"Video title: {title}\n\n"
        # Limit notes to avoid overly long prompts
        truncated = notes_text[:3000]
        prompt += f"Script:\n{truncated}"

        system = (
            "You are an expert YouTube content creator. Write professional, "
            "engaging video descriptions that are concise and SEO-friendly."
        )

        result = ollama_client.generate(
            prompt=prompt,
            model=ollama_model,
            system=system,
            base_url=ollama_url,
            timeout=30.0,
        )
        return result.strip()
    except Exception as e:
        logger.warning("AI description generation failed: %s", e)
        return ""


def _extract_summary(notes_text: str) -> str:
    """Extract the first 2-3 sentences from the notes as a basic summary."""
    import re
    sentences = re.split(r'(?<=[.!?])\s+', notes_text.strip())
    summary_sentences = sentences[:3]
    summary = " ".join(summary_sentences)
    if len(summary) > 300:
        summary = summary[:300].rsplit(" ", 1)[0] + "..."
    return summary


def _extract_topics(slides: list, slide_infos: Optional[list] = None) -> List[str]:
    """Extract key topics from slide titles or first lines of notes."""
    topics = []
    for i, slide in enumerate(slides):
        title = None
        if slide_infos and i < len(slide_infos):
            title = getattr(slide_infos[i], "title", None)
            if title:
                title = title.strip()
        if not title:
            title = _chapter_title_from_notes(slide.speaker_notes, i)
        # Skip generic "Slide N" titles
        if not title.startswith("Slide "):
            topics.append(title)
    return topics


def _extract_tags(
    slides: list, slide_infos: Optional[list] = None, title: str = ""
) -> List[str]:
    """Extract relevant tags from slide content."""
    # Collect words from titles and notes
    words: dict[str, int] = {}
    sources = []

    if title:
        sources.append(title)

    for i, slide in enumerate(slides):
        if slide_infos and i < len(slide_infos):
            t = getattr(slide_infos[i], "title", None)
            if t:
                sources.append(t)
        if slide.speaker_notes:
            sources.append(slide.speaker_notes)

    # Simple word frequency analysis for tags
    import re
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "need", "dare", "ought",
        "and", "but", "or", "nor", "not", "so", "yet", "both", "either",
        "neither", "each", "every", "all", "any", "few", "more", "most",
        "other", "some", "such", "no", "only", "own", "same", "than",
        "too", "very", "just", "because", "as", "until", "while", "of",
        "at", "by", "for", "with", "about", "between", "through", "during",
        "before", "after", "above", "below", "to", "from", "up", "down",
        "in", "out", "on", "off", "over", "under", "again", "then", "once",
        "here", "there", "when", "where", "why", "how", "what", "which",
        "who", "whom", "this", "that", "these", "those", "i", "me", "my",
        "we", "our", "you", "your", "he", "him", "his", "she", "her", "it",
        "its", "they", "them", "their", "also", "into", "let", "us",
        "slide", "slides", "presentation",
    }

    for text in sources:
        for word in re.findall(r'\b[a-zA-Z]{3,}\b', text.lower()):
            if word not in stop_words:
                words[word] = words.get(word, 0) + 1

    # Return top tags by frequency
    sorted_words = sorted(words.items(), key=lambda x: x[1], reverse=True)
    tags = [w for w, _ in sorted_words[:10]]
    return tags
