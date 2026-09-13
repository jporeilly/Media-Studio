"""MCP (Model Context Protocol) client for GitBook documentation servers.

Connects to GitBook-hosted MCP endpoints (Streamable HTTP / SSE transport)
to search documentation and retrieve context for AI-enhanced speaker notes.
"""

import json
import re
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import List

from utils.logger import get_logger

logger = get_logger("MCP")


PENTAHO_LATEST_VERSION = "11.0"


def extract_pentaho_version(url: str) -> str:
    """Extract Pentaho product version from a docs.pentaho.com URL.

    URL patterns found in GitBook MCP responses:
        .../install/10.2-install/...                        → "10.2"
        .../pdia-data-integration/10.2-data-integration/... → "10.2"
        .../pdc-10.2-install                                → "10.2"
        .../r/en/10.2/...                                   → "10.2"
        .../release-notes-11.0                              → "11.0"
        .../pdc-get-started                                 → "" (no version, assumed latest)

    Returns version string or empty string.
    """
    if "docs.pentaho.com" not in url:
        return ""
    # Match version patterns: X.Y or X.Y.Z preceded by / or -
    # Followed by -, /, or end-of-path (for URLs like release-notes-11.0)
    m = re.search(r"[/-]((\d+\.\d+(?:\.\d+)?)(?:[-/]|$))", url)
    if m:
        return m.group(2)
    return ""


def format_pentaho_version(version: str) -> str:
    """Format version for display in citations.

    Returns e.g. "(Pentaho 10.2)" or "(Pentaho 11.0 - Latest)" or "".
    """
    if not version:
        return f"(Pentaho {PENTAHO_LATEST_VERSION} - Latest)"
    if version == PENTAHO_LATEST_VERSION:
        return f"(Pentaho {version} - Latest)"
    return f"(Pentaho {version})"


@dataclass
class MCPTool:
    """A tool exposed by an MCP server."""
    name: str
    description: str = ""
    input_schema: dict = field(default_factory=dict)


@dataclass
class SearchResult:
    """A single search result from the MCP documentation search."""
    title: str
    link: str
    content: str
    version: str = ""  # Pentaho product version extracted from URL


def _parse_sse_response(body: str) -> dict:
    """Parse an SSE response and extract the JSON-RPC data."""
    for line in body.split("\n"):
        if line.startswith("data: "):
            return json.loads(line[6:])
    # Fallback: try parsing whole body as JSON
    return json.loads(body)


def _post_jsonrpc(url: str, method: str, params: dict, msg_id: int = 1,
                  timeout: float = 15.0) -> dict:
    """Send a JSON-RPC message to the MCP endpoint and return parsed result."""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": method,
        "params": params,
    }).encode()

    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }, method="POST")

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode()
        return _parse_sse_response(body)


def check_connection(mcp_url: str, timeout: float = 5.0) -> bool:
    """Check if the MCP server is reachable by sending an initialize message."""
    try:
        result = _post_jsonrpc(mcp_url, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pptx-to-video", "version": "1.0"},
        }, timeout=timeout)
        return "result" in result
    except Exception:
        return False


def list_tools(mcp_url: str, timeout: float = 10.0) -> List[MCPTool]:
    """Discover available tools from the MCP server."""
    try:
        data = _post_jsonrpc(mcp_url, "tools/list", {}, msg_id=2, timeout=timeout)
        tools_data = data.get("result", {}).get("tools", [])
        return [
            MCPTool(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
            )
            for t in tools_data
        ]
    except Exception:
        return []


def search_documentation(
    query: str,
    mcp_url: str,
    timeout: float = 20.0,
) -> List[SearchResult]:
    """Search documentation via the MCP server's searchDocumentation tool.

    Args:
        query: Search query string.
        mcp_url: The MCP endpoint URL.
        timeout: Request timeout in seconds.

    Returns:
        List of SearchResult with title, link, and content.
    """
    try:
        data = _post_jsonrpc(mcp_url, "tools/call", {
            "name": "searchDocumentation",
            "arguments": {"query": query},
        }, msg_id=3, timeout=timeout)

        results = []
        content_list = data.get("result", {}).get("content", [])
        for item in content_list:
            text = item.get("text", "")
            if not text.strip():
                continue
            # Parse the structured text format returned by GitBook MCP
            title = ""
            link = ""
            content = ""
            for line in text.split("\n"):
                if line.startswith("Title: "):
                    title = line[7:].strip()
                elif line.startswith("Link: "):
                    link = line[6:].strip()
                elif line.startswith("Content: "):
                    content = line[9:].strip()
                elif content and line.strip():
                    content += " " + line.strip()

            if title or content:
                version = extract_pentaho_version(link)
                results.append(SearchResult(
                    title=title, link=link, content=content, version=version,
                ))
        return results
    except Exception as e:
        logger.error("Search error: %s", e)
        return []


