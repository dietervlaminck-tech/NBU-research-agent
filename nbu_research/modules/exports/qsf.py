"""Qualtrics Survey Format (.qsf) export for survey studies.

Produces the standard QSF JSON structure so the file can be imported via
Qualtrics "Import survey": SurveyEntry + SurveyElements (SQ per question,
plus the required BL, FL, SO and QC elements).
"""
import json

from ... import db
from .common import slugify

MAX_SCALE_POINTS = 50


def _choices(options):
    choices = {str(i): {"Display": str(o)} for i, o in enumerate(options, 1)}
    return choices, [str(i) for i in range(1, len(options) + 1)]


def _scale_options(scale):
    scale = scale or {}
    try:
        mn, mx = int(scale.get("min", 1)), int(scale.get("max", 5))
    except (TypeError, ValueError):
        mn, mx = 1, 5
    if mx < mn:
        mn, mx = mx, mn
    mx = min(mx, mn + MAX_SCALE_POINTS - 1)
    labels = []
    for v in range(mn, mx + 1):
        label = str(v)
        if v == mn and scale.get("min_label"):
            label = f"{v} — {scale['min_label']}"
        elif v == mx and scale.get("max_label"):
            label = f"{v} — {scale['max_label']}"
        labels.append(label)
    return labels


def _base_payload(q, qid):
    text = q.get("text", "")
    return {
        "QuestionText": text,
        "DataExportTag": q.get("id") or qid,
        "QuestionDescription": text[:100],
        "Configuration": {"QuestionDescriptionOption": "UseText"},
        "Validation": {"Settings": {
            "ForceResponse": "ON" if q.get("required") else "OFF",
            "ForceResponseType": "ON",
            "Type": "None",
        }},
        "Language": [],
        "QuestionID": qid,
    }


def _question_payload(q, qid):
    payload = _base_payload(q, qid)
    qtype = q.get("type", "open")

    if qtype in ("multiple_choice", "dropdown", "checkbox", "likert"):
        payload["QuestionType"] = "MC"
        if qtype == "dropdown":
            payload["Selector"] = "DL"
        elif qtype == "checkbox":
            payload["Selector"] = "MAVR"
            payload["SubSelector"] = "TX"
        else:
            payload["Selector"] = "SAVR"
            payload["SubSelector"] = "TX"
        options = (_scale_options(q.get("scale")) if qtype == "likert"
                   else [str(o) for o in (q.get("options") or [])])
        payload["Choices"], payload["ChoiceOrder"] = _choices(options)
    elif qtype == "numeric":
        payload["QuestionType"] = "TE"
        payload["Selector"] = "SL"
        payload["Validation"]["Settings"]["Type"] = "ContentType"
        payload["Validation"]["Settings"]["ContentType"] = "ValidNumber"
    elif qtype == "matrix":
        payload["QuestionType"] = "Matrix"
        payload["Selector"] = "Likert"
        payload["SubSelector"] = "SingleAnswer"
        rows = [str(r) for r in (q.get("rows") or [])]
        payload["Choices"], payload["ChoiceOrder"] = _choices(rows)
        answers = _scale_options(q.get("scale"))
        payload["Answers"] = {str(i): {"Display": a} for i, a in enumerate(answers, 1)}
        payload["AnswerOrder"] = [str(i) for i in range(1, len(answers) + 1)]
        payload["ChoiceDataExportTags"] = False
    else:  # open text
        payload["QuestionType"] = "TE"
        payload["Selector"] = "ML"
    return payload


def study_qsf(study_id):
    study = db.get("studies", study_id) or {}
    config = study.get("config") or {}
    questions = config.get("questions") or []
    survey_id = "SV_" + (study.get("id") or "export")[:15]
    title = study.get("title") or "Survey"

    elements, block_elements = [], []
    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            continue
        qid = f"QID{i + 1}"
        elements.append({
            "SurveyID": survey_id,
            "Element": "SQ",
            "PrimaryAttribute": qid,
            "SecondaryAttribute": q.get("text", "")[:100],
            "TertiaryAttribute": None,
            "Payload": _question_payload(q, qid),
        })
        block_elements.append({"Type": "Question", "QuestionID": qid})

    structural = [
        {
            "SurveyID": survey_id,
            "Element": "BL",
            "PrimaryAttribute": "Survey Blocks",
            "SecondaryAttribute": None,
            "TertiaryAttribute": None,
            "Payload": [
                {"Type": "Default", "Description": "Default Question Block",
                 "ID": "BL_1", "BlockElements": block_elements},
                {"Type": "Trash", "Description": "Trash / Unused Questions",
                 "ID": "BL_2", "BlockElements": []},
            ],
        },
        {
            "SurveyID": survey_id,
            "Element": "FL",
            "PrimaryAttribute": "Survey Flow",
            "SecondaryAttribute": None,
            "TertiaryAttribute": None,
            "Payload": {
                "Type": "Root",
                "FlowID": "FL_1",
                "Flow": [{"Type": "Block", "ID": "BL_1", "FlowID": "FL_2"}],
                "Properties": {"Count": 2},
            },
        },
        {
            "SurveyID": survey_id,
            "Element": "SO",
            "PrimaryAttribute": "Survey Options",
            "SecondaryAttribute": None,
            "TertiaryAttribute": None,
            "Payload": {
                "BackButton": "false",
                "SaveAndContinue": "true",
                "SurveyProtection": "PublicSurvey",
                "BallotBoxStuffingPrevention": "false",
                "NoIndex": "Yes",
                "SecureResponseFiles": "true",
                "SurveyExpiration": "None",
                "SurveyTermination": "DefaultMessage",
                "Header": config.get("welcome_text", ""),
                "Footer": config.get("thankyou_text", ""),
            },
        },
        {
            "SurveyID": survey_id,
            "Element": "QC",
            "PrimaryAttribute": "Survey Question Count",
            "SecondaryAttribute": str(len(block_elements)),
            "TertiaryAttribute": None,
            "Payload": None,
        },
    ]

    qsf = {
        "SurveyEntry": {
            "SurveyID": survey_id,
            "SurveyName": title,
            "SurveyDescription": None,
            "SurveyOwnerID": "",
            "SurveyBrandID": "",
            "DivisionID": None,
            "SurveyLanguage": "EN",
            "SurveyActiveResponseSet": "RS_1",
            "SurveyStatus": "Inactive",
            "SurveyStartDate": "0000-00-00 00:00:00",
            "SurveyExpirationDate": "0000-00-00 00:00:00",
            "SurveyCreationDate": study.get("created_at", ""),
            "CreatorID": "",
            "LastModified": study.get("created_at", ""),
            "LastAccessed": "0000-00-00 00:00:00",
            "LastActivated": "0000-00-00 00:00:00",
            "Deleted": None,
        },
        "SurveyElements": structural + elements,
    }
    data = json.dumps(qsf, indent=1, ensure_ascii=False).encode("utf-8")
    return data, f"{slugify(title)}.qsf", "application/json"
