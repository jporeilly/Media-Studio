"""AI Workflow Assistant — context-aware guide powered by local LLM (Ollama).

Analyzes the current project state and provides:
- Contextual suggestions based on what's done and what's next
- Natural language command interpretation
- Quick action chips for common workflows
- Step-by-step guided workflows
"""

from typing import List, Dict
from pathlib import Path
import logging
import json
from core.ollama_client import request_body

logger = logging.getLogger("mediastudio.ASSISTANT")


# Quick action definitions — displayed as clickable chips
QUICK_ACTIONS = [
    {"id": "generate_notes", "label": "Generate speaker notes", "icon": "auto_fix_high",
     "description": "AI writes narration text for slides that have no notes",
     "requires": ["file_loaded"], "stage": "notes"},
    {"id": "enhance_notes", "label": "Enhance notes for narration", "icon": "auto_awesome",
     "description": "Rewrites notes to sound natural when spoken aloud",
     "requires": ["has_notes"], "stage": "notes"},
    {"id": "qa_review", "label": "QA review notes", "icon": "fact_check",
     "description": "Check notes for quality issues and auto-fix",
     "requires": ["has_notes"], "stage": "review"},
    {"id": "change_tone", "label": "Change tone / audience", "icon": "tune",
     "description": "Adapt notes for technical, executive, student, or casual audiences",
     "requires": ["has_notes"], "stage": "notes"},
    {"id": "add_pacing", "label": "Add natural pacing", "icon": "speed",
     "description": "Insert pauses at transitions and key points",
     "requires": ["has_notes"], "stage": "notes"},
    {"id": "generate_audio", "label": "Generate audio", "icon": "mic",
     "description": "Create TTS audio from speaker notes",
     "requires": ["has_notes"], "stage": "audio"},
    {"id": "generate_video", "label": "Generate video", "icon": "movie",
     "description": "Create the final MP4 video with narration",
     "requires": ["has_notes"], "stage": "video"},
    {"id": "one_click", "label": "Run Agent", "icon": "smart_toy",
     "description": "AI agent analyzes your presentation and runs the best workflow — review steps before executing",
     "requires": ["file_loaded"], "stage": "all"},
    {"id": "add_subtitles", "label": "Generate subtitles", "icon": "subtitles",
     "description": "Create SRT/VTT subtitle files from the video audio",
     "requires": ["has_video"], "stage": "export"},
    {"id": "youtube_metadata", "label": "YouTube chapters & description", "icon": "description",
     "description": "Generate YouTube-ready metadata with chapters and tags",
     "requires": ["has_audio"], "stage": "export"},
    {"id": "translate", "label": "Translate to another language", "icon": "translate",
     "description": "Translate all speaker notes into another language with your local Ollama model",
     "requires": ["has_notes"], "stage": "notes"},
    {"id": "analyze_slides", "label": "Analyze slide quality", "icon": "analytics",
     "description": "AI reviews slides for text density, missing visuals, and improvements",
     "requires": ["file_loaded"], "stage": "review"},
    {"id": "generate_qa", "label": "Generate Q&A document", "icon": "quiz",
     "description": "Create anticipated questions and answers from your content",
     "requires": ["has_notes"], "stage": "export"},
    {"id": "split_slides", "label": "Split dense slides", "icon": "call_split",
     "description": "Find slides with too much content and split them",
     "requires": ["file_loaded"], "stage": "review"},
    {"id": "import_video", "label": "Import & re-voice a video", "icon": "video_file",
     "description": "Transcribe an existing video and generate with a new voice",
     "requires": [], "stage": "import"},
    {"id": "batch_generate", "label": "Batch generate from CSV", "icon": "dynamic_feed",
     "description": "Create personalised videos from a template with variable substitution",
     "requires": ["file_loaded"], "stage": "batch"},
    # Internal actions — no chip, handled by AI assistant chat
    {"id": "save_task_list", "label": "Save task list", "icon": "save",
     "description": "Save the current task checklist for later", "requires": [], "stage": "all"},
    {"id": "load_task_list", "label": "Load task list", "icon": "folder_open",
     "description": "Load a previously saved task list", "requires": [], "stage": "all"},
    {"id": "clear_task_list", "label": "Clear task list", "icon": "clear_all",
     "description": "Clear all tasks from the checklist", "requires": [], "stage": "all"},
    {"id": "run_tasks", "label": "Run tasks", "icon": "rocket_launch",
     "description": "Execute all tasks in the checklist", "requires": [], "stage": "all"},
]

