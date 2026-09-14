"""Auto-Pacing — analyzes speaker notes and inserts timing markers.

Detects key points, transitions, and emphasis markers to control
TTS pacing for more natural-sounding narration.

Uses SSML-style markers:
- <break time="500ms"/> — pause at transitions
- Sentence-level pacing adjustments
- Emphasis detection for key terms
"""

from typing import List, Optional, Callable
import re
import logging

logger = logging.getLogger("mediastudio.PACING")


def analyze_pacing(notes: List[str]) -> List[dict]:
    """Analyze speaker notes and suggest pacing improvements.

    Returns list of dicts per slide:
    {
        'index': int,
        'original': str,
        'paced': str,
        'changes': list of description strings,
        'estimated_duration_change': float (seconds, positive = slower)
    }
    """
    results = []
    for i, note in enumerate(notes):
        if not note.strip():
            results.append({
                'index': i, 'original': note, 'paced': note,
                'changes': [], 'estimated_duration_change': 0.0,
            })
            continue

        paced, changes = _apply_pacing_rules(note)

        # Estimate duration change (each pause adds ~0.5s)
        pause_count = len(re.findall(r'\.\.\.', paced)) - len(re.findall(r'\.\.\.', note))
        duration_change = pause_count * 0.3

        results.append({
            'index': i,
            'original': note,
            'paced': paced,
            'changes': changes,
            'estimated_duration_change': duration_change,
        })

    return results


def _apply_pacing_rules(text: str) -> tuple:
    """Apply pacing rules to text. Returns (paced_text, list_of_changes)."""
    changes = []
    result = text

    # Rule 1: Add pause after transition phrases
    transitions = [
        "Now,", "Next,", "However,", "Furthermore,", "Additionally,",
        "In contrast,", "For example,", "Let's look at", "Moving on",
        "As we can see,", "Importantly,", "In summary,", "To conclude,",
        "First,", "Second,", "Third,", "Finally,",
    ]
    for phrase in transitions:
        if phrase in result:
            # Add a brief pause after the transition (represented by ellipsis)
            result = result.replace(phrase, f"{phrase} ...")
            changes.append(f"Added pause after '{phrase}'")

    # Rule 2: Add pause before key statistics or numbers
    # Pattern: pause before "X%" or "$X" or "X million"
    num_pattern = r'(\s)(\d+[\.,]?\d*\s*(?:%|percent|million|billion|thousand|dollars|EUR|GBP))'
    matches = list(re.finditer(num_pattern, result, re.IGNORECASE))
    if matches:
        for m in reversed(matches):  # Reverse to preserve positions
            result = result[:m.start(1)] + " ... " + result[m.start(2):]
        changes.append(f"Added emphasis pause before {len(matches)} key statistic(s)")

    # Rule 3: Add pause between major sections (double newline or bullet lists)
    if '\n\n' in result:
        result = result.replace('\n\n', '\n\n... ')
        changes.append("Added pause between sections")

    # Rule 4: Slow down for questions (add pause before and after)
    question_pattern = r'([^.!?]*\?)'
    questions = re.findall(question_pattern, result)
    if questions:
        for q in questions:
            result = result.replace(q, f"... {q} ...")
        changes.append(f"Added pause around {len(questions)} question(s)")

    # Rule 5: Add slight pause after colons (introducing a list or explanation)
    result = re.sub(r':\s', ': ... ', result, count=3)
    colon_count = min(3, result.count(': ...') - text.count(': ...'))
    if colon_count > 0:
        changes.append(f"Added pause after {colon_count} colon introduction(s)")

    # Clean up multiple consecutive pauses
    result = re.sub(r'(\.\.\.\s*){2,}', '... ', result)
    result = re.sub(r'\s{2,}', ' ', result)

    return result.strip(), changes


def apply_pacing_to_notes(notes: List[str], on_progress: Optional[Callable] = None) -> List[str]:
    """Apply pacing rules to all notes and return the paced versions."""
    results = analyze_pacing(notes)

    paced = []
    for i, r in enumerate(results):
        if on_progress:
            on_progress(i / len(results), f"Pacing slide {i+1}/{len(results)}")
        paced.append(r['paced'])

    if on_progress:
        total_changes = sum(len(r['changes']) for r in results)
        on_progress(1.0, f"Applied {total_changes} pacing adjustments")

    return paced


def ai_pacing(notes: List[str], ollama_url: str, ollama_model: str,
              on_progress: Optional[Callable] = None) -> List[str]:
    """Use AI to optimize pacing with natural pause placement."""
    import requests

    paced = []
    for i, note in enumerate(notes):
        if on_progress:
            on_progress(i / len(notes), f"AI pacing slide {i+1}/{len(notes)}")

        if not note.strip():
            paced.append(note)
            continue

        prompt = (
            "Add natural pauses (represented by '...') to this narration text. "
            "Place pauses: after introductory phrases, before key points, "
            "between topic transitions, and around questions. "
            "Don't change any words, only add '...' where pauses should go. "
            "Return only the text with pauses added.\n\n"
            f"{note}"
        )

        try:
            resp = requests.post(
                f"{ollama_url.rstrip('/')}/api/generate",
                json={"model": ollama_model, "prompt": prompt, "stream": False},
                timeout=20,
            )
            if resp.status_code == 200:
                result = resp.json().get("response", "").strip()
                if result and len(result) >= len(note) * 0.8:
                    paced.append(result)
                    continue
        except Exception:
            pass

        # Fallback to rule-based
        paced_text, _ = _apply_pacing_rules(note)
        paced.append(paced_text)

    if on_progress:
        on_progress(1.0, "AI pacing complete")

    return paced
