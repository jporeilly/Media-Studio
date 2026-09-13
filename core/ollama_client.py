"""Ollama local LLM client for AI-powered speaker notes enhancement."""

import base64
import json
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Generator


@dataclass
class OllamaModel:
    """An available Ollama model."""
    name: str
    size: int = 0  # bytes


DEFAULT_URL = "http://localhost:11434"

DEFAULT_SYSTEM_PROMPT = (
    "You are an expert presentation coach. "
    "Rewrite the speaker notes below so they are clear, engaging, and natural "
    "when read aloud as video narration. "
    "Keep the same meaning and approximate length. "
    "Return ONLY the improved text — no headings, no bullet points, no commentary."
)


def check_connection(base_url: str = DEFAULT_URL, timeout: float = 3.0) -> bool:
    """Return True if Ollama is reachable."""
    try:
        req = urllib.request.Request(f"{base_url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        return False


def list_models(base_url: str = DEFAULT_URL, timeout: float = 5.0) -> List[OllamaModel]:
    """Fetch available models from the Ollama server."""
    try:
        req = urllib.request.Request(f"{base_url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            return [
                OllamaModel(name=m["name"], size=m.get("size", 0))
                for m in data.get("models", [])
            ]
    except Exception:
        return []


def pull_model(
    model: str,
    base_url: str = DEFAULT_URL,
    timeout: float = 600.0,
) -> Generator[dict, None, None]:
    """Pull (download) a model from the Ollama registry. Yields progress dicts.

    Each dict has: {"status": "...", "total": int, "completed": int}
    """
    payload = json.dumps({"name": model, "stream": True}).encode()
    req = urllib.request.Request(
        f"{base_url}/api/pull",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for line in resp:
            if line.strip():
                chunk = json.loads(line.decode())
                yield chunk
                if chunk.get("status") == "success":
                    break


def get_model_info(model: str, base_url: str = DEFAULT_URL, timeout: float = 5.0) -> dict:
    """Get model details from Ollama /api/show. Returns dict with context_length, parameter_size, etc."""
    try:
        payload = json.dumps({"name": model}).encode()
        req = urllib.request.Request(
            f"{base_url}/api/show",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            details = data.get("details", {})
            model_info = data.get("model_info", {})
            context_length = 0
            for key, val in model_info.items():
                if key.endswith(".context_length") and isinstance(val, int):
                    context_length = val
                    break
            return {
                "context_length": context_length,
                "parameter_size": details.get("parameter_size", ""),
                "quantization": details.get("quantization_level", ""),
                "family": details.get("family", ""),
                "format": details.get("format", ""),
            }
    except Exception:
        return {}


def get_gpu_info() -> list:
    """Detect GPU(s) via nvidia-smi. Returns list of dicts with name, total_mb, free_mb."""
    import subprocess, os
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=creationflags,
        )
        gpus = []
        for line in result.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                gpus.append({
                    "name": parts[0],
                    "total_mb": int(parts[1]),
                    "free_mb": int(parts[2]),
                })
        return gpus
    except Exception:
        return []


def recommend_num_ctx(model: str, base_url: str = DEFAULT_URL) -> dict:
    """Auto-recommend num_ctx based on model context limit and available GPU memory.

    Returns dict with recommended, max_model, gpu_free_mb, reasoning.
    """
    info = get_model_info(model, base_url)
    gpus = get_gpu_info()

    max_model = info.get("context_length", 0)
    param_size = info.get("parameter_size", "")
    total_gpu_free_mb = sum(g["free_mb"] for g in gpus) if gpus else 0
    total_gpu_mb = sum(g["total_mb"] for g in gpus) if gpus else 0
    gpu_names = ", ".join(g["name"] for g in gpus) if gpus else "No GPU detected"

    if total_gpu_free_mb > 0:
        import re
        param_billions = 0
        if param_size:
            m = re.search(r'([\d.]+)', param_size)
            if m:
                param_billions = float(m.group(1))

        kb_per_token = 0.5 if param_billions < 10 else 1.0 if param_billions < 30 else 1.5
        available_for_ctx_mb = total_gpu_free_mb * 0.4
        max_from_gpu = int((available_for_ctx_mb * 1024) / kb_per_token)
        max_from_gpu = (max_from_gpu // 1024) * 1024
        max_from_gpu = max(2048, min(max_from_gpu, max_model or 131072))
    else:
        max_from_gpu = 8192

    recommended = min(max_from_gpu, max_model) if max_model else max_from_gpu
    recommended = max(4096, min(recommended, 65536))

    reasoning = f"GPU: {gpu_names} ({total_gpu_free_mb:,} MB free of {total_gpu_mb:,} MB)"
    if max_model:
        reasoning += f" | Model max: {max_model:,} tokens"
    reasoning += f" | Recommended: {recommended:,} tokens"

    return {
        "recommended": recommended,
        "max_model": max_model,
        "gpu_free_mb": total_gpu_free_mb,
        "gpu_total_mb": total_gpu_mb,
        "gpu_names": gpu_names,
        "param_size": param_size,
        "reasoning": reasoning,
    }


# ── Model recommendation catalog ──────────────────────────

MODEL_CATALOG = [
    # All-Rounder
    {"name": "gemma3:27b", "category": "All-Rounder", "vram_gb": 18, "params": "27B", "quality": 5, "speed": 3, "notes": "Excellent all-round, vision capable"},
    {"name": "gemma3:12b", "category": "All-Rounder", "vram_gb": 8, "params": "12B", "quality": 4, "speed": 4, "notes": "Great balance of quality and speed"},
    {"name": "gemma3:4b", "category": "All-Rounder", "vram_gb": 3, "params": "4B", "quality": 3, "speed": 5, "notes": "Fast, good for low-end hardware"},
    {"name": "llama3.1:70b", "category": "All-Rounder", "vram_gb": 40, "params": "70B", "quality": 5, "speed": 1, "notes": "Top quality, needs high-end GPU"},
    {"name": "llama3.1:8b", "category": "All-Rounder", "vram_gb": 5, "params": "8B", "quality": 4, "speed": 4, "notes": "Solid general-purpose model"},
    {"name": "llama3.2:3b", "category": "All-Rounder", "vram_gb": 2, "params": "3B", "quality": 3, "speed": 5, "notes": "Lightweight, very fast"},
    {"name": "mistral", "category": "All-Rounder", "vram_gb": 5, "params": "7B", "quality": 4, "speed": 4, "notes": "Fast and efficient general model"},
    {"name": "qwen3:32b", "category": "All-Rounder", "vram_gb": 20, "params": "32B", "quality": 5, "speed": 3, "notes": "Strong reasoning and multilingual"},
    {"name": "qwen3:8b", "category": "All-Rounder", "vram_gb": 5, "params": "8B", "quality": 4, "speed": 4, "notes": "Good quality, efficient"},
    {"name": "phi4:14b", "category": "All-Rounder", "vram_gb": 9, "params": "14B", "quality": 4, "speed": 4, "notes": "Microsoft, strong reasoning"},

    # Vision (multimodal) — important for slide image analysis
    {"name": "gemma3:27b", "category": "Vision", "vram_gb": 18, "params": "27B", "quality": 5, "speed": 3, "notes": "Image understanding + text"},
    {"name": "gemma3:12b", "category": "Vision", "vram_gb": 8, "params": "12B", "quality": 4, "speed": 4, "notes": "Vision capable, good balance"},
    {"name": "gemma3:4b", "category": "Vision", "vram_gb": 3, "params": "4B", "quality": 3, "speed": 5, "notes": "Lightweight vision model"},
    {"name": "llava:13b", "category": "Vision", "vram_gb": 8, "params": "13B", "quality": 4, "speed": 3, "notes": "Image captioning and Q&A"},
    {"name": "llava:7b", "category": "Vision", "vram_gb": 5, "params": "7B", "quality": 3, "speed": 4, "notes": "Fast image understanding"},
    {"name": "moondream:1.8b", "category": "Vision", "vram_gb": 2, "params": "1.8B", "quality": 3, "speed": 5, "notes": "Tiny vision model, very fast"},

    # Narration / Presentation (best for this app)
    {"name": "gemma3:27b", "category": "Narration", "vram_gb": 18, "params": "27B", "quality": 5, "speed": 3, "notes": "Best for speaker notes + slide analysis"},
    {"name": "qwen3:32b", "category": "Narration", "vram_gb": 20, "params": "32B", "quality": 5, "speed": 3, "notes": "Strong structured output, multilingual"},
    {"name": "llama3.1:8b", "category": "Narration", "vram_gb": 5, "params": "8B", "quality": 4, "speed": 4, "notes": "Good quality at lower VRAM"},
    {"name": "phi4:14b", "category": "Narration", "vram_gb": 9, "params": "14B", "quality": 4, "speed": 4, "notes": "Clear, structured writing style"},
    {"name": "mistral", "category": "Narration", "vram_gb": 5, "params": "7B", "quality": 4, "speed": 4, "notes": "Fast, decent narration quality"},

    # Chat / Conversational
    {"name": "llama3.1:8b", "category": "Chat", "vram_gb": 5, "params": "8B", "quality": 4, "speed": 4, "notes": "Natural conversational style"},
    {"name": "gemma3:12b", "category": "Chat", "vram_gb": 8, "params": "12B", "quality": 4, "speed": 4, "notes": "Excellent chat with vision"},
    {"name": "command-r:35b", "category": "Chat", "vram_gb": 22, "params": "35B", "quality": 5, "speed": 3, "notes": "Cohere, built for RAG and chat"},
]


def recommend_models(gpu_total_mb: int = 0) -> dict:
    """Recommend models by category based on available GPU VRAM.

    Returns dict of {category: [models]} where models fit in the GPU.
    Each model dict includes a 'fits' bool and 'fit_label' string.
    """
    if gpu_total_mb <= 0:
        gpus = get_gpu_info()
        gpu_total_mb = sum(g["total_mb"] for g in gpus) if gpus else 0

    gpu_total_gb = gpu_total_mb / 1024 if gpu_total_mb else 0

    categories = {}
    seen = set()
    for m in MODEL_CATALOG:
        key = (m["name"], m["category"])
        if key in seen:
            continue
        seen.add(key)
        cat = m["category"]
        fits = m["vram_gb"] <= gpu_total_gb if gpu_total_gb > 0 else m["vram_gb"] <= 8
        tight = m["vram_gb"] > gpu_total_gb * 0.7 if gpu_total_gb > 0 else False
        if fits and not tight:
            fit_label = "Fits well"
            fit_color = "positive"
        elif fits:
            fit_label = "Tight fit"
            fit_color = "warning"
        else:
            fit_label = "Too large"
            fit_color = "negative"

        entry = {**m, "fits": fits, "fit_label": fit_label, "fit_color": fit_color}
        categories.setdefault(cat, []).append(entry)

    for cat in categories:
        categories[cat].sort(key=lambda x: (not x["fits"], -x["quality"], -x["speed"]))

    return categories


def _encode_image(image_path: str) -> Optional[str]:
    """Read an image file and return its base64 encoding, or None on failure."""
    try:
        p = Path(image_path)
        if p.exists():
            return base64.b64encode(p.read_bytes()).decode("ascii")
    except Exception:
        pass
    return None


def generate(
    prompt: str,
    model: str,
    system: str = DEFAULT_SYSTEM_PROMPT,
    base_url: str = DEFAULT_URL,
    timeout: float = 120.0,
    images: Optional[List[str]] = None,
) -> str:
    """Generate a completion (non-streaming) and return the full response text.

    Args:
        images: Optional list of file paths to images. The images are base64-encoded
                and sent to vision-capable models (e.g. llava, llama3.2-vision).
                Non-vision models will ignore them gracefully.
    """
    body = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": False,
    }
    if images:
        encoded = [b64 for path in images if (b64 := _encode_image(path))]
        if encoded:
            body["images"] = encoded
    payload = json.dumps(body).encode()

    req = urllib.request.Request(
        f"{base_url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
        return data.get("response", "").strip()


def generate_stream(
    prompt: str,
    model: str,
    system: str = DEFAULT_SYSTEM_PROMPT,
    base_url: str = DEFAULT_URL,
    timeout: float = 120.0,
) -> Generator[str, None, None]:
    """Generate a completion with streaming — yields text chunks."""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": True,
    }).encode()

    req = urllib.request.Request(
        f"{base_url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for line in resp:
            if line.strip():
                chunk = json.loads(line.decode())
                text = chunk.get("response", "")
                if text:
                    yield text
                if chunk.get("done"):
                    break


def chat(
    messages: list,
    model: str,
    system: str = DEFAULT_SYSTEM_PROMPT,
    base_url: str = DEFAULT_URL,
    timeout: float = 120.0,
) -> str:
    """Send a multi-turn chat and return the assistant's response.

    Args:
        messages: List of {"role": "user"|"assistant", "content": "..."} dicts.
        model: Ollama model name.
        system: System prompt.
    """
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}] + messages,
        "stream": False,
    }).encode()

    req = urllib.request.Request(
        f"{base_url}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
        return data.get("message", {}).get("content", "").strip()


def chat_stream(
    messages: list,
    model: str,
    system: str = DEFAULT_SYSTEM_PROMPT,
    base_url: str = DEFAULT_URL,
    timeout: float = 120.0,
) -> Generator[str, None, None]:
    """Send a multi-turn chat with streaming — yields text chunks."""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}] + messages,
        "stream": True,
    }).encode()

    req = urllib.request.Request(
        f"{base_url}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for line in resp:
            if line.strip():
                chunk = json.loads(line.decode())
                text = chunk.get("message", {}).get("content", "")
                if text:
                    yield text
                if chunk.get("done"):
                    break
