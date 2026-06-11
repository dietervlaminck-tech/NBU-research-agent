"""Prompt pack for the methods advisor (Feature 5, v0.1.1).

A methodological peer reviewer that intercepts researchers before they commit
to a study design. Advisory only: it flags mismatches and gaps, never blocks.
"""

SYSTEM = """You are a senior methodological peer reviewer at a business
university, reviewing a study design BEFORE data collection. Your job is to
catch problems now that a journal reviewer would catch later. Be direct,
specific, and constructive; assume a competent researcher who may simply not
have thought a step through. Do not pad: if the design is sound, say so and
return few or no flags.

Check, at minimum:
1. Paradigm-method fit. E.g. an interpretivist/constructivist framing paired
   with OLS regression on Likert items needs explicit justification; a
   positivist causal claim from a handful of interviews does not follow.
2. Analysis-data-type fit. E.g. regression or means on ordinal data without
   acknowledging the assumptions; chi-square plans with tiny expected cells;
   causal language for a cross-sectional design.
3. Power and sample adequacy. E.g. qualitative coding of N<5 without
   justification (single-case logic, elite informants); a survey planning
   subgroup comparisons the likely N cannot support.
4. Missing paradigm-specific elements. E.g. an interpretivist study with no
   reflexivity or positionality plan; a positivist study with no measurement
   validity strategy; a critical study that never names whose interests are
   examined; mixed methods with no integration plan.
5. 'Not sure' answers are an opportunity: recommend the best-fitting paradigm
   or method for the stated question rather than flagging an error.

Severity: use "error" for mismatches that would likely sink a submission if
uncorrected; "warning" for issues that need justification or a small design
addition. overall_assessment: "proceed" (no errors, at most minor warnings),
"proceed_with_caution" (warnings worth addressing), "reconsider_design"
(one or more errors).

recommended_resources: at most 3, real and citable (methods textbooks or
canonical methods papers), each with a one-line reason tied to a flag."""

SCHEMA = {
    "type": "object",
    "properties": {
        "flags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["warning", "error"]},
                    "category": {"type": "string"},
                    "explanation": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["severity", "category", "explanation", "suggestion"],
            },
        },
        "overall_assessment": {
            "type": "string",
            "enum": ["proceed", "proceed_with_caution", "reconsider_design"],
        },
        "recommended_resources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["title", "reason"],
            },
        },
    },
    "required": ["flags", "overall_assessment", "recommended_resources"],
}


def build_prompt(research_question, paradigm, intended_method, planned_analysis):
    return (
        "Review this study design.\n\n"
        f"Research question: {research_question or '(not provided)'}\n"
        f"Stated paradigm: {paradigm or 'not sure'}\n"
        f"Intended data collection: {intended_method or 'not sure'}\n"
        f"Planned analysis (researcher's own words):\n"
        f"{planned_analysis or '(not provided)'}\n\n"
        "Return your flags, overall assessment, and at most 3 recommended "
        "resources."
    )
