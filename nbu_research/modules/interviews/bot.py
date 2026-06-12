"""Interview bot: system prompt construction and streamed replies.

Ported from the NBU-AI-interviewer reference app. All Anthropic calls go
through the shared llm helpers; study instrument fields live in the
`config` JSON column of the `studies` table.
"""
from ... import llm
from ...config import DEFAULT_INTERVIEW_MODEL

COMPLETION_CODE = "x7y8"
SAFETY_CODE = "5j3k"

FIRST_USER_MESSAGE = "Hello, I'm ready to start the interview."

DEFAULT_GENERAL_INSTRUCTIONS = """
You are an expert qualitative research interviewer. Follow these principles:

1. Ask one question at a time. Wait for the respondent's answer before moving on.
2. Use open-ended questions. Avoid yes/no questions when possible.
3. Listen actively: acknowledge what the respondent says before transitioning.
4. Use neutral, non-leading language. Do not suggest answers.
5. Probe for depth: ask follow-up questions like "Can you tell me more about that?" or "What do you mean by...?"
6. Stay flexible: if the respondent brings up something relevant, explore it before returning to the script.
7. Keep a warm, conversational, and professional tone.
8. Do not repeat questions that have already been thoroughly answered.
9. Track which topics have been covered and which remain.
10. When all questions have been sufficiently addressed, provide a brief summary of the key points discussed and ask the respondent if they want to add anything.
""".strip()

CLOSING_MESSAGE_COMPLETE = "Thank you very much for participating in this interview. Your responses have been recorded and will be very valuable for our research. You may now close this window."
CLOSING_MESSAGE_SAFETY = "Thank you for your time. The interview has ended. You may now close this window."

# Human-readable names for the language codes offered in the create form;
# any other config.language value is passed through verbatim.
LANGUAGE_NAMES = {
    "en": "English",
    "nl": "Dutch",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
}


def _language_section(config):
    """Firm instruction to conduct the interview in config.language (if set
    and not English). Termination codes are language-independent."""
    language = (config.get("language") or "en").strip()
    if not language or language.lower() == "en":
        return ""
    name = LANGUAGE_NAMES.get(language.lower(), language)
    return f"""

## Interview Language
You MUST conduct the ENTIRE interview in {name} ("{language}"). Every question, acknowledgement, follow-up probe, summary, and closing remark must be in {name} — even if the interview outline above is written in another language, translate it and ask in {name}. Only switch languages if the respondent explicitly asks you to. The termination codes below stay exactly as specified and are never translated."""


def build_system_prompt(study):
    config = study.get("config") or {}
    instructions = config.get("general_instructions") or DEFAULT_GENERAL_INSTRUCTIONS
    outline = config.get("interview_outline", "")

    return f"""You are conducting a qualitative research interview.

## Research Context
The research question guiding this study is: {study['research_question']}

## Interview Outline
Follow this interview script, adapting your questions based on the respondent's answers:

{outline}

## General Instructions
{instructions}{_language_section(config)}

## Termination Codes
- When you have asked all questions and the interview is complete, end your FINAL message with the code: {COMPLETION_CODE}
- If the respondent posts clearly problematic, offensive, or unethical content, end your message with the code: {SAFETY_CODE}

IMPORTANT: Only include a termination code when the condition is truly met. The code must appear at the very end of your message, on its own line. Do not mention these codes to the respondent."""


def stream_interview_reply(study, messages):
    """Yield {"type": "text"|"done", ...} events for one interviewer turn.

    Watches the accumulating stream for termination codes: the completion
    code ends the interview normally, the safety code aborts it. Codes are
    stripped before the text reaches the respondent.
    """
    system_prompt = build_system_prompt(study)
    model = study.get("model") or DEFAULT_INTERVIEW_MODEL
    api_messages = [{"role": m["role"], "content": m["content"]} for m in messages]

    full_text = ""
    for text in llm.stream_text(system_prompt, api_messages, model):
        full_text += text
        if COMPLETION_CODE in full_text:
            yield {"type": "text", "content": text.replace(COMPLETION_CODE, "")}
            yield {"type": "done", "code": "complete", "closing": CLOSING_MESSAGE_COMPLETE}
            return
        if SAFETY_CODE in full_text:
            yield {"type": "done", "code": "safety", "closing": CLOSING_MESSAGE_SAFETY}
            return
        yield {"type": "text", "content": text}
    yield {"type": "done", "code": None, "closing": None}
