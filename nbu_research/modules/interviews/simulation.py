"""Run a full simulated qualitative study from a research question + N.

Auto-generates an interview guide and N diverse respondent personas, runs each
interview (the platform's real interviewer talks to a Haiku-played persona),
stores everything as a normal interview study + sessions — clearly flagged as
simulated — then (optionally) chains straight into thematic analysis and lands
on the report. This is the live-demo centrepiece.

Cross-module note: this imports analysis.qualitative to chain into thematic
coding — a sanctioned one-way call (same pattern as exports→analysis),
documented in docs/INTERFACES.md.
"""
from ... import db, llm
from ...config import DEFAULT_INTERVIEW_MODEL, MODEL_HAIKU
from ...jobs import job as job_task, start_job, update_progress
from ..analysis import qualitative
from . import bot

MAX_N = 20
MAX_TURNS = 8  # interviewer question/answer rounds before a forced wrap-up

PERSONAS_SCHEMA = {
    "type": "object",
    "properties": {
        "personas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["name", "role", "description"],
            },
        }
    },
    "required": ["personas"],
}


def _generate_outline(research_question, model):
    system = (
        "You are a qualitative methodologist. Given a research question, write a "
        "concise semi-structured interview guide: 5-6 numbered topic areas with a "
        "short lead question each, moving from warm-up to core to closing. Plain "
        "text, no preamble."
    )
    return llm.complete(system, f"Research question: {research_question}",
                        model=model, max_tokens=1200)


def _generate_personas(research_question, n, guidance, model):
    system = (
        "You design realistic, demographically and attitudinally DIVERSE "
        "interview respondents for a qualitative study. Vary roles, seniority, "
        "enthusiasm vs. skepticism, age, and background so the sample is rich. "
        "Each description is 2-3 sentences the respondent could be role-played from."
    )
    prompt = (
        f"Research question: {research_question}\n"
        f"Generate exactly {n} distinct personas who would be relevant respondents."
    )
    if guidance:
        prompt += f"\nExtra guidance on who to include: {guidance}"
    data = llm.complete_json(system, prompt, PERSONAS_SCHEMA, model=model, max_tokens=4000)
    personas = data.get("personas", [])[:n]
    # Pad defensively if the model under-produces.
    while len(personas) < n:
        personas.append({"name": f"Respondent {len(personas) + 1}",
                         "role": "Participant", "description": "A relevant respondent."})
    return personas


def _persona_reply(persona, research_question, transcript, question, model):
    system = (
        "You are role-playing a single interview respondent. Stay fully in "
        "character; answer naturally in the first person, 2-5 sentences, no stage "
        "directions or meta-commentary. Bring your own opinions, hesitations and "
        "concrete examples."
    )
    prompt = (
        f"Your persona — {persona['name']} ({persona.get('role','')}): "
        f"{persona.get('description','')}\n\n"
        f"The study's research question (for context, don't recite it): {research_question}\n\n"
        f"Conversation so far:\n{transcript}\n\n"
        f"The interviewer just asked:\n{question}\n\nYour reply, in character:"
    )
    return llm.complete(system, prompt, model=model, max_tokens=400, thinking=False).strip()


def _run_one_interview(study, persona, research_question, persona_model):
    """Drive a full interview between the platform interviewer and the persona.
    Returns the message list [{role, content}]."""
    messages = [{"role": "user", "content": bot.FIRST_USER_MESSAGE}]
    for _turn in range(MAX_TURNS):
        # Interviewer turn (reuses the real bot, incl. termination codes).
        text, done = "", None
        for ev in bot.stream_interview_reply(study, messages):
            if ev["type"] == "text":
                text += ev.get("content", "")
            elif ev["type"] == "done":
                done = ev.get("code")
        messages.append({"role": "assistant", "content": text.strip()})
        if done:  # completion or safety code reached
            break
        transcript = "\n".join(
            f"{'Interviewer' if m['role'] == 'assistant' else persona['name']}: {m['content']}"
            for m in messages if m["content"].strip()
        )
        answer = _persona_reply(persona, research_question, transcript,
                                text.strip(), persona_model)
        messages.append({"role": "user", "content": answer})
    return messages


@job_task("interview_simulation")
def _run_simulation(job_id, research_question=None, n=4, outline=None,
                    persona_guidance=None, project_id=None,
                    model=None, language=None, auto_analyze=True):
    model = model or DEFAULT_INTERVIEW_MODEL
    n = max(1, min(int(n), MAX_N))

    update_progress(job_id, 0.04, "Designing the interview guide…")
    if not (outline or "").strip():
        outline = _generate_outline(research_question, model)

    update_progress(job_id, 0.10, f"Inventing {n} diverse respondents…")
    personas = _generate_personas(research_question, n, persona_guidance, model)

    config = {
        "interview_outline": outline,
        "general_instructions": bot.DEFAULT_GENERAL_INSTRUCTIONS,
        "simulated": True,
        "n_simulated": n,
        "personas": personas,
    }
    if language and language.lower() != "en":
        config["language"] = language

    study_id = db.insert("studies", {
        "project_id": project_id,
        "study_type": "interview",
        "title": f"[Simulation] {research_question[:80]}",
        "research_question": research_question,
        "config": config,
        "model": model,
        "status": "open",
    })
    study = db.get("studies", study_id)

    # Interviews occupy 0.10 → 0.65 of the bar.
    for i, persona in enumerate(personas):
        update_progress(job_id, 0.10 + 0.55 * (i / n),
                        f"Interviewing {persona['name']} ({i + 1}/{n})…")
        messages = _run_one_interview(study, persona, research_question, MODEL_HAIKU)
        db.insert("sessions", {
            "study_id": study_id,
            "respondent_name": persona["name"],
            "messages": messages,
            "status": "completed",
            "started_at": db.now(),
            "completed_at": db.now(),
        })

    result = {"study_id": study_id, "n": n, "simulated": True}

    if auto_analyze:
        update_progress(job_id, 0.68, "Interviews done — running thematic analysis…")
        # Chains into the real thematic pipeline on this same job (it manages
        # progress from here: codebook → coding → synthesis).
        analysis = qualitative.run_thematic_analysis(study_id, job_id)
        result["analysis_id"] = analysis.get("analysis_id")
    else:
        update_progress(job_id, 1.0, f"Simulation complete — {n} interviews ready.")

    return result


def start_simulation(research_question, n, outline=None, persona_guidance=None,
                     project_id=None, model=None, language=None, auto_analyze=True):
    return start_job("interview_simulation", {
        "research_question": research_question,
        "n": max(1, min(int(n), MAX_N)),
        "outline": outline,
        "persona_guidance": persona_guidance,
        "project_id": project_id,
        "model": model,
        "language": language,
        "auto_analyze": auto_analyze,
    }, ref_table="studies")
