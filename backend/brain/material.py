"""Material the candidate gives the interviewer to draw its questions from.

Two sources:
  - a file imported on the setup page (slides, notes, a résumé, a job description): POST
    /api/sessions/{id}/material, before the interview starts;
  - for a Class Presentation with no file, the candidate's own spoken description: the interviewer opens by
    asking what the presentation covers (lines.PRESENTATION_MATERIAL_LINE) and plans from the answer.

Text is read without extra dependencies: plain text as-is, .docx and .pptx (zip files of XML) from their
XML, including PowerPoint speaker notes. A PDF is kept as bytes and handed to Gemini as-is, since Gemini
reads PDFs natively.
"""

import html
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

MAX_BYTES = 10 * 1024 * 1024          # per upload
MAX_TEXT_CHARS = 30_000               # what goes into the planning prompt
MAX_UNZIPPED_BYTES = 60 * 1024 * 1024  # a .docx/.pptx expanding past this is refused (zip bomb guard)

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv"}
SUPPORTED = sorted(TEXT_SUFFIXES | {".pdf", ".docx", ".pptx"})


class MaterialError(ValueError):
    """The upload can't be used; the message is shown to the user."""


@dataclass
class Material:
    name: str                   # file name, or "your description" for the spoken kind
    text: str = ""              # extracted (or spoken) text, capped at MAX_TEXT_CHARS
    pdf: Optional[bytes] = None  # a PDF, sent to Gemini as-is
    spoken: bool = False        # True when it's the candidate's spoken description, not a file

    @property
    def kind(self) -> str:
        return "spoken" if self.spoken else "pdf" if self.pdf is not None else "text"


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _open_zip(data: bytes) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise MaterialError("that file looks damaged (it isn't a valid Office file)")
    if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED_BYTES:
        raise MaterialError("that file expands to more than 60 MB")
    return z


_W_PARAGRAPH = re.compile(r"<w:p[ >].*?</w:p>", re.S)
_W_TEXT = re.compile(r"<w:t(?: [^>]*)?>(.*?)</w:t>", re.S)
_A_PARAGRAPH = re.compile(r"<a:p>.*?</a:p>", re.S)
_A_TEXT = re.compile(r"<a:t>(.*?)</a:t>", re.S)


def _docx_text(data: bytes) -> str:
    z = _open_zip(data)
    try:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    except KeyError:
        raise MaterialError("that .docx has no document body")
    paragraphs = ("".join(_W_TEXT.findall(p)) for p in _W_PARAGRAPH.findall(xml))
    return html.unescape("\n".join(p for p in paragraphs if p.strip()))


def _slide_number(name: str) -> int:
    m = re.search(r"(\d+)\.xml$", name)
    return int(m.group(1)) if m else 0


def _pptx_text(data: bytes) -> str:
    z = _open_zip(data)
    names = z.namelist()
    slides = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=_slide_number)
    if not slides:
        raise MaterialError("that .pptx has no slides")
    notes = {_slide_number(n): n for n in names if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", n)}
    out = []
    for name in slides:
        n = _slide_number(name)
        xml = z.read(name).decode("utf-8", errors="replace")
        lines = ["".join(_A_TEXT.findall(p)) for p in _A_PARAGRAPH.findall(xml)]
        part = [f"Slide {n}:"] + [line for line in lines if line.strip()]
        if n in notes:
            nxml = z.read(notes[n]).decode("utf-8", errors="replace")
            # Notes pages also hold the slide number as a paragraph of its own: skip number-only lines.
            said = ("".join(_A_TEXT.findall(p)).strip() for p in _A_PARAGRAPH.findall(nxml))
            spoken = " ".join(t for t in said if t and not t.isdigit())
            if spoken:
                part.append(f"Speaker notes: {spoken}")
        out.append("\n".join(part))
    return html.unescape("\n\n".join(out))


def from_upload(filename: str, data: bytes) -> Material:
    """Read an imported file. Raises MaterialError with a user-facing reason if it can't be used."""
    name = Path(filename or "file").name[:200]
    suffix = Path(name).suffix.lower()
    if not data:
        raise MaterialError("that file is empty")
    if len(data) > MAX_BYTES:
        raise MaterialError("that file is over 10 MB")
    if suffix == ".pdf":
        if not data.startswith(b"%PDF"):
            raise MaterialError("that file isn't a valid PDF")
        return Material(name=name, pdf=data)
    if suffix in TEXT_SUFFIXES:
        text = data.decode("utf-8", errors="replace")
    elif suffix == ".docx":
        text = _docx_text(data)
    elif suffix == ".pptx":
        text = _pptx_text(data)
    else:
        raise MaterialError(f"{suffix or 'that'} files aren't supported: use {', '.join(SUPPORTED)}")
    text = _clean(text)
    if not text:
        raise MaterialError("there's no readable text in that file")
    return Material(name=name, text=text[:MAX_TEXT_CHARS])


def from_speech(transcript: str) -> Optional[Material]:
    """The candidate's spoken description of their presentation, or None if they said nothing."""
    text = _clean(transcript or "")
    return Material(name="your description", text=text[:MAX_TEXT_CHARS], spoken=True) if text else None