# Workflow stages in order
WORKFLOW_STAGES = ["import", "notes", "review", "audio", "video", "export", "batch", "all"]


def analyze_project_state(state) -> Dict:
    """Analyze the current project state and return a status summary.

    Args:
        state: AppState object

    Returns dict with:
        'file_loaded': bool,
        'file_name': str,
        'slide_count': int,
        'has_notes': bool,
        'notes_coverage': float (0-1),
        'has_audio': bool,
        'has_video': bool,
        'current_stage': str,
        'next_actions': list of action IDs,
    """
    result = {
        'file_loaded': False,
        'file_name': '',
        'slide_count': 0,
        'has_notes': False,
        'notes_coverage': 0.0,
        'has_audio': False,
        'has_video': False,
        'current_stage': 'import',
        'next_actions': [],
    }

    if not state.files or state.selected_file_index < 0:
        result['next_actions'] = [
            'import_video', 'one_click', 'batch_generate',
            'generate_notes', 'generate_video',
        ]
        return result

    fi = state.files[state.selected_file_index] if state.selected_file_index < len(state.files) else None
    if not fi:
        return result

    result['file_loaded'] = True
    result['file_name'] = fi.path.name

    # Check notes
    pm = fi.project_manager
    if pm and pm.state and pm.state.slides:
        slides = pm.state.slides
        result['slide_count'] = len(slides)
        notes_count = sum(1 for s in slides if s.speaker_notes and s.speaker_notes.strip())
        result['notes_coverage'] = notes_count / len(slides) if slides else 0
        result['has_notes'] = notes_count > 0

        # Check audio
        audio_count = sum(1 for s in slides if s.audio_path and Path(s.audio_path).exists())
        result['has_audio'] = audio_count > 0

        # Check video
        if pm.state.output_video_path and Path(pm.state.output_video_path).exists():
            result['has_video'] = True
    elif fi.reader:
        result['slide_count'] = fi.reader.slide_count
        notes_count = sum(1 for i in range(fi.reader.slide_count)
                         if fi.reader.get_slide_notes(i))
        result['notes_coverage'] = notes_count / fi.reader.slide_count if fi.reader.slide_count > 0 else 0
        result['has_notes'] = notes_count > 0

    # Determine current stage and next actions
    if not result['has_notes']:
        result['current_stage'] = 'notes'
        result['next_actions'] = ['generate_notes', 'one_click']
    elif not result['has_audio']:
        result['current_stage'] = 'audio'
        result['next_actions'] = ['enhance_notes', 'change_tone', 'add_pacing', 'generate_audio', 'one_click']
    elif not result['has_video']:
        result['current_stage'] = 'video'
        result['next_actions'] = ['generate_video']
    else:
        result['current_stage'] = 'export'
        result['next_actions'] = ['add_subtitles', 'youtube_metadata', 'generate_qa', 'translate']

    return result


