"""REFI-QDA Project (.qdpx) export for Atlas.ti / NVivo / MAXQDA.

A .qdpx file is a ZIP holding project.qde (REFI-QDA Project XML,
xmlns="urn:QDA-XML:project:1.0") plus a sources/ folder with one plain-text
file per interview transcript.
"""
import io
import uuid
import zipfile
import xml.etree.ElementTree as ET

from ... import db
from .common import slugify, transcript_text

NS = "urn:QDA-XML:project:1.0"


def _guid():
    return str(uuid.uuid4()).upper()


def _el(parent, tag, attrs=None):
    return ET.SubElement(parent, f"{{{NS}}}{tag}", attrs or {})


def _add_codes(parent_el, codes, code_guids, parent_id, visited):
    for code in codes:
        cid = code.get("id")
        if code.get("parent_id") != parent_id or cid in visited:
            continue
        visited.add(cid)
        el = _el(parent_el, "Code", {
            "guid": code_guids[cid],
            "name": code.get("name") or "Code",
            "isCodable": "true",
        })
        if code.get("description"):
            _el(el, "Description").text = code["description"]
        _add_codes(el, codes, code_guids, cid, visited)


def study_qdpx(study_id):
    study = db.get("studies", study_id) or {}
    sid = study.get("id", study_id)
    sessions = db.query("sessions", "study_id = ?", (sid,), order="started_at")
    codebooks = db.query("codebooks", "study_id = ?", (sid,))
    segments = []
    for cb in codebooks:
        segments += db.query("coded_segments", "codebook_id = ?", (cb["id"],))

    ET.register_namespace("", NS)
    project = ET.Element(f"{{{NS}}}Project", {
        "name": study.get("title") or "Interview study",
        "origin": "NBU Research Agent",
    })

    # CodeBook (codes nested via parent_id).
    all_codes = [c for cb in codebooks for c in (cb.get("codes") or [])
                 if isinstance(c, dict)]
    code_guids = {c.get("id"): _guid() for c in all_codes}
    if all_codes:
        codes_el = _el(_el(project, "CodeBook"), "Codes")
        known = set(code_guids)
        visited = set()
        _add_codes(codes_el, all_codes, code_guids, None, visited)
        # Codes whose parent_id points at a missing code become roots too.
        for c in all_codes:
            if c.get("id") not in visited and c.get("parent_id") not in known:
                visited.add(c.get("id"))
                el = _el(codes_el, "Code", {
                    "guid": code_guids[c.get("id")],
                    "name": c.get("name") or "Code",
                    "isCodable": "true",
                })
                if c.get("description"):
                    _el(el, "Description").text = c["description"]
                _add_codes(el, all_codes, code_guids, c.get("id"), visited)

    source_guids = {s["id"]: _guid() for s in sessions}

    # Cases (one per session; schema order puts Cases before Sources).
    if sessions:
        cases_el = _el(project, "Cases")
        for i, s in enumerate(sessions, 1):
            case = _el(cases_el, "Case", {
                "guid": _guid(),
                "name": s.get("respondent_name") or f"Session {i}",
            })
            _el(case, "SourceRef", {"targetGUID": source_guids[s["id"]]})

    # Sources with embedded coded selections.
    texts = {}
    if sessions:
        sources_el = _el(project, "Sources")
        for i, s in enumerate(sessions, 1):
            guid = source_guids[s["id"]]
            text = transcript_text(s)
            texts[guid] = text
            source = _el(sources_el, "TextSource", {
                "guid": guid,
                "name": s.get("respondent_name") or f"Session {i}",
                "plainTextPath": f"internal://{guid}.txt",
            })
            for seg in segments:
                if seg.get("session_id") != s["id"]:
                    continue
                code_guid = code_guids.get(seg.get("code_id"))
                seg_text = seg.get("text") or ""
                if not code_guid or not seg_text:
                    continue
                start = text.find(seg_text)
                if start < 0:
                    continue  # segment text not found in transcript; skip silently
                sel = _el(source, "PlainTextSelection", {
                    "guid": _guid(),
                    "startPosition": str(start),
                    "endPosition": str(start + len(seg_text)),
                })
                coding = _el(sel, "Coding", {"guid": _guid()})
                _el(coding, "CodeRef", {"targetGUID": code_guid})

    xml_bytes = ET.tostring(project, encoding="utf-8", xml_declaration=True)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.qde", xml_bytes)
        for guid, text in texts.items():
            zf.writestr(f"sources/{guid}.txt", text.encode("utf-8"))
    return buf.getvalue(), f"{slugify(study.get('title'))}.qdpx", "application/zip"
