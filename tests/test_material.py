"""Imported material (setup page) and a presentation's spoken description, which the questions are planned from."""

import base64
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from backend.brain import gemini, lines, material
from backend.brain.state import metas
from backend.main import app


def office_file(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, xml in entries.items():
            z.writestr(name, xml)
    return buf.getvalue()


DOCX = office_file({"word/document.xml":
                    '<w:document><w:body><w:p><w:r><w:t>Coastal flooding &amp; housing</w:t></w:r></w:p>'
                    '<w:p><w:r><w:t xml:space="preserve">Sea levels rose 20 cm.</w:t></w:r></w:p></w:body></w:document>'})
PPTX = office_file({
    "ppt/slides/slide2.xml": "<p:sld><a:p><a:r><a:t>Method: survey of 400 homes</a:t></a:r></a:p></p:sld>",
    "ppt/slides/slide1.xml": "<p:sld><a:p><a:r><a:t>Climate and coastal housing</a:t></a:r></a:p></p:sld>",
    "ppt/notesSlides/notesSlide1.xml": "<p:notes><a:p><a:r><a:t>Open with the 2019 storm.</a:t></a:r></a:p>"
                                       "<a:p><a:r><a:t>1</a:t></a:r></a:p></p:notes>",
})


def test_reads_text_docx_and_pptx_without_extra_libraries():
    assert material.from_upload("notes.md", b"# Talk\n\nPoint one").text == "# Talk\n\nPoint one"
    assert material.from_upload("essay.docx", DOCX).text == "Coastal flooding & housing\nSea levels rose 20 cm."
    pptx = material.from_upload("deck.pptx", PPTX).text
    assert pptx.index("Slide 1:") < pptx.index("Slide 2:")                     # slides in order
    assert "Speaker notes: Open with the 2019 storm." in pptx and "Speaker notes: Open with the 2019 storm. 1" not in pptx


def test_a_pdf_is_kept_as_bytes_for_gemini():
    m = material.from_upload("slides.pdf", b"%PDF-1.7 rest of file")
    assert m.pdf == b"%PDF-1.7 rest of file" and m.text == "" and m.kind == "pdf"


@pytest.mark.parametrize("name,data,reason", [
    ("deck.key", b"x", "aren't supported"),
    ("empty.txt", b"", "empty"),
    ("fake.pdf", b"hello", "valid PDF"),
    ("broken.docx", b"not a zip", "damaged"),
    ("blank.txt", b"   \n  ", "no readable text"),
    ("huge.txt", b"a" * (material.MAX_BYTES + 1), "over 10 MB"),
])
def test_unusable_files_are_refused_with_a_reason(name, data, reason):
    with pytest.raises(material.MaterialError, match=reason):
        material.from_upload(name, data)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def create(client, interview_type):
    return client.post("/api/sessions", json={"user_name": "Ada", "interview_type": interview_type,
                                               "role_title": "Coastal housing"}).json()["session_id"]


def upload(client, sid, filename, data):
    return client.post(f"/api/sessions/{sid}/material",
                       json={"filename": filename, "data": base64.b64encode(data).decode()})


def test_upload_route(client):
    sid = create(client, "Class Presentation")
    r = upload(client, sid, "essay.docx", DOCX)
    assert r.status_code == 200 and r.json() == {"name": "essay.docx", "kind": "text", "chars": 49}
    assert metas[sid].material.text.startswith("Coastal flooding")
    assert upload(client, sid, "deck.key", b"x").status_code == 415
    assert client.post(f"/api/sessions/{sid}/material", json={"filename": "a.txt", "data": "%%%"}).status_code == 400
    assert upload(client, "no-such-session", "a.txt", b"hi").status_code == 404


def run_first_two_turns(client, monkeypatch, sid):
    """Start the interview, answer the opener, and return (opener text, material passed to planning)."""
    seen = {}

    async def plan(spec, role, m=None):
        seen["material"] = m
        return gemini.fake.plan(spec)

    monkeypatch.setattr(gemini, "plan_interview", plan)
    with client.websocket_connect(f"/ws/{sid}") as ws:
        client.post(f"/api/sessions/{sid}/start")
        while (first := ws.receive_json())["type"] != "question":
            pass
        qid = first["data"]["question_id"]
        ws.send_json({"type": "answer_started", "data": {"question_id": qid}})
        ws.send_json({"type": "answer_done", "data": {"question_id": qid,
                                                       "transcript": "It covers flooding risk and a survey of 400 homes."}})
        while (ev := ws.receive_json())["type"] != "question":
            pass
        ws.send_json({"type": "end_session", "data": {}})
        while ws.receive_json()["type"] != "session_ended":
            pass
    return first["data"]["text"], seen.get("material")


def test_presentation_without_a_file_asks_what_it_covers_and_plans_from_the_answer(client, monkeypatch):
    sid = create(client, "Class Presentation")
    opener, m = run_first_two_turns(client, monkeypatch, sid)
    assert opener == lines.PRESENTATION_MATERIAL_LINE
    assert m is not None and m.spoken and "survey of 400 homes" in m.text


def test_presentation_with_a_file_skips_that_question_and_plans_from_the_file(client, monkeypatch):
    sid = create(client, "Class Presentation")
    assert upload(client, sid, "essay.docx", DOCX).status_code == 200
    opener, m = run_first_two_turns(client, monkeypatch, sid)
    assert opener == lines.CALIBRATION_LINE
    assert m is not None and not m.spoken and m.name == "essay.docx"


def test_other_interview_types_never_ask_about_material(client, monkeypatch):
    sid = create(client, "Behavioral")
    opener, m = run_first_two_turns(client, monkeypatch, sid)
    assert opener == lines.CALIBRATION_LINE and m is None


def test_the_material_goes_into_the_planning_prompt():
    from backend.brain import prompts
    from backend.brain.specs import get_spec
    spoken = material.from_speech("It covers flooding risk.")
    assert "THEIR DESCRIPTION" in prompts.plan_prompt(get_spec("presentation"), "Coastal housing", spoken)
    filed = material.from_upload("notes.txt", b"Sea levels rose 20 cm.")
    assert "Sea levels rose 20 cm." in prompts.plan_prompt(get_spec("presentation"), "Coastal housing", filed)
    pdf = material.from_upload("slides.pdf", b"%PDF-1.7")
    assert "attached above" in prompts.plan_prompt(get_spec("swe"), "Engineer", pdf)
