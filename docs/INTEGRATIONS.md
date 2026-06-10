# Integrations & Export Formats

Standards-based file exchange — no vendor API keys required. Every format is
produced by the export registry (`modules/exports/`) and downloadable from
`/exports/<study|article|review>/<id>`.

## Qualitative analysis tools — REFI-QDA (.qdpx)

The [REFI-QDA Project standard](https://www.qdasoftware.org/) is the official
exchange format adopted by Atlas.ti, NVivo, MAXQDA, QDA Miner, Quirkos,
Transana and others. Our `.qdpx` contains:

- one text source per interview session (full transcript)
- the codebook (codes with descriptions, hierarchy preserved)
- coded segments as plain-text selections linked to codes
- cases per respondent

Import: Atlas.ti → *File → Import Project (REFI-QDA)*; NVivo → *Import → REFI-QDA*;
MAXQDA → *Import → REFI-QDA Project*. You can continue (re)coding there.

## SPSS — .sav

Survey responses export as native SPSS files via `pyreadstat`:
numeric variables for Likert/numeric items, variable labels = question text,
value labels from scale anchors, sanitized variable names. Also opens in
JASP, jamovi, R (`haven`), and Stata (via conversion).

## Qualtrics — .qsf

Survey *instruments* export as Qualtrics Survey Format for fielding or
archiving in Qualtrics: *Create project → Survey → Import QSF*. Question type
mapping: likert/MC/dropdown → MC, checkbox → MC multi, open → Text Entry,
numeric → validated Text Entry, matrix → Matrix/Likert.
Response data can travel the other direction by exporting Qualtrics responses
to CSV and analyzing them in SPSS alongside our .sav files. (Live Qualtrics
REST API sync is on the roadmap.)

## Documents & manuscripts

| Format | Source | Notes |
|---|---|---|
| DOCX | articles, reviews, interview transcripts | Word/Google Docs ready |
| LaTeX | articles | `article` class, sections from markdown |
| HTML / Markdown | articles, reviews | web & pipeline friendly |
| BibTeX (.bib) | literature review sources | for LaTeX/Zotero/Mendeley |

## Tabular data

CSV / XLSX / JSON for every study: surveys as a wide respondent × item matrix
(checkboxes one-hot, matrices flattened), interviews as session metadata +
transcripts.

## Distribution channels

Respondent links for interviews and surveys come with branded share actions:
WhatsApp, Telegram, Microsoft Teams, Slack, email, plus the raw URL/QR for
panels (Prolific, MTurk), LMS announcements, or printed materials — any channel
that can carry a URL.