def get_greeting(project_state: Dict, username: str = "") -> str:
    """Generate a contextual greeting based on project state."""
    name_part = f"Hi **{username}**! " if username else ""

    if not project_state['file_loaded']:
        return (
            f"{name_part}\n\n"
            "Welcome to **SlideStudio-Enterprise**!\n\n"
            "Load a PowerPoint file to get started, or import an existing video to re-voice it.\n\n"
            "Here are some things I can help with:"
        )

    name = project_state['file_name']
    slides = project_state['slide_count']
    coverage = project_state['notes_coverage']

    if not project_state['has_notes']:
        return (
            f"{name_part}\n\n"
            f"I've loaded **{name}** ({slides} slides).\n\n"
            "None have speaker notes yet. I can generate narration text for all slides, "
            "or you can use **One-Click Video** to do everything automatically."
        )
    elif coverage < 1.0:
        pct = int(coverage * 100)
        return (
            f"{name_part}\n\n"
            f"**{name}** has {slides} slides ({pct}% have notes).\n\n"
            "I can fill in the missing notes, enhance them for narration, "
            "or adjust the tone for your target audience."
        )
    elif not project_state['has_audio']:
        return (
            f"{name_part}\n\n"
            f"**{name}** is ready! All {slides} slides have speaker notes.\n\n"
            "I can generate the voice narration, or you can review/enhance the notes first."
        )
    elif not project_state['has_video']:
        return (
            f"{name_part}\n\n"
            f"Audio is ready for **{name}**.\n\n"
            "Let's create the video!"
        )
    else:
        return (
            f"{name_part}\n\n"
            f"Video complete for **{name}**!\n\n"
            "You can now generate subtitles, create YouTube metadata, "
            "translate to other languages, or generate a Q&A document."
        )


def get_suggested_actions(project_state: Dict) -> List[Dict]:
    """Get the most relevant quick actions for the current state."""
    actions = []
    state_flags = set()

    if project_state['file_loaded']:
        state_flags.add('file_loaded')
    if project_state['has_notes']:
        state_flags.add('has_notes')
    if project_state['has_audio']:
        state_flags.add('has_audio')
    if project_state['has_video']:
        state_flags.add('has_video')

    # When no file loaded, show key actions regardless of requirements
    # (clicking them will prompt the user to load a file first)
    if not project_state['file_loaded']:
        showcase_ids = [
            'import_video', 'one_click', 'generate_notes', 'generate_video',
            'translate', 'add_subtitles', 'analyze_slides', 'batch_generate',
        ]
        for aid in showcase_ids:
            action = next((a for a in QUICK_ACTIONS if a['id'] == aid), None)
            if action:
                actions.append(action)
        return actions

    # Prioritize next_actions, then add others that are available
    priority_ids = project_state.get('next_actions', [])

    for action_id in priority_ids:
        action = next((a for a in QUICK_ACTIONS if a['id'] == action_id), None)
        if action:
            required = set(action.get('requires', []))
            if required.issubset(state_flags):
                actions.append(action)

    # Add remaining available actions (not already added)
    added_ids = {a['id'] for a in actions}
    for action in QUICK_ACTIONS:
        if action['id'] not in added_ids:
            required = set(action.get('requires', []))
            if required.issubset(state_flags):
                actions.append(action)

    return actions


