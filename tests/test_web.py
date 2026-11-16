"""Tests for the web server. The models are replaced by fakes, so nothing is called over the network."""

import json
import time

import pymupdf
import pytest
from fastapi.testclient import TestClient

from cookbook.extract.transcript import Note, Transcript
from cookbook.llm import LLMResult
from cookbook.web.app import Readers, create_app
from test_validate import LINES, _recipe


class FakeReader:
    """Reads an image as fixed text and fills the schema with a fixed answer."""

    def __init__(self, model, text="", answer=None):
        self.name, self.model, self.text, self.answer, self.structured = "fake", model, text, answer, 0

    def transcribe(self, image, prompt):
        return LLMResult(self.text, self.model, 1, 1, 0.0)

    def structure(self, text, prompt, schema):
        self.structured += 1
        return LLMResult(self.answer, self.model, 1, 1, 0.0)


READ = ("Rindergulasch fuer vier Personen\n500 g Rindfleisch und 2 Zwiebeln\netwas Paprika und Salz\n"
        "in einem Topf anbraten\ndie Zwiebeln dazugeben und schmoren lassen\nam Ende saure Sahne dazu")


def _client(primary=None, second=None):
    primary = primary or FakeReader("m1", READ, _recipe().model_dump_json())
    second = second or FakeReader("m2", READ.replace("2 Zwiebeln", "3 Zwiebeln"))
    app = create_app(lambda: Readers(primary, second))
    return TestClient(app), primary


def _png() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 600), False)
    pix.set_rect(pix.irect, (255, 255, 255))
    return pix.tobytes("png")


def _wait(client, job_id):
    for _ in range(100):
        state = client.get(f"/api/jobs/{job_id}").json()
        if state["status"] != "reading":
            return state
        time.sleep(0.05)
    raise AssertionError("the job did not finish")


def _upload(client, data, name="recipe.bin"):
    r = client.post("/api/jobs", files={"file": (name, data)})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_page_is_served():
    client, _ = _client()
    r = client.get("/")
    assert r.status_code == 200 and "Cookbook Challenge" in r.text


def test_a_text_file_is_read_without_calling_a_model():
    client, primary = _client()
    job = _upload(client, "\n".join(LINES).encode("utf-8"), "gulasch.txt")
    assert job["kind"] == "text"
    state = _wait(client, job["job_id"])
    assert state["status"] == "done"
    lines = state["result"]["transcript"]["lines"]
    assert lines["p1:L1"] == "Rindergulasch" and len(lines) == len(LINES)
    assert state["result"]["pages"][0]["comparisons"] == []  # nothing was compared: nothing was read


def test_an_image_is_read_by_two_models_and_the_difference_is_reported():
    client, _ = _client()
    job = _upload(client, _png(), "photo.png")
    assert job["kind"] == "image"
    result = _wait(client, job["job_id"])["result"]
    [cmp] = result["pages"][0]["comparisons"]
    assert 0.8 < cmp["agreement"] < 1
    [flag] = cmp["flags"]
    assert (flag["kind"], flag["severity"]) == ("number", "high")
    # the transcript carries both readings of the doubtful word, so the page can underline it
    [note] = result["transcript"]["uncertain"][flag["line"]]
    assert (note["primary"], note["secondary"]) == (flag["primary"], flag["secondary"])
    assert {note["primary"], note["secondary"]} == {"2", "3"}
    assert result["models"] == {"primary": "fake/m1", "second": "fake/m2"}


