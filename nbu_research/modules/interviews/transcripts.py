"""Transcript import: parse uploaded interview transcripts (.txt/.docx) into
the standard session message format ([{"role", "content"}]).

Speaker prefixes (case-insensitive, leading whitespace allowed):
  - "Interviewer:" or "I:"  -> assistant turn
  - "Respondent:"  or "R:"  -> user turn
Consecutive lines from the same speaker merge into one turn; un-prefixed lines
continue the current speaker's turn. When a transcript contains no speaker
prefixes at all, the whole text becomes a single user turn.
"""
import io
import os
import re

_SPEAKER_RE = re.compile(r"^\s*(interviewer|respondent|i|r)\s*:\s?", re.IGNORECASE)

_ROLE_FOR = {
    "interviewer": "assistant",
    "i": "assistant",
    "respondent": "user",
    "r": "user",
}


def parse_transcript(text):
    """Parse transcript text into [{"role", "content"}] messages."""
    lines = (text or "").splitlines()
    messages = []
    current_role = None
    current_lines = []

    def flush():
        nonlocal current_lines
        if current_role is not None:
            content = "\n".join(current_lines).strip()
            if content:
                messages.append({"role": current_role, "content": content})
        current_lines = []

    for line in lines:
        match = _SPEAKER_RE.match(line)
        if match:
            role = _ROLE_FOR[match.group(1).lower()]
            if role != current_role:
                flush()
                current_role = role
            current_lines.append(line[match.end():])
        elif current_role is not None:
            # Continuation of the current speaker's turn.
            current_lines.append(line)
        # Un-prefixed text before any speaker prefix is ignored here; the
        # no-prefix fallback below covers prefix-free transcripts entirely.
    flush()

    if not messages:
        whole = (text or "").strip()
        if whole:
            messages = [{"role": "user", "content": whole}]
    return messages


def extract_text(filename, raw):
    """Decode an uploaded transcript file's bytes to text.

    .docx via python-docx (paragraph texts); .txt as utf-8 with latin-1
    fallback. Raises ValueError for unsupported extensions.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".docx":
        import docx  # python-docx; already a platform dependency

        document = docx.Document(io.BytesIO(raw))
        return "\n".join(p.text for p in document.paragraphs)
    if ext == ".txt":
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1")
    raise ValueError(f"Unsupported transcript file type: {ext or filename}")
