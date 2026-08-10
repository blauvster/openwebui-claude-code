"""
Invisible in-text marker carrying the Claude Code session_id as a fallback
to the SQLite-backed SessionStore (see session_store.py).

Rationale
---------
The SQLite store (Variant A) is the primary source of truth: it survives
process restarts and worker changes without depending on what OpenWebUI
sends back in the request body. But it has one failure mode worth guarding
against: if WORKDIR_ROOT points at ephemeral/container-local storage that
gets wiped independently of OpenWebUI's own chat database (e.g. different
persistent-volume lifecycle in a container redeploy), the SQLite file can
disappear while OpenWebUI's chat history — which is persisted separately and
NOT touched by this pipe — still exists and still contains the last
assistant reply.

To cover that case, every assistant reply gets an invisible marker appended:

    [claude-session:<session_id>]: #

This is a Markdown *link reference definition* (CommonMark spec, "reference
link" section): a line of the form `[label]: destination` that is never
rendered as visible output by CommonMark-compliant renderers — it just
registers a reference. Using `#` as the destination keeps it inert (no
actual link target semantics matter here; we only care about it being
absent from rendered output). This is the same technique already validated
in production by rbb-dev/Open-WebUI-OpenRouter-pipe for larger artifact
references.

Important: this marker rides inside the ordinary assistant message text
that OpenWebUI already persists as part of the normal chat flow. Nothing is
written into OpenWebUI's own chat/meta database columns from here — there is
no additional write path into OpenWebUI's data model at all, so there is no
extra conflict/race-condition surface beyond what streaming a normal
response already has.

Limitations (see also the write-up in chat): this only works if OpenWebUI
resends the previous assistant message inside `body["messages"]` on the next
turn (the normal case for OpenAI-Chat-Completions-shaped pipes). It is a
fallback, not a replacement, for the SQLite store.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_MARKER_RE = re.compile(r"\[claude-session:([A-Za-z0-9_-]{1,128})\]:\s*#")


def make_marker(session_id: str) -> str:
    """Return the invisible marker text to append to a streamed reply."""
    return f"\n\n[claude-session:{session_id}]: #\n"


def extract_session_id_from_messages(messages: List[Dict[str, Any]]) -> Optional[str]:
    """Scan chat history (most recent first) for the last embedded marker.

    Looks only at assistant messages, newest to oldest, and returns the
    session_id from the first marker found. Returns None if no marker is
    present (e.g. first turn in a chat, or history was trimmed/edited).
    """
    for message in reversed(messages or []):
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        text = _flatten_content(content)
        if not text:
            continue
        match = _MARKER_RE.search(text)
        if match:
            return match.group(1)
    return None


def strip_marker(text: str) -> str:
    """Remove marker lines from text before showing/logging it elsewhere.

    Not needed for normal rendering (CommonMark already hides it), but
    useful if the raw text is re-used somewhere that doesn't apply Markdown
    rendering (e.g. plain-text export, logs).
    """
    return _MARKER_RE.sub("", text)


def _flatten_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return "\n".join(parts)
    return ""