def interpret_command(user_input: str, project_state: Dict,
                      ollama_url: str = "", ollama_model: str = "") -> Dict:
    """Interpret a natural language command and map to an action.

    Args:
        user_input: What the user typed
        project_state: Current project state
        ollama_url: Ollama URL for AI interpretation
        ollama_model: Model to use

    Returns dict:
        'action': str (action ID from QUICK_ACTIONS, or 'chat')
        'params': dict (extracted parameters)
        'response': str (text to show the user)
    """
    text = user_input.lower().strip()

    # Keyword matching runs first — if actionable keywords are found, use them.
    # Questions with no keyword matches fall through to _ai_interpret at the end.
    # Longer phrases checked first (sorted by length descending) to avoid partial matches
    keyword_map = {
        # Notes — generate, enhance, QA
        'generate speaker notes': 'generate_notes',
        'generate notes': 'generate_notes',
        'create notes': 'generate_notes',
        'write notes': 'generate_notes',
        'add notes': 'generate_notes',
        'auto generate': 'generate_notes',
        'narration text': 'generate_notes',
        'enhance notes': 'enhance_notes',
        'improve notes': 'enhance_notes',
        'rewrite notes': 'enhance_notes',
        'polish notes': 'enhance_notes',
        'enhance': 'enhance_notes',
        'qa review': 'qa_review',
        'quality review': 'qa_review',
        'quality check': 'qa_review',
        'check quality': 'qa_review',
        'check notes': 'qa_review',
        'review notes': 'qa_review',
        'proofread': 'qa_review',

        # Tone / audience adaptation
        'change tone': 'change_tone',
        'adapt tone': 'change_tone',
        'make it technical': 'change_tone',
        'make it executive': 'change_tone',
        'make it casual': 'change_tone',
        'make it formal': 'change_tone',
        'for students': 'change_tone',
        'for executives': 'change_tone',
        'for sales': 'change_tone',
        'executive friendly': 'change_tone',
        'technical audience': 'change_tone',
        'tone': 'change_tone',

        # Pacing
        'add pacing': 'add_pacing',
        'add pauses': 'add_pacing',
        'natural pacing': 'add_pacing',
        'improve pacing': 'add_pacing',
        'pacing': 'add_pacing',

        # Video generation
        'generate video': 'generate_video',
        'create video': 'generate_video',
        'make video': 'generate_video',
        'make a video': 'generate_video',
        'build video': 'generate_video',
        'render video': 'generate_video',
        'produce video': 'generate_video',
        'video': 'generate_video',

        # Audio generation
        'generate audio': 'generate_audio',
        'create audio': 'generate_audio',
        'make audio': 'generate_audio',
        'text to speech': 'generate_audio',
        'tts': 'generate_audio',
        'narrate': 'generate_audio',

        # One-click / automated pipeline
        'one click video': 'one_click',
        'one-click video': 'one_click',
        'one click': 'one_click',
        'do everything': 'one_click',
        'full pipeline': 'one_click',
        'automate everything': 'one_click',
        'auto generate video': 'one_click',
        'just do it': 'one_click',
        'smart agent': 'one_click',
        'run agent': 'one_click',
        'agent': 'one_click',

        # Subtitles / captions
        'generate subtitles': 'add_subtitles',
        'create subtitles': 'add_subtitles',
        'add subtitles': 'add_subtitles',
        'subtitle': 'add_subtitles',
        'caption': 'add_subtitles',
        'closed caption': 'add_subtitles',
        'srt file': 'add_subtitles',
        'vtt file': 'add_subtitles',
        'srt': 'add_subtitles',

        # YouTube
        'youtube metadata': 'youtube_metadata',
        'youtube chapters': 'youtube_metadata',
        'youtube description': 'youtube_metadata',
        'video description': 'youtube_metadata',
        'chapter': 'youtube_metadata',
        'youtube': 'youtube_metadata',

        # Translation
        'translate notes': 'translate',
        'translate to': 'translate',
        'translate': 'translate',
        'translation': 'translate',
        'in spanish': 'translate',
        'in french': 'translate',
        'in german': 'translate',
        'in japanese': 'translate',
        'in chinese': 'translate',
        'in portuguese': 'translate',
        'in italian': 'translate',
        'in korean': 'translate',
        'in arabic': 'translate',
        'in russian': 'translate',
        'in hindi': 'translate',
        'spanish': 'translate',
        'french': 'translate',
        'german': 'translate',

        # Slide analysis
        'analyze slides': 'analyze_slides',
        'slide analysis': 'analyze_slides',
        'check slides': 'analyze_slides',
        'review slides': 'analyze_slides',
        'analyze': 'analyze_slides',

        # Split slides
        'split slides': 'split_slides',
        'split dense': 'split_slides',
        'too much text': 'split_slides',
        'split': 'split_slides',

        # Q&A document
        'generate q&a': 'generate_qa',
        'create q&a': 'generate_qa',
        'q&a document': 'generate_qa',
        'anticipated questions': 'generate_qa',
        'faq': 'generate_qa',
        'questions and answers': 'generate_qa',
        'q&a': 'generate_qa',

        # Batch generation
        'batch generate': 'batch_generate',
        'batch video': 'batch_generate',
        'personalised video': 'batch_generate',
        'personalized video': 'batch_generate',
        'csv template': 'batch_generate',
        'variable substitution': 'batch_generate',
        'batch': 'batch_generate',
        'csv': 'batch_generate',
        'mail merge': 'batch_generate',

        # Video import / re-voicing
        'import video': 'import_video',
        'import a video': 'import_video',
        're-voice': 'import_video',
        'revoice': 'import_video',
        'transcribe video': 'import_video',
        'transcribe': 'import_video',
        'new voice': 'import_video',
        'different voice on video': 'import_video',

        # Task Checklist management
        'save task list': 'save_task_list',
        'save tasks': 'save_task_list',
        'save my tasks': 'save_task_list',
        'save the tasks': 'save_task_list',
        'save checklist': 'save_task_list',
        'load task list': 'load_task_list',
        'load tasks': 'load_task_list',
        'open task list': 'load_task_list',
        'load saved tasks': 'load_task_list',
        'load checklist': 'load_task_list',
        'clear task list': 'clear_task_list',
        'clear tasks': 'clear_task_list',
        'clear checklist': 'clear_task_list',
        'run tasks': 'run_tasks',
        'run the tasks': 'run_tasks',
        'run my tasks': 'run_tasks',
        'run all tasks': 'run_tasks',
        'execute tasks': 'run_tasks',
        'start tasks': 'run_tasks',
        'voice track': 'generate_audio',
        'voice narration': 'generate_audio',
        'audio track': 'generate_audio',
        'generate voice': 'generate_audio',
        'regen audio': 'generate_audio',
        'regenerate audio': 'generate_audio',
        'redo audio': 'generate_audio',
        'rebuild audio': 'generate_audio',
        'regen video': 'generate_video',
        'regenerate video': 'generate_video',
        'redo video': 'generate_video',
        'rebuild video': 'generate_video',
    }
    # Sort by key length descending so longer phrases match first
    sorted_keywords = sorted(keyword_map.items(), key=lambda x: len(x[0]), reverse=True)

    # Natural response based on action type
    _action_responses = {
        'generate_notes': "I'll generate speaker notes for your slides now.",
        'enhance_notes': "I'll enhance your speaker notes to sound more natural when spoken aloud.",
        'qa_review': "Let me review your notes for quality and consistency.",
        'change_tone': "I'll adapt your notes for a different audience.",
        'add_pacing': "I'll add natural pauses to improve narration flow.",
        'generate_video': "Starting video generation with your current settings.",
        'generate_audio': "I'll generate the voice narration from your speaker notes.",
        'one_click': "Running the full pipeline — I'll handle notes, audio, video, subtitles, and metadata.",
        'add_subtitles': "I'll transcribe the audio and generate subtitle files.",
        'youtube_metadata': "I'll create YouTube chapters, description, and tags.",
        'translate': "I'll translate your speaker notes to the target language.",
        'analyze_slides': "Let me analyze your slides for quality and suggest improvements.",
        'split_slides': "I'll look for dense slides that should be split.",
        'generate_qa': "I'll generate anticipated questions and answers from your content.",
        'batch_generate': "Let's set up batch video generation from a CSV template.",
        'import_video': "I'll import the video, transcribe the audio, and let you re-voice it.",
        'save_task_list': "Opening the save dialog — give your task list a name and I'll store it for later.",
        'load_task_list': "Opening the saved task lists — pick one to load it into the checklist.",
        'clear_task_list': "Clearing all tasks from the checklist.",
        'run_tasks': "Running all tasks in the checklist now.",
    }

    _META_ACTIONS = {'chat', 'one_click', 'save_task_list', 'load_task_list', 'clear_task_list', 'run_tasks'}

    # Collect all distinct action matches, ordered by position in user input
    matched_actions_with_pos = []
    seen_action_ids = set()
    for keyword, action_id in sorted_keywords:
        pos = text.find(keyword)
        if pos >= 0 and action_id not in seen_action_ids:
            action = next((a for a in QUICK_ACTIONS if a['id'] == action_id), None)
            if action:
                seen_action_ids.add(action_id)
                matched_actions_with_pos.append((pos, action_id))
    matched_actions_with_pos.sort(key=lambda x: x[0])
    matched_actions = [aid for _, aid in matched_actions_with_pos]

    # Separate meta-actions from task actions
    task_actions = [a for a in matched_actions if a not in _META_ACTIONS]
    meta_actions = [a for a in matched_actions if a in _META_ACTIONS]

    # If only meta-action(s) found (no task actions), return the first meta-action as before
    if meta_actions and not task_actions:
        action_id = meta_actions[0]
        action = next((a for a in QUICK_ACTIONS if a['id'] == action_id), None)
        params = _extract_params(text, action_id)
        resp = _action_responses.get(action_id, f"Starting: {action['description']}")
        return {
            'action': action_id,
            'params': params,
            'response': resp,
        }

    # Single task action found — return in original format
    if len(task_actions) == 1:
        action_id = task_actions[0]
        action = next((a for a in QUICK_ACTIONS if a['id'] == action_id), None)
        params = _extract_params(text, action_id)
        resp = _action_responses.get(action_id, f"Starting: {action['description']}")
        return {
            'action': action_id,
            'params': params,
            'response': resp,
        }

    # Multiple task actions found — return multi_task format
    if len(task_actions) > 1:
        labels = []
        for aid in task_actions:
            action_obj = next((a for a in QUICK_ACTIONS if a['id'] == aid), None)
            labels.append(f"**{action_obj['label']}**" if action_obj else f"**{aid}**")
        resp = "I'll add these tasks to your checklist: " + ", ".join(labels) + "."
        return {
            'action': 'multi_task',
            'actions': task_actions,
            'params': {},
            'response': resp,
        }

    # If AI available, use it for interpretation
    if ollama_url and ollama_model:
        return _ai_interpret(user_input, project_state, ollama_url, ollama_model)

    # Fallback
    return {
        'action': 'chat',
        'params': {},
        'response': "I'm not sure what you'd like to do. Try one of the suggested actions below, or ask me something like 'generate notes' or 'make a video'.",
    }


