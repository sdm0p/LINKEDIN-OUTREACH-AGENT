"""LinkedIn search source via stickerdaniel/linkedin-mcp-server.

Per-run stdio transport: `docker run -i --rm -v ~/.linkedin-mcp:/home/pwuser/.linkedin-mcp
stickerdaniel/linkedin-mcp-server` speaks JSON-RPC over stdin/stdout (MCP
newline-delimited framing). The container is spawned lazily per session,
tools are called, and it exits with the process group.

No scraping logic lives here — everything goes through MCP tool calls.
Nothing in this module can send messages; only read-only tools are invoked.
"""

import asyncio
import hashlib
import json
import re
import shutil
from typing import Any

from .base import Post, Recency, SearchError, SearchResult
from .raw_tool import raw_tool

DOCKER_IMAGE = "stickerdaniel/linkedin-mcp-server:latest"
# Session lives in a named volume: Windows bind mounts break the server's
# profile-creation ACL hardening, and a volume sidesteps that entirely.
DOCKER_VOLUME = "linkedin-mcp-session"

_PROTOCOL_VERSION = "2024-11-05"
_INIT_TIMEOUT = 60.0
_CALL_TIMEOUT = 240.0  # container cold-start + headless chromium can be slow

# MCP error text that means the LinkedIn session is dead rather than empty.
_AUTH_FAILURE_MARKERS = (
    "login",
    "log in",
    "sign in",
    "session",
    "auth",
    "checkpoint",
    "captcha",
    "cookie",
)


def _session_mount() -> str:
    return f"{DOCKER_VOLUME}:/home/pwuser/.linkedin-mcp"


def _volume_exists() -> bool:
    try:
        import subprocess

        result = subprocess.run(
            ["docker", "volume", "inspect", DOCKER_VOLUME],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def prerequisites() -> dict[str, bool]:
    """Cheap checks that don't spawn the browser, for Settings display."""
    docker = shutil.which("docker") is not None
    return {
        "docker_installed": docker,
        "session_dir_present": _volume_exists() if docker else False,
    }


class _MCPProcess:
    """One stdio MCP server process. Not context-managed by design: the
    caller controls shutdown so a crash mid-run can still clean up."""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._req_id = 0

    async def start(self) -> None:
        if shutil.which("docker") is None:
            raise SearchError("Docker is not installed or not on PATH.")
        try:
            self._proc = await asyncio.create_subprocess_exec(
                "docker",
                "run",
                "-i",
                "--rm",
                "-v",
                _session_mount(),
                DOCKER_IMAGE,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise SearchError(f"Failed to start the MCP container: {exc}") from exc

    async def _read_message(self, timeout: float) -> dict[str, Any]:
        assert self._proc and self._proc.stdout
        try:
            line = await asyncio.wait_for(self._proc.stdout.readline(), timeout)
        except asyncio.TimeoutError as exc:
            raise SearchError(
                "MCP server did not respond in time (is Docker still starting?)"
            ) from exc
        if not line:
            stderr = await self._drain_stderr()
            raise SearchError(
                f"MCP server closed its output unexpectedly. {stderr}".strip()
            )
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise SearchError(
                f"MCP server sent a non-JSON line: {line[:200]!r}"
            ) from exc

    async def _drain_stderr(self, limit: int = 400) -> str:
        if not self._proc or not self._proc.stderr:
            return ""
        try:
            data = await asyncio.wait_for(self._proc.stderr.read(limit), 2.0)
            return data.decode(errors="replace").strip()
        except (asyncio.TimeoutError, asyncio.LimitOverrunError, OSError):
            return ""

    async def _request(self, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        if not self._proc or not self._proc.stdin:
            raise SearchError("MCP process is not running.")
        self._req_id += 1
        message = {
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": method,
            "params": params,
        }
        self._proc.stdin.write((json.dumps(message) + "\n").encode())
        await self._proc.stdin.drain()

        # Skip server-initiated requests/notifications while waiting for our
        # response (matching id). Everything else is noise.
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise SearchError(f"MCP request '{method}' timed out.")
            msg = await self._read_message(remaining)
            if msg.get("id") == self._req_id and ("result" in msg or "error" in msg):
                if "error" in msg:
                    raise SearchError(f"MCP error from '{method}': {msg['error']}")
                return msg["result"]

    async def initialize(self) -> dict[str, Any]:
        result = await self._request(
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "linkedin-outreach-agent", "version": "0.1.0"},
            },
            _INIT_TIMEOUT,
        )
        # Notification (no response expected).
        if self._proc and self._proc.stdin:
            note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
            self._proc.stdin.write((json.dumps(note) + "\n").encode())
            await self._proc.stdin.drain()
        return result

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "tools/call", {"name": name, "arguments": arguments}, _CALL_TIMEOUT
        )

    async def close(self) -> None:
        if self._proc:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.terminate()
            except ProcessLookupError:
                pass
            self._proc = None