def search_and_format(
    query: str,
    mcp_url: str,
    max_results: int = 3,
    max_chars: int = 2000,
    timeout: float = 20.0,
) -> str:
    """Search documentation and return formatted context for an LLM prompt.

    Args:
        query: Search query.
        mcp_url: MCP endpoint URL.
        max_results: Maximum number of results to include.
        max_chars: Maximum total characters of context.
        timeout: Request timeout.

    Returns:
        Formatted string of documentation context, or empty string if no results.
    """
    results = search_documentation(query, mcp_url, timeout=timeout)
    if not results:
        return ""

    # Prioritize latest version results
    def _version_sort_key(r: SearchResult):
        if not r.version:
            return (1, 0, 0)
        try:
            p = [int(x) for x in r.version.split(".")]
            return (0, *p)
        except ValueError:
            return (2, 0, 0)
    results.sort(key=_version_sort_key, reverse=True)

    parts = []
    total_chars = 0
    for r in results[:max_results]:
        snippet = r.content[:600] if len(r.content) > 600 else r.content
        ver_tag = f" {format_pentaho_version(r.version)}" if "docs.pentaho.com" in r.link else ""
        entry = f"[{r.title}]({r.link}){ver_tag}\n{snippet}"
        if total_chars + len(entry) > max_chars:
            break
        parts.append(entry)
        total_chars += len(entry)

    if not parts:
        return ""

    return "--- Relevant Documentation ---\n" + "\n\n".join(parts) + "\n--- End Documentation ---"


def search_multiple_servers(
    query: str,
    servers: list,
    max_results: int = 4,
    max_chars: int = 3000,
    timeout: float = 20.0,
) -> str:
    """Search across multiple MCP servers and return combined formatted context.

    Args:
        query: Search query.
        servers: List of {"name": "...", "url": "..."} server dicts.
        max_results: Max results total across all servers.
        max_chars: Max total characters.
        timeout: Per-server timeout.

    Returns:
        Formatted documentation context string, or empty string.
    """
    # Collect results per server
    per_server: list[tuple[str, List[SearchResult]]] = []
    for server in servers:
        url = server.get("url", "")
        name = server.get("name", url)
        if not url:
            continue
        try:
            results = search_documentation(query, url, timeout=timeout)
            for r in results:
                r.title = f"{r.title} [{name}]"
            per_server.append((name, results))
            logger.info("%s: %d result(s)", name, len(results))
        except Exception as e:
            logger.error("Error searching %s: %s", name, e)

    if not per_server:
        return ""

    # Interleave results round-robin across servers for balanced coverage
    all_results: List[SearchResult] = []
    max_per_server = max(len(r) for _, r in per_server) if per_server else 0
    for i in range(max_per_server):
        for _name, results in per_server:
            if i < len(results):
                all_results.append(results[i])

    if not all_results:
        return ""

    # Prioritize latest version results — unversioned (assumed latest) and highest versions first
    def _version_sort_key(r: SearchResult):
        if not r.version:
            return (1, 0, 0)
        try:
            p = [int(x) for x in r.version.split(".")]
            return (0, *p)
        except ValueError:
            return (2, 0, 0)
    all_results.sort(key=_version_sort_key, reverse=True)

    parts = []
    total_chars = 0
    for r in all_results[:max_results]:
        snippet = r.content[:600] if len(r.content) > 600 else r.content
        ver_tag = f" {format_pentaho_version(r.version)}" if "docs.pentaho.com" in r.link else ""
        entry = f"[{r.title}]({r.link}){ver_tag}\n{snippet}"
        if total_chars + len(entry) > max_chars:
            break
        parts.append(entry)
        total_chars += len(entry)

    if not parts:
        return ""

    return "--- Relevant Documentation ---\n" + "\n\n".join(parts) + "\n--- End Documentation ---"