def _extract_params(text: str, action_id: str) -> Dict:
    """Extract parameters from natural language for a specific action."""
    params = {}

    if action_id == 'translate':
        languages = {
            'spanish': 'es', 'french': 'fr', 'german': 'de', 'italian': 'it',
            'portuguese': 'pt', 'japanese': 'ja', 'korean': 'ko', 'chinese': 'zh',
            'arabic': 'ar', 'russian': 'ru', 'hindi': 'hi',
        }
        for lang_name, code in languages.items():
            if lang_name in text:
                params['target_language'] = code
                break

    elif action_id == 'change_tone':
        tones = ['technical', 'executive', 'student', 'sales', 'casual', 'formal']
        for tone in tones:
            if tone in text:
                params['tone'] = tone.capitalize()
                break

    elif action_id in ('generate_video', 'generate_audio'):
        # Check for voice preferences
        if 'female' in text:
            params['voice_gender'] = 'female'
        elif 'male' in text:
            params['voice_gender'] = 'male'

    return params


_docs_cache = {"text": None}


def _load_docs_context() -> str:
    """Load README (full) and HOW_TO_GUIDE (condensed) — cached after first read.

    README is ~20KB and fits comfortably. HOW_TO_GUIDE is ~40KB so we extract
    the table of contents + first 200 chars of each section as a quick reference.
    No vector DB needed — just smart truncation.
    """
    if _docs_cache["text"] is not None:
        return _docs_cache["text"]

    app_dir = Path(__file__).resolve().parent.parent
    docs = ""

    # README — load in full
    readme = app_dir / "README.md"
    if readme.exists():
        try:
            docs += f"\n--- README.md ---\n{readme.read_text(encoding='utf-8')}\n"
        except Exception:
            pass

    # HOW_TO_GUIDE — extract section headers + first 200 chars each
    guide = app_dir / "HOW_TO_GUIDE.md"
    if guide.exists():
        try:
            text = guide.read_text(encoding="utf-8")
            condensed = _condense_guide(text)
            docs += f"\n--- HOW_TO_GUIDE.md (condensed) ---\n{condensed}\n"
        except Exception:
            pass

    _docs_cache["text"] = docs
    return docs