def _classify_failure(detail: str) -> str:
    lowered = detail.lower()
    if any(marker in lowered for marker in _AUTH_FAILURE_MARKERS):
        return "expired"
    return "unavailable"


def _content_text(result: dict[str, Any]) -> str:
    """Extract the text payload from a tools/call result."""
    if result.get("isError"):
        raise SearchError(_error_text(result))
    content = result.get("content", [])
    parts = []
    for item in content if isinstance(content, list) else []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "\n".join(parts)


def _error_text(result: dict[str, Any]) -> str:
    content = result.get("content", [])
    for item in content if isinstance(content, list) else []:
        if isinstance(item, dict) and item.get("type") == "text":
            return str(item.get("text", "tool error"))[:500]
    return "tool error"


_RECENTY_MAP: dict[Recency, str] = {
    "24h": "past-24h",
    "week": "past-week",
    "month": "past-month",
}


_FEED_POST_MARKER = re.compile(r"^Feed post\s*$", re.MULTILINE)
_SKIP_LINES = {"", " \u200b", "\u200b", "Follow", "\u2026 more", "more", "Load more"}
_RELATIVE_AGE = re.compile(r"^\d+\s?(m|h|d|w|mo)\s?\u2022$")


def _person_references(data: dict) -> dict[str, str]:
    """Extract name -> profile-URL mappings from the references section."""
    out: dict[str, str] = {}
    refs = data.get("references")
    if not isinstance(refs, dict):
        return out
    for ref in refs.get("search_results", []) or []:
        if not isinstance(ref, dict) or ref.get("kind") != "person":
            continue
        url = str(ref.get("url") or "")
        name = str(ref.get("text") or "").strip()
        if url and name:
            out[name.lower()] = (
                url if url.startswith("http") else f"https://www.linkedin.com{url}"
            )
    return out


def _split_feed_posts(blob: str) -> list[list[str]]:
    """Split the concatenated section text into per-post line lists."""
    chunks = _FEED_POST_MARKER.split(blob)
    return [chunk.strip("\n").splitlines() for chunk in chunks if chunk.strip()]


def _parse_post_chunk(lines: list[str], references: dict[str, str]) -> Post | None:
    """Parse one post's lines into a Post.

    Layout observed live:
        <Author Name>
        (blank / connection-degree lines)
        <Headline>
        <age> \u2022
        Follow
        <post body...>
        ... <job-card noise ...>
    """
    cleaned = [ln.replace("\u200b", "").strip() for ln in lines]
    cleaned = [ln for ln in cleaned if ln not in _SKIP_LINES]
    if not cleaned:
        return None

    author_name = cleaned[0]
    # Skip connection-degree markers like "\u2022 3rd+" or "\u2022 1st".
    rest = [ln for ln in cleaned[1:] if not re.fullmatch(r"\u2022?\s*(1st|2nd|3rd\+?)\s*\u2022?", ln)]

    headline = ""
    body_start = 0
    for i, ln in enumerate(rest[:6]):
        if _RELATIVE_AGE.match(ln):
            body_start = i + 1
            break
        if not headline:
            headline = ln
            body_start = i + 1
    if body_start == 0:
        body_start = min(1, len(rest))

    body = "\n".join(rest[body_start:]).strip()
    # Cut embedded job-card noise (observed in job-attached posts) at the
    # earliest card marker.
    card_markers = (
        "Actively reviewing applicants",
        "View job",
        "(Verified job)",
        "Are these results helpful?",
        "Load more",
    )
    cuts = [idx for idx in (body.find(m) for m in card_markers) if idx != -1]
    if cuts:
        idx = min(cuts)
        # Cut at the last line break before the marker so the partial line
        # the marker sits in is dropped whole.
        nl = body.rfind("\n", 0, idx)
        body = (body[:nl] if nl != -1 else "").strip()
    if body.endswith("\u2026 more"):
        body = body[: -len("\u2026 more")].strip()
    # Drop trailing reaction-count lines (bare integers observed at chunk end).
    body_lines = body.splitlines()
    while body_lines and body_lines[-1].strip().isdigit():
        body_lines.pop()
    body = "\n".join(body_lines).strip()

    if not body:
        return None

    profile_url = references.get(author_name.lower(), "")
    # No real post ids exist in this response; derive a stable id for dedup.
    post_id = hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]

    return Post(
        post_id=post_id,
        text=body,
        post_url="",
        author_name=author_name,
        author_headline=headline,
        author_profile_url=profile_url,
        posted_at="",
        raw={"author": author_name, "headline": headline, "profile_url": profile_url},
    )


