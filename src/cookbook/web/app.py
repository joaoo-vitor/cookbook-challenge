"""The web page's server: a few routes around the functions the command line already uses.

The server keeps no recipe. The page holds the transcript and the recipe and sends them back
with each request, so a request is a plain function call: structure this text, check this
recipe, change this recipe. Only the reading of a document is a job, because it is slow.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cookbook.extract.structure import StructureError, check_recipe, structure_recipe
from cookbook.extract.transcribe import transcribe_pdf
from cookbook.extract.transcript import Transcript, build_transcript
from cookbook.manipulate.engine import manipulate
from cookbook.manipulate.scaling import NeedsBaseServings
from cookbook.manipulate.vegan import VeganError
from cookbook.models import Recipe

from .jobs import Jobs, describe_pages

MAX_UPLOAD = 20 * 1024 * 1024  # bytes
STATIC = Path(__file__).parent / "static"


@dataclass
class Readers:
    """The model clients: two readers for images and one that fills the schema."""

    primary: object
    second: object | None = None


def default_readers() -> Readers:
    """Gemini, set up from .env exactly as the command line does it."""
    from cookbook.llm import DEFAULT_FALLBACK_MODELS, DEFAULT_SECOND_MODEL, make_client

    raw = os.environ.get("GEMINI_FALLBACK_MODELS")
    fallbacks = [m.strip() for m in raw.split(",") if m.strip()] if raw else list(DEFAULT_FALLBACK_MODELS)
    second = os.environ.get("GEMINI_SECOND_MODEL") or DEFAULT_SECOND_MODEL
    return Readers(make_client("gemini", None, fallbacks), make_client("gemini", second, fallbacks))


class StructureRequest(BaseModel):
    transcript: dict  # as given by the job
    edits: dict[str, str] = {}  # the person's version of the lines they corrected or confirmed


class CheckRequest(BaseModel):
    transcript: dict
    recipe: Recipe


class ManipulateRequest(BaseModel):
    recipe: Recipe
    servings: int | None = None
    base_servings: int | None = None
    vegan: bool = False


def create_app(make_readers=default_readers) -> FastAPI:
    """The app. `make_readers` is called when a model is first needed, so the page opens without a key."""
    @asynccontextmanager
    async def lifespan(_app):
        yield
        _app.state.jobs.close()  # the uploaded files are removed when the server stops

    app = FastAPI(title="Cookbook Challenge", lifespan=lifespan)
    state: dict = {"readers": None}

    def readers() -> Readers:
        if state["readers"] is None:
            try:
                state["readers"] = make_readers()
            except (RuntimeError, ValueError) as e:  # for example: no API key
                raise HTTPException(503, f"the models are not set up: {e}") from e
        return state["readers"]

    def read(path: Path) -> dict:
        r = readers()
        pages = transcribe_pdf(path, r.primary, second=r.second)
        transcript = build_transcript(pages)
        return {"transcript": transcript.to_json(), "pages": describe_pages(pages),
                "models": {"primary": f"{r.primary.name}/{r.primary.model}",
                           "second": f"{r.second.name}/{r.second.model}" if r.second else None}}

    jobs = Jobs(read)
    app.state.jobs = jobs

    @app.post("/api/jobs")
    async def start_job(file: UploadFile = File(...)):
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, f"the file is larger than {MAX_UPLOAD // 1024 // 1024} MB")
        if not data:
            raise HTTPException(400, "the file is empty")
        try:
            job = jobs.start(data, file.filename or "upload")
        except ValueError as e:
            raise HTTPException(400, "that is not a PDF, an image or a text file") from e
        return {"job_id": job.id, "filename": job.filename, "kind": job.kind}

    @app.get("/api/jobs/{job_id}")
    def job_state(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        return {"status": job.status, "message": job.message, "kind": job.kind,
                "filename": job.filename, "result": job.result}

    @app.get("/api/jobs/{job_id}/page/{page}.jpg")
    def page_image(job_id: str, page: int):
        from cookbook.ingest.pdf import render_page

        job = jobs.get(job_id)
        if job is None or job.kind == "text":
            raise HTTPException(404, "no image for this page")
        try:
            return Response(render_page(job.path, page), media_type="image/jpeg")
        except (IndexError, ValueError) as e:
            raise HTTPException(404, "no such page") from e

    @app.post("/api/structure")
    def structure(req: StructureRequest):
        try:
            transcript = Transcript.from_json(req.transcript).with_edits(req.edits)
        except (ValueError, TypeError, KeyError) as e:
            raise HTTPException(400, str(e)[:300]) from e
        try:
            result = structure_recipe(transcript, readers().primary)
        except StructureError as e:
            raise HTTPException(502, str(e)) from e
        return {"transcript": transcript.to_json(), "recipe": result.recipe,
                "issues": [i.model_dump() for i in result.issues]}

    @app.post("/api/check")
    def check(req: CheckRequest):
        recipe, issues = check_recipe(req.recipe, Transcript.from_json(req.transcript))
        return {"recipe": recipe, "issues": [i.model_dump() for i in issues]}

    @app.post("/api/manipulate")
    def change(req: ManipulateRequest):
        if not req.vegan and req.servings is None:
            raise HTTPException(400, "choose a manipulation: make it vegan, or give the number of servings wanted")
        llm = readers().primary if req.vegan else None  # only the vegan rule needs a model
        try:
            result = manipulate(req.recipe, servings=req.servings, base_servings=req.base_servings,
                                vegan=req.vegan, llm=llm)
        except VeganError as e:
            raise HTTPException(502, str(e)) from e
        except (NeedsBaseServings, ValueError) as e:
            raise HTTPException(422, str(e)) from e
        except Exception as e:  # noqa: BLE001 - a provider error while the model wrote the new steps
            raise HTTPException(502, f"{type(e).__name__}: {str(e)[:300]}") from e
        return result

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app