def _condense_guide(text: str) -> str:
    """Extract section headers and first ~200 chars of each section."""
    import re
    lines = text.split("\n")
    sections = []
    current_header = ""
    current_body = []

    for line in lines:
        if re.match(r'^#{1,3}\s', line):
            # Save previous section
            if current_header:
                body = " ".join(current_body).strip()[:200]
                sections.append(f"{current_header}\n{body}")
            current_header = line
            current_body = []
        else:
            if line.strip():
                current_body.append(line.strip())

    # Last section
    if current_header:
        body = " ".join(current_body).strip()[:200]
        sections.append(f"{current_header}\n{body}")

    return "\n\n".join(sections)


# Help prompt — for answering user questions with detailed instructions
_HELP_PROMPT = """You are the AI Assistant for SlideStudio-Enterprise, a video creation application.

Your job is to help users by answering their questions using ONLY the documentation below.
Give detailed, step-by-step instructions when the user asks how to do something.
Use markdown formatting (bold, bullet points, numbered lists) for clarity.
If the documentation doesn't cover a topic, say so honestly.

DOCUMENTATION:
{docs}

RULES:
- Give detailed, helpful answers with step-by-step instructions
- Use the documentation as your ONLY source of truth
- Format responses with markdown for readability
- Include relevant tips or related features when helpful
- Do NOT make up features that aren't in the documentation"""

