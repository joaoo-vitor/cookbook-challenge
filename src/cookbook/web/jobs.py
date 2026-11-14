"""Reading a document takes a while (two models per page), so it runs as a job in a thread.

The page starts a job, then asks for its state every second or so. Jobs live in memory and are
forgotten when the server stops; only the last few are kept, with their uploaded file (the page
image is rendered from it). Nothing else is stored on the server.
"""

from __future__ import annotations

import dataclasses
import shutil
import tempfile
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

KEEP_JOBS = 5  # older jobs and their files are removed when more are started


@dataclass
class Job:
    id: str
    path: Path
    filename: str
    kind: str  # "pdf", "image" or "text"
    status: str = "reading"  # "reading", "done" or "error"
    message: str = "reading the document with two models"
    result: dict | None = None


def describe_pages(pages) -> list[dict]:
    """What the page needs to know about each page's reading: how the readers agreed, and any warning."""
    return [{
        "page": p.page, "strategy": p.strategy.value, "source": p.source, "unreliable": p.unreliable,
        "warnings": p.warnings,
        "comparisons": [{"label": c.label, "agreement": round(c.agreement, 3), "unreliable": c.unreliable,
                         "reordered": c.reordered, "flags": [dataclasses.asdict(f) for f in c.flags]}
                        for c in p.comparisons],
    } for p in pages]


@dataclass
class Jobs:
    work: object  # a function (path) -> result dict; it may raise
    folder: Path = field(default_factory=lambda: Path(tempfile.mkdtemp(prefix="cookbook-web-")))
    _jobs: "OrderedDict[str, Job]" = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def start(self, data: bytes, filename: str) -> Job:
        """Save the upload and start reading it. Raises ValueError if it is not a PDF, image or text."""
        from cookbook.ingest.pdf import detect_kind

        job_id = uuid.uuid4().hex[:12]
        path = self.folder / job_id
        path.mkdir()
        file = path / "upload"  # never the name the browser sent
        file.write_bytes(data)
        try:
            kind = detect_kind(file)  # by the first bytes, not by the name
        except ValueError:
            shutil.rmtree(path, ignore_errors=True)
            raise
        file = file.rename(path / ("upload" + _suffix(kind, data)))
        job = Job(job_id, file, Path(filename).name[:120], kind)
        with self._lock:
            self._jobs[job_id] = job
            while len(self._jobs) > KEEP_JOBS:
                _, old = self._jobs.popitem(last=False)
                shutil.rmtree(old.path.parent, ignore_errors=True)
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _run(self, job: Job) -> None:
        try:
            job.result = self.work(job.path)
            job.status, job.message = "done", "done"
        except Exception as e:  # noqa: BLE001 - shown to the person as a short message, not a traceback
            job.status, job.message = "error", f"{type(e).__name__}: {str(e)[:300]}"

    def close(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)


_IMAGE_SUFFIX = ((b"\x89PNG", ".png"), (b"\xff\xd8\xff", ".jpg"), (b"GIF8", ".gif"),
                 (b"II*\x00", ".tif"), (b"MM\x00*", ".tif"), (b"RIFF", ".webp"))


def _suffix(kind: str, data: bytes) -> str:
    """The extension PyMuPDF needs to open the file, from what the file is (not what it was called)."""
    if kind != "image":
        return {"pdf": ".pdf", "text": ".txt"}[kind]
    return next((s for magic, s in _IMAGE_SUFFIX if data.startswith(magic)), ".png")
