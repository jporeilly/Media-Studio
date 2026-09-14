"""AI-powered slide content analysis using Ollama.

Analyzes slides for:
- Text density (too much/too little text per slide)
- Speaker notes quality (length, coverage)
- Missing visuals
- Consistency issues
"""

from pathlib import Path
from typing import List, Dict
import logging

logger = logging.getLogger("mediastudio.ANALYZE")


def analyze_slide_content(slides_data: List[Dict], ollama_url: str = "",
                          ollama_model: str = "", on_progress=None) -> Dict:
    """Analyze slide content and return improvement suggestions.

    Args:
        slides_data: list of dicts with keys: 'index', 'text', 'notes', 'has_images', 'word_count', 'notes_word_count'
        ollama_url: Ollama server URL
        ollama_model: model to use
        on_progress: callback(fraction, message)

    Returns dict with:
        'overall_score': int (1-10),
        'summary': str,
        'slides': list of per-slide analysis dicts,
        'suggestions': list of improvement strings
    """
    results = {
        'overall_score': 0,
        'summary': '',
        'slides': [],
        'suggestions': [],
    }

    total_issues = 0

    for i, slide in enumerate(slides_data):
        if on_progress:
            on_progress(i / len(slides_data), f"Analyzing slide {i+1}/{len(slides_data)}...")

        issues = []
        score = 10

        # Text density check
        wc = slide.get('word_count', 0)
        if wc > 80:
            issues.append(f"Too much text ({wc} words) — aim for under 40 words per slide")
            score -= 2
        elif wc > 40:
            issues.append(f"Consider reducing text ({wc} words) — slides work best with under 40 words")
            score -= 1
        elif wc == 0:
            issues.append("No text on slide — consider adding a title or key point")
            score -= 1

        # Speaker notes check
        nwc = slide.get('notes_word_count', 0)
        if nwc == 0:
            issues.append("No speaker notes — add narration text for TTS")
            score -= 3
        elif nwc < 20:
            issues.append(f"Very short speaker notes ({nwc} words) — expand for better narration")
            score -= 1
        elif nwc > 300:
            issues.append(f"Very long speaker notes ({nwc} words) — consider splitting across slides")
            score -= 1

        # Visual check
        if not slide.get('has_images', False) and wc > 0:
            issues.append("Text-only slide — consider adding a diagram, chart, or image")
            score -= 1

        total_issues += len(issues)
        results['slides'].append({
            'index': i,
            'score': max(1, score),
            'issues': issues,
        })

    # Overall score
    avg_score = sum(s['score'] for s in results['slides']) / max(len(results['slides']), 1)
    results['overall_score'] = round(avg_score)

    # AI-powered deep analysis if Ollama available
    if ollama_url and ollama_model:
        try:
            ai_suggestions = _get_ai_suggestions(slides_data, ollama_url, ollama_model)
            if ai_suggestions:
                results['suggestions'] = ai_suggestions
        except Exception as e:
            logger.warning("AI analysis failed: %s", e)

    # Generate summary
    if total_issues == 0:
        results['summary'] = "Excellent! All slides look well-structured for video generation."
    elif total_issues <= 3:
        results['summary'] = f"Good overall. {total_issues} minor suggestions for improvement."
    elif total_issues <= 8:
        results['summary'] = f"Fair. {total_issues} issues found that could improve video quality."
    else:
        results['summary'] = f"Needs work. {total_issues} issues found — addressing these will significantly improve the output."

    if on_progress:
        on_progress(1.0, "Analysis complete")

    return results


def _get_ai_suggestions(slides_data: List[Dict], ollama_url: str, ollama_model: str) -> List[str]:
    """Get AI-powered suggestions from Ollama."""
    import requests

    # Build a compact summary of the presentation
    slide_summaries = []
    for s in slides_data[:20]:  # Limit to first 20 slides
        slide_summaries.append(
            f"Slide {s['index']+1}: {s.get('word_count', 0)} words on slide, "
            f"{s.get('notes_word_count', 0)} words in notes, "
            f"{'has images' if s.get('has_images') else 'text only'}"
        )

    prompt = (
        "You are a presentation design expert. Analyze this presentation structure "
        "and give 3-5 specific, actionable suggestions to improve it for video generation. "
        "Focus on: pacing, visual variety, narration quality, and audience engagement.\n\n"
        + "\n".join(slide_summaries)
        + "\n\nRespond with only a numbered list of suggestions, no preamble."
    )

    try:
        resp = requests.post(
            f"{ollama_url.rstrip('/')}/api/generate",
            json={"model": ollama_model, "prompt": prompt, "stream": False},
            timeout=30,
        )
        if resp.status_code == 200:
            text = resp.json().get("response", "")
            # Parse numbered list
            suggestions = []
            for line in text.strip().split("\n"):
                line = line.strip()
                if line and (line[0].isdigit() or line.startswith("-")):
                    # Remove leading number/bullet
                    clean = line.lstrip("0123456789.-) ").strip()
                    if clean:
                        suggestions.append(clean)
            return suggestions[:5]
    except Exception:
        pass
    return []


def extract_slide_data(pptx_path: Path) -> List[Dict]:
    """Extract analysis-relevant data from a PPTX file."""
    from pptx import Presentation

    prs = Presentation(str(pptx_path))
    slides_data = []

    for i, slide in enumerate(prs.slides):
        text_parts = []
        has_images = False

        for shape in slide.shapes:
            if shape.has_text_frame:
                text_parts.append(shape.text_frame.text)
            if shape.shape_type and shape.shape_type in (13, 17):  # Picture, Placeholder with picture
                has_images = True
            # Also check for any image-like shapes
            if hasattr(shape, 'image'):
                has_images = True

        full_text = " ".join(text_parts)
        notes_text = ""
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
            notes_text = slide.notes_slide.notes_text_frame.text

        slides_data.append({
            'index': i,
            'text': full_text,
            'notes': notes_text,
            'has_images': has_images,
            'word_count': len(full_text.split()) if full_text.strip() else 0,
            'notes_word_count': len(notes_text.split()) if notes_text.strip() else 0,
        })

    return slides_data
