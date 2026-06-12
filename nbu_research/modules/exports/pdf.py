"""PDF article export via reportlab platypus.

A small markdown -> platypus story converter (headings, paragraphs, bullet
lists, **bold**/*italic*/`code` inline, fenced code blocks). The built-in
Helvetica fonts are latin-1 only, so text is normalized first (typographic
punctuation folded to ASCII, anything else outside latin-1 dropped).
"""
import io
import re
from datetime import date
from html import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (HRFlowable, Paragraph, Preformatted,
                                SimpleDocTemplate, Spacer)

from ... import db
from .common import slugify

# Typographic characters Helvetica (latin-1) cannot show, folded to ASCII.
_FOLD = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "−": "-",
    "…": "...",
    " ": " ", " ": " ", " ": " ",
}


def _latin1(text):
    """Normalize text to what the built-in PDF fonts can render."""
    text = str(text or "")
    for src, dst in _FOLD.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", "ignore").decode("latin-1")


def _inline(text):
    """Escape HTML, then map markdown inline markup to platypus tags."""
    text = escape(_latin1(text), quote=False)
    text = re.sub(r"`(.+?)`", r'<font face="Courier">\1</font>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    return text


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("ArticleTitle", parent=base["Title"],
                                spaceAfter=8),
        "meta": ParagraphStyle("ArticleMeta", parent=base["Normal"],
                               fontSize=10, leading=13,
                               textColor=colors.HexColor("#555555"),
                               alignment=1, spaceAfter=2),
        "h1": ParagraphStyle("MdH1", parent=base["Heading1"],
                             spaceBefore=16, spaceAfter=6),
        "h2": ParagraphStyle("MdH2", parent=base["Heading2"],
                             spaceBefore=12, spaceAfter=5),
        "h3": ParagraphStyle("MdH3", parent=base["Heading3"],
                             spaceBefore=10, spaceAfter=4),
        "body": ParagraphStyle("MdBody", parent=base["BodyText"],
                               leading=14, spaceAfter=6),
        "bullet": ParagraphStyle("MdBullet", parent=base["BodyText"],
                                 leading=14, leftIndent=18, bulletIndent=6,
                                 spaceAfter=3),
        "code": ParagraphStyle("MdCode", parent=base["Code"],
                               leading=11, leftIndent=12,
                               backColor=colors.whitesmoke,
                               spaceBefore=4, spaceAfter=8),
    }


_HR = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")


def md_to_story(md_text, styles=None):
    """Convert markdown to a list of platypus flowables."""
    styles = styles or _styles()
    story = []
    lines = (md_text or "").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):           # fenced code block
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1                               # skip closing fence
            story.append(Preformatted(_latin1("\n".join(block)), styles["code"]))
            continue
        i += 1
        if not stripped:
            continue
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:                                    # heading (levels 4-6 -> h3)
            level = min(len(m.group(1)), 3)
            story.append(Paragraph(_inline(m.group(2)), styles[f"h{level}"]))
            continue
        if _HR.match(line):                      # horizontal rule
            story.append(Spacer(1, 4))
            story.append(HRFlowable(width="100%", thickness=0.5,
                                    color=colors.HexColor("#bbbbbb")))
            story.append(Spacer(1, 8))
            continue
        m = re.match(r"^\s*[-*+]\s+(.*)", line)
        if m:                                    # bullet list item
            story.append(Paragraph(_inline(m.group(1)), styles["bullet"],
                                   bulletText="•"))
            continue
        m = re.match(r"^\s*(\d+)\.\s+(.*)", line)
        if m:                                    # numbered list item
            story.append(Paragraph(_inline(m.group(2)), styles["bullet"],
                                   bulletText=f"{m.group(1)}."))
            continue
        story.append(Paragraph(_inline(line), styles["body"]))
    return story


def article_pdf(article_id):
    """Render an article's markdown draft to a PDF (bytes, filename, mime)."""
    article = db.get("articles", article_id) or {}
    title = article.get("title") or "Article"
    project = (db.get("projects", article["project_id"])
               if article.get("project_id") else None)

    styles = _styles()
    story = [Paragraph(_inline(title), styles["title"])]
    if project and project.get("title"):
        story.append(Paragraph(_inline(f"Project: {project['title']}"),
                               styles["meta"]))
    story.append(Paragraph(f"Generated {date.today().isoformat()}",
                           styles["meta"]))
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=0.5,
                            color=colors.HexColor("#bbbbbb")))
    story.append(Spacer(1, 14))

    content = (article.get("content_md") or "").strip()
    if content:
        story.extend(md_to_story(content, styles))
    else:
        story.append(Paragraph("This article has no content yet.",
                               styles["body"]))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, title=_latin1(title),
        leftMargin=2.5 * cm, rightMargin=2.5 * cm,
        topMargin=2.5 * cm, bottomMargin=2.5 * cm,
    )
    doc.build(story)
    return buf.getvalue(), f"{slugify(article.get('title'))}.pdf", "application/pdf"
