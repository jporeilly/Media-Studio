"""Tone/Audience Adapter — rewrites speaker notes for different audiences.

Uses Ollama to adapt content for:
- Technical (detailed, jargon-friendly)
- Executive (high-level, business impact)
- Student (simple, explanatory)
- Sales (persuasive, benefit-focused)
- Casual (conversational, friendly)
"""

from typing import List, Optional, Callable
import logging

logger = logging.getLogger("pptx2video.TONE")

TONE_PRESETS = {
    "Technical": {
        "description": "Detailed, uses industry jargon, includes specifics",
        "prompt": "Rewrite for a technical audience. Use precise terminology, include specific details, and assume domain knowledge. Keep the same information but make it technically rigorous.",
    },
    "Executive": {
        "description": "High-level, focuses on business impact and ROI",
        "prompt": "Rewrite for C-level executives. Focus on business impact, ROI, and strategic value. Remove technical details, use clear business language. Be concise and action-oriented.",
    },
    "Student": {
        "description": "Simple language, explanatory, builds understanding",
        "prompt": "Rewrite for students new to this topic. Use simple language, explain concepts step by step, avoid jargon. Add brief explanations for any technical terms you must use.",
    },
    "Sales": {
        "description": "Persuasive, benefit-focused, creates urgency",
        "prompt": "Rewrite for a sales presentation. Focus on customer benefits, competitive advantages, and business outcomes. Use persuasive language that creates interest and urgency.",
    },
    "Casual": {
        "description": "Conversational, friendly, engaging",
        "prompt": "Rewrite in a casual, conversational tone. Make it sound like you're explaining to a friend over coffee. Keep it engaging and approachable, but still informative.",
    },
    "Formal": {
        "description": "Professional, structured, authoritative",
        "prompt": "Rewrite in a formal, professional tone suitable for an industry conference. Use structured language, maintain authority, and present information with gravitas.",
    },
}


def get_available_tones() -> dict:
    """Return available tone presets with descriptions."""
    return {k: v["description"] for k, v in TONE_PRESETS.items()}


def adapt_notes(
    notes: List[str],
    tone: str,
    ollama_url: str,
    ollama_model: str,
    custom_prompt: str = "",
    on_progress: Optional[Callable] = None,
) -> List[str]:
    """Adapt all speaker notes to a target tone/audience.

    Args:
        notes: List of speaker note strings
        tone: Key from TONE_PRESETS or "Custom"
        ollama_url: Ollama server URL
        ollama_model: Model to use
        custom_prompt: Custom adaptation prompt (used when tone="Custom")
        on_progress: callback(fraction, message)

    Returns list of adapted note strings.
    """
    import requests

    if tone == "Custom" and custom_prompt:
        base_prompt = custom_prompt
    elif tone in TONE_PRESETS:
        base_prompt = TONE_PRESETS[tone]["prompt"]
    else:
        logger.error("Unknown tone: %s", tone)
        return notes

    adapted = []
    for i, note in enumerate(notes):
        if on_progress:
            on_progress(i / len(notes), f"Adapting slide {i+1}/{len(notes)} to {tone} tone...")

        if not note.strip():
            adapted.append("")
            continue

        prompt = f"{base_prompt}\n\nOriginal text:\n{note}\n\nRewritten text:"

        try:
            resp = requests.post(
                f"{ollama_url.rstrip('/')}/api/generate",
                json={"model": ollama_model, "prompt": prompt, "stream": False},
                timeout=30,
            )
            if resp.status_code == 200:
                result = resp.json().get("response", "").strip()
                if result and len(result) > 10:
                    adapted.append(result)
                    continue
        except Exception as e:
            logger.warning("Adaptation failed for slide %d: %s", i+1, e)

        adapted.append(note)  # Fallback to original

    if on_progress:
        on_progress(1.0, f"Adapted {len(adapted)} slides to {tone} tone")

    return adapted