# Action prompt — for identifying which action to execute
_ACTION_PROMPT = """You are an assistant for SlideStudio-Enterprise. The user wants to perform an action.
Map their request to ONE of these actions and respond with a brief natural message.

Available actions:
{actions}

Respond with JSON only: {{"action": "action_id", "response": "brief natural message"}}
If no action matches, respond: {{"action": "chat", "response": "I'm not sure what action you want. Could you be more specific?"}}"""


def _ai_interpret(user_input: str, project_state: Dict,
                   ollama_url: str, ollama_model: str) -> Dict:
    """Use Ollama to answer questions or identify actions.

    Questions get the full documentation as context for detailed answers.
    Action requests get a focused prompt for quick classification.
    """
    import requests

    text_lower = user_input.lower().strip()
    is_question = any(text_lower.startswith(w) for w in (
        "how", "what", "why", "can", "does", "where", "is ", "help",
        "tell me", "explain", "show me", "what's", "who", "which",
        "do you", "are there", "could", "should", "would",
    ))

    if is_question:
        # Use help prompt with full docs — get detailed instructions
        docs = _load_docs_context()
        system = _HELP_PROMPT.format(docs=docs)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_input},
        ]
    else:
        # Use action prompt — quick classification
        action_list = "\n".join(f"- {a['id']}: {a['description']}" for a in QUICK_ACTIONS)
        system = _ACTION_PROMPT.format(actions=action_list)
        state_summary = json.dumps({k: v for k, v in project_state.items() if k != 'next_actions'})
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"[State: {state_summary}] {user_input}"},
        ]

    try:
        resp = requests.post(
            f"{ollama_url.rstrip('/')}/api/chat",
            json=request_body(ollama_model, messages=messages),
            timeout=60,
        )
        if resp.status_code == 200:
            text = resp.json().get("message", {}).get("content", "")
            if not text:
                text = resp.json().get("response", "")

            if is_question:
                # Return the full answer as-is — no JSON parsing
                return {'action': 'chat', 'params': {}, 'response': text.strip()}

            # For actions, try to parse JSON
            try:
                start = text.find('{')
                end = text.rfind('}') + 1
                if start >= 0 and end > start:
                    parsed = json.loads(text[start:end])
                    return {
                        'action': parsed.get('action', 'chat'),
                        'params': parsed.get('params', {}),
                        'response': parsed.get('response', text),
                    }
            except json.JSONDecodeError:
                pass

            # Fallback — treat as chat
            text = text.strip().strip('`').strip()
            if text.startswith('json'):
                text = text[4:].strip()
            return {'action': 'chat', 'params': {}, 'response': text}

    except requests.exceptions.ConnectionError:
        return {
            'action': 'chat', 'params': {},
            'response': "Cannot connect to Ollama. Make sure it's running (`ollama serve`) and the model is loaded.",
        }
    except requests.exceptions.Timeout:
        return {
            'action': 'chat', 'params': {},
            'response': "Ollama took too long to respond. The model might be loading — try again in a moment.",
        }
    except Exception as e:
        logger.warning("AI interpret failed: %s", e)

    return {
        'action': 'chat', 'params': {},
        'response': "I couldn't process that right now. Try one of the suggested actions below.",
    }
