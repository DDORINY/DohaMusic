from __future__ import annotations

import hashlib
import wave
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.audio.export_analyzer import ExportAnalysisError
from backend.audio.export_quality import ExportQualityDecision
from backend.audio.working_preview_renderer import PreviewRenderError
from backend.models.workspace import ExportPublicationState
from backend.services.workspace.export_worker_service import ExportWorkerError, ExportWorkerService


class _Completion:
    def replay_completed(self, _job_id):
        return None

    def complete(self, request):
        return request


def _worker(*, analyzer=None) -> ExportWorkerService:
    return ExportWorkerService(
        lambda: None,
        artifacts=object(),
        renderer=object(),
        publications=object(),
        completion=_Completion(),
        analyzer=analyzer,
    )


def test_worker_hashes_rendered_wav_through_closed_binary_stream(tmp_path, monkeypatch) -> None:
    wav_path = tmp_path / "export.wav"
    with wave.open(str(wav_path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(48_000)
        output.writeframes(b"\0\0\0\0" * 48_000)
    expected = wav_path.read_bytes()

    class Publications:
        integrity = None

        def set_expected_integrity(self, **values):
            self.integrity = values

        def publish_or_recover(self, **_values):
            return None

    class Analyzer:
        def analyze(self, _path):
            return SimpleNamespace(
                quality=ExportQualityDecision(
                    integrated_loudness_lufs=Decimal("-14.00"),
                    true_peak_dbtp=Decimal("-1.00"),
                    loudness_passed=True,
                    true_peak_passed=True,
                )
            )

    class Completion:
        def replay_completed(self, _job_id):
            return None

        def complete(self, request):
            return request

    publications = Publications()
    worker = ExportWorkerService(
        lambda: None,
        artifacts=object(),
        renderer=object(),
        publications=publications,
        completion=Completion(),
        analyzer=Analyzer(),
    )
    frozen = SimpleNamespace(fingerprint="f" * 64, tracks=(), clips=(), master_gain_db=0)
    monkeypatch.setattr(
        worker,
        "_load",
        lambda *_args: (frozen, uuid4(), ExportPublicationState.INTENDED),
    )

    @contextmanager
    def rendered(*_args):
        yield SimpleNamespace(path=wav_path)

    monkeypatch.setattr(worker, "_render", rendered)
    worker.execute_owned_claim(job_id=uuid4(), claimed_by="worker", claim_token=uuid4())

    assert publications.integrity["sha256"] == hashlib.sha256(expected).hexdigest()
    assert publications.integrity["size_bytes"] == len(expected)
    with wav_path.open("rb") as reopened:
        assert reopened.read(4) == b"RIFF"
    wav_path.unlink()
    assert not wav_path.exists()


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (PreviewRenderError("render"), "EXPORT_RENDER_FAILED"),
        (ExportAnalysisError("analysis"), "EXPORT_QUALITY_ANALYSIS_FAILED"),
        (ExportWorkerError("EXPORT_QUALITY_GATE_FAILED"), "EXPORT_QUALITY_GATE_FAILED"),
        (ExportWorkerError("EXPORT_INTEGRITY_FAILED"), "EXPORT_INTEGRITY_FAILED"),
        (RuntimeError("private publication detail"), "EXPORT_PUBLICATION_FAILED"),
    ],
)
def test_worker_failure_finishes_owned_claim(monkeypatch, error, expected_code) -> None:
    worker = _worker()
    finished = []
    monkeypatch.setattr(
        worker, "_execute_owned_claim", lambda **_values: (_ for _ in ()).throw(error)
    )
    monkeypatch.setattr(worker, "_claim_disposition", lambda *_values: "owned")
    monkeypatch.setattr(worker, "_finish_terminal", lambda *args, **values: finished.append(values))

    with pytest.raises(type(error)):
        worker.execute_owned_claim(job_id=uuid4(), claimed_by="worker", claim_token=uuid4())

    assert finished == [{"cancelled": False, "error_code": expected_code}]


def test_worker_cancellation_finishes_cancelled(monkeypatch) -> None:
    worker = _worker()
    finished = []
    monkeypatch.setattr(
        worker,
        "_execute_owned_claim",
        lambda **_values: (_ for _ in ()).throw(ExportWorkerError("EXPORT_CANCELLED")),
    )
    monkeypatch.setattr(worker, "_claim_disposition", lambda *_values: "cancelled")
    monkeypatch.setattr(worker, "_finish_terminal", lambda *args, **values: finished.append(values))

    with pytest.raises(ExportWorkerError, match="EXPORT_CANCELLED"):
        worker.execute_owned_claim(job_id=uuid4(), claimed_by="worker", claim_token=uuid4())

    assert finished == [{"cancelled": True, "error_code": None}]


@pytest.mark.parametrize("disposition", ["stale"])
def test_worker_stale_claim_does_not_write_terminal(monkeypatch, disposition) -> None:
    worker = _worker()
    finished = []
    monkeypatch.setattr(
        worker,
        "_execute_owned_claim",
        lambda **_values: (_ for _ in ()).throw(RuntimeError("stale")),
    )
    monkeypatch.setattr(worker, "_claim_disposition", lambda *_values: disposition)
    monkeypatch.setattr(worker, "_finish_terminal", lambda *args, **values: finished.append(values))

    with pytest.raises(ExportWorkerError, match="EXPORT_STALE_CLAIM"):
        worker.execute_owned_claim(job_id=uuid4(), claimed_by="worker", claim_token=uuid4())

    assert finished == []


def test_worker_happy_path_does_not_write_failure_terminal(monkeypatch) -> None:
    worker = _worker()
    result = object()
    monkeypatch.setattr(worker, "_execute_owned_claim", lambda **_values: result)
    monkeypatch.setattr(
        worker,
        "_finish_terminal",
        lambda *_args, **_values: pytest.fail("unexpected terminal failure"),
    )

    assert (
        worker.execute_owned_claim(job_id=uuid4(), claimed_by="worker", claim_token=uuid4())
        is result
    )


def test_worker_terminal_replay_is_read_only(monkeypatch) -> None:
    replay = object()

    class Completion(_Completion):
        def replay_completed(self, _job_id):
            return replay

    worker = ExportWorkerService(
        lambda: pytest.fail("unexpected database session"),
        artifacts=object(),
        renderer=object(),
        publications=object(),
        completion=Completion(),
        analyzer=object(),
    )
    monkeypatch.setattr(
        worker,
        "_execute_owned_claim",
        lambda **_values: pytest.fail("unexpected render/publication execution"),
    )
    monkeypatch.setattr(
        worker,
        "_finish_terminal",
        lambda *_args, **_values: pytest.fail("unexpected terminal mutation"),
    )

    assert (
        worker.execute_owned_claim(job_id=uuid4(), claimed_by="ignored", claim_token=uuid4())
        is replay
    )