def test_the_original_page_can_be_shown():
    client, _ = _client()
    job_id = _upload(client, _png(), "photo.png")["job_id"]
    _wait(client, job_id)
    r = client.get(f"/api/jobs/{job_id}/page/1.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    assert client.get(f"/api/jobs/{job_id}/page/9.jpg").status_code == 404


def test_a_file_that_is_not_a_document_is_refused():
    client, _ = _client()
    r = client.post("/api/jobs", files={"file": ("x.pdf", b"\x00\x01\x02binary")})
    assert r.status_code == 400
    assert client.post("/api/jobs", files={"file": ("x.txt", b"")}).status_code == 400


def test_an_upload_that_is_too_large_is_refused(monkeypatch):
    monkeypatch.setattr("cookbook.web.app.MAX_UPLOAD", 10)
    client, _ = _client()
    assert client.post("/api/jobs", files={"file": ("x.txt", b"a" * 11)}).status_code == 413


def test_an_unknown_job_is_a_404():
    client, _ = _client()
    assert client.get("/api/jobs/nope").status_code == 404


def test_a_failing_reader_makes_the_job_an_error_with_a_short_message():
    class Broken(FakeReader):
        def transcribe(self, image, prompt):
            raise RuntimeError("quota exceeded")

    client, _ = _client(primary=Broken("m1"))
    state = _wait(client, _upload(client, _png())["job_id"])
    assert state["status"] == "error" and "quota exceeded" in state["message"]


def _transcript_json(uncertain=True):
    t = Transcript()
    t.lines = {f"p1:L{n}": text for n, text in enumerate(LINES, start=1)}
    if uncertain:
        t.uncertain["p1:L2"] = [Note("high", "number: one reading has '4', the other '5'", "number", "4", "5")]
    return t.to_json()


def test_structure_uses_the_persons_lines_and_clears_their_doubt():
    client, primary = _client()
    r = client.post("/api/structure", json={"transcript": _transcript_json(),
                                            "edits": {"p1:L2": LINES[1]}})  # confirmed as it is
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recipe"]["title"] == "Rindergulasch" and primary.structured == 1
    assert "p1:L2" not in body["transcript"]["uncertain"] and body["transcript"]["human_checked"] == ["p1:L2"]
    assert "uncertain_reading" not in [i["code"] for i in body["issues"]]


def test_without_a_confirmation_the_doubt_reaches_the_recipe_issues():
    client, _ = _client()
    body = client.post("/api/structure", json={"transcript": _transcript_json()}).json()
    assert "uncertain_reading" in [i["code"] for i in body["issues"]]


def test_structure_refuses_edits_to_lines_that_do_not_exist():
    client, _ = _client()
    r = client.post("/api/structure", json={"transcript": _transcript_json(), "edits": {"p9:L1": "x"}})
    assert r.status_code == 400 and "p9:L1" in r.json()["detail"]


def test_a_recipe_that_does_not_parse_is_a_502_not_a_crash():
    client, _ = _client(primary=FakeReader("m1", READ, "not json"))
    r = client.post("/api/structure", json={"transcript": _transcript_json()})
    assert r.status_code == 502


def test_check_refreshes_the_issues_of_an_edited_recipe_without_a_model():
    client, primary = _client()
    recipe = _recipe()
    recipe.ingredients[0].quantity = 600  # the person typed 600, the text says 500
    r = client.post("/api/check", json={"transcript": _transcript_json(False), "recipe": recipe.model_dump()})
    assert r.status_code == 200
    assert "quantity_mismatch" in [i["code"] for i in r.json()["issues"]] and primary.structured == 0
    fixed = _recipe()
    ok = client.post("/api/check", json={"transcript": _transcript_json(False), "recipe": fixed.model_dump()}).json()
    assert [i for i in ok["issues"] if i["severity"] == "high"] == []


def test_manipulate_scales_the_recipe_and_returns_the_log():
    client, _ = _client()
    r = client.post("/api/manipulate", json={"recipe": _recipe().model_dump(), "servings": 8})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["base_servings"], body["target_servings"], body["base_is_estimate"]) == (4, 8, False)
    beef = next(i for i in body["recipe"]["ingredients"] if i["id"] == "i1")
    assert beef["quantity"] == 1000
    assert any(c["kind"] == "changed" for c in body["changes"])


def test_manipulate_says_when_the_base_is_missing_and_accepts_one():
    client, _ = _client()
    recipe = _recipe()
    recipe.servings.value = None
    r = client.post("/api/manipulate", json={"recipe": recipe.model_dump(), "servings": 8})
    assert r.status_code == 422 and "servings" in r.json()["detail"]
    ok = client.post("/api/manipulate", json={"recipe": recipe.model_dump(), "servings": 8, "base_servings": 4})
    assert ok.status_code == 200 and ok.json()["base_servings"] == 4


def _plan_json():
    swap = {"id": "i1", "category": "meat", "substitute_name": "Seitan", "substitute_en": "seitan", "reason": "beef"}
    cream = {"id": "i5", "category": "dairy", "substitute_name": "Hafersahne", "substitute_en": "plant cream", "reason": "cream"}
    return json.dumps({"swaps": [swap, cream], "steps": [
        {"n": 1, "text": "Den Seitan in Würfel schneiden"}, {"n": 4, "text": "am Ende Hafersahne dazu"}]})