class LinkedInMCPSource:
    name = "linkedin"
    raw_tool = raw_tool

    def __init__(self) -> None:
        self._proc: _MCPProcess | None = None

    async def _ensure_session(self) -> _MCPProcess:
        if self._proc is None:
            self._proc = _MCPProcess()
            await self._proc.start()
            await self._proc.initialize()
        return self._proc

    async def _tool_text(self, tool: str, arguments: dict[str, Any]) -> str:
        try:
            proc = await self._ensure_session()
            result = await proc.call_tool(tool, arguments)
        except SearchError:
            await self.close()
            raise
        try:
            return _content_text(result)
        except SearchError:
            await self.close()
            raise

    async def search_posts(self, keyword: str, recency: Recency) -> SearchResult:
        """One search_posts call.

        Response schema confirmed by the live validation pass (the plan's
        open item): the tool returns {"url", "sections", "references"}
        where sections.search_results is a single text blob with every post
        separated by "Feed post" lines, and references.search_results maps
        author names to profile URLs. There are no per-post ids or
        permalinks, so post ids are content-derived for dedup purposes.
        """
        text = await self._tool_text(
            "search_posts",
            {"keywords": keyword, "date_posted": _RECENTY_MAP[recency]},
        )

        data: Any = None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            pass

        if not isinstance(data, dict) or not isinstance(data.get("sections"), dict):
            # Non-JSON or unrecognizable shape — soft failure (session alive
            # but response unusable). Empty results are legitimate; garbage
            # is not, and the UI needs to tell them apart.
            return SearchResult(posts=[], raw_hit_count=0, degraded=bool(text.strip()))

        section_text = data["sections"].get("search_results") or ""
        references = _person_references(data)
        posts: list[Post] = []
        for lines in _split_feed_posts(section_text):
            post = _parse_post_chunk(lines, references)
            if post is not None:
                posts.append(post)

        degraded = not posts and bool(section_text.strip())
        return SearchResult(posts=posts, raw_hit_count=len(posts), degraded=degraded)

    def _extract_items(self, data: Any) -> list[Any]:
        """Fallback for list-shaped responses (kept for robustness)."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("posts", "results", "items", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return []

    async def search_people(self, query: str) -> list[dict[str, Any]]:
        """People lookup for the DM fallback. The response's references
        section carries profile URLs keyed by name — exactly what a DM
        target needs."""
        text = await self._tool_text("search_people", {"keywords": query})
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        people = [
            {"name": name, "profile_url": url}
            for name, url in _person_references(data).items()
        ]
        if people:
            return people
        return [i for i in self._extract_items(data) if isinstance(i, dict)]

    async def check_health(self) -> dict[str, str]:
        """Canary: initialize the container and call a read-only tool.
        Distinguishes hard failure (spawn/init), auth failure (login
        markers), and soft failure (alive but empty/garbage)."""
        prereq = prerequisites()
        if not prereq["docker_installed"]:
            return {
                "status": "unavailable",
                "detail": "Docker is not installed or not on PATH.",
            }
        if not prereq["session_dir_present"]:
            return {
                "status": "not_configured",
                "detail": "No LinkedIn session found — run the one-time login "
                "(docker run ... --login --login-viewer, port 6080).",
            }
        try:
            text = await self._tool_text("get_my_profile", {})
        except SearchError as exc:
            return {"status": _classify_failure(str(exc)), "detail": str(exc)[:300]}
        if not text.strip():
            return {
                "status": "degraded",
                "detail": "Session answered but returned an empty profile — "
                "responses look unreliable.",
            }
        return {
            "status": "valid",
            "detail": "Session is live and responding to tool calls.",
        }

    async def close(self) -> None:
        if self._proc:
            await self._proc.close()
            self._proc = None