def test_vegan_swaps_the_ingredients_with_the_model_and_returns_the_log():
    client, primary = _client(primary=FakeReader("m1", READ, _plan_json()))
    r = client.post("/api/manipulate", json={"recipe": _recipe().model_dump(), "vegan": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rules"] == ["make_vegan"] and body["target_servings"] is None
    names = {i["id"]: i["name"] for i in body["recipe"]["ingredients"]}
    assert names["i1"] == "Seitan" and names["i5"] == "Hafersahne"
    assert primary.structured == 1
    assert any(c["kind"] == "warning" and c["severity"] == "medium" for c in body["changes"])  # the meat note


def test_vegan_and_servings_can_be_asked_together():
    client, _ = _client(primary=FakeReader("m1", READ, _plan_json()))
    r = client.post("/api/manipulate", json={"recipe": _recipe().model_dump(), "vegan": True, "servings": 8})
    assert r.status_code == 200 and r.json()["rules"] == ["make_vegan", "scale_servings"]
    assert r.json()["recipe"]["ingredients"][0]["quantity"] == 1000


def test_scaling_alone_calls_no_model_and_an_empty_request_is_refused():
    client, primary = _client()
    assert client.post("/api/manipulate", json={"recipe": _recipe().model_dump(), "servings": 8}).status_code == 200
    assert primary.structured == 0
    assert client.post("/api/manipulate", json={"recipe": _recipe().model_dump()}).status_code == 400


def test_render_returns_the_recipe_as_markdown_without_a_model():
    client, primary = _client()
    issue = {"code": "x", "severity": "high", "message": "the amount is doubtful", "refers_to": "i1", "lines": []}
    r = client.post("/api/render", json={"recipe": _recipe().model_dump(), "issues": [issue]})
    assert r.status_code == 200
    md = r.json()["markdown"]
    assert md.startswith("# Rindergulasch") and "- 500 g Rindfleisch ⚠" in md and "the amount is doubtful" in md
    assert primary.structured == 0


def test_render_shows_what_the_manipulations_did():
    client, _ = _client()
    done = client.post("/api/manipulate", json={"recipe": _recipe().model_dump(), "servings": 8}).json()
    r = client.post("/api/render", json={"recipe": done["recipe"], "changes": done["changes"]})
    assert "umgerechnet von 4" in r.json()["markdown"] and "- 1000 g Rindfleisch" in r.json()["markdown"]


def test_render_refuses_a_malformed_recipe():
    client, _ = _client()
    assert client.post("/api/render", json={"recipe": {"ingredients": "no"}}).status_code == 422


def test_an_unreadable_vegan_answer_is_a_502_and_a_missing_key_a_503():
    client, _ = _client(primary=FakeReader("m1", READ, "not json"))
    assert client.post("/api/manipulate", json={"recipe": _recipe().model_dump(), "vegan": True}).status_code == 502

    def no_key():
        raise RuntimeError("GEMINI_API_KEY is not set")

    r = TestClient(create_app(no_key)).post("/api/manipulate", json={"recipe": _recipe().model_dump(), "vegan": True})
    assert r.status_code == 503


def test_the_page_opens_without_a_key_and_the_error_comes_when_a_model_is_needed():
    def no_key():
        raise RuntimeError("GEMINI_API_KEY is not set")

    client = TestClient(create_app(no_key))
    assert client.get("/").status_code == 200
    r = client.post("/api/structure", json={"transcript": _transcript_json()})
    assert r.status_code == 503 and "GEMINI_API_KEY" in r.json()["detail"]


def test_transcript_survives_the_round_trip_through_json():
    t = Transcript.from_json(_transcript_json())
    assert Transcript.from_json(t.to_json()) == t
    assert t.uncertain["p1:L2"][0].primary == "4"


def test_with_edits_marks_changed_and_confirmed_lines_but_not_untouched_ones():
    t = Transcript.from_json(_transcript_json())
    out = t.with_edits({"p1:L2": "Für 5 Personen nimmt man 500 g Rindfleisch, 2 Zwiebeln,", "p1:L1": "Rindergulasch"})
    assert out.human_checked == {"p1:L2"}  # L1 was sent back unchanged and was not in doubt
    assert out.lines["p1:L2"].startswith("Für 5") and "p1:L2" not in out.uncertain
    assert t.lines["p1:L2"] == LINES[1] and "p1:L2" in t.uncertain  # the original is not touched
    with pytest.raises(ValueError):
        t.with_edits({"p9:L1": "x"})
