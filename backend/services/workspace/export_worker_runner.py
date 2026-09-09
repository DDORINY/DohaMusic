"""Production polling lifecycle for canonical Export Jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.repositories.workspace import JobRepository
from backend.services.workspace.export_worker_service import ExportWorkerService

logger = get_logger(__name__)


class ExportWorkerRunner:
    """Claim Export Jobs and delegate all Export semantics to ExportWorkerService."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        worker: ExportWorkerService,
        poll_interval_seconds: float,
        lease_duration: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        runner_id: str | None = None,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("Export worker poll interval must be positive")
        self._session_factory = session_factory
        self._worker = worker
        self._poll_interval_seconds = poll_interval_seconds
        self._lease_duration = lease_duration
        self._clock = clock
        self._runner_id = runner_id or f"export-{uuid4().hex}"
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="canonical-export-worker")
        logger.info("export_worker_runner_started runner_id=%s", self._runner_id)

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop.set()
        await asyncio.gather(task, return_exceptions=True)
        self._task = None
        logger.info("export_worker_runner_stopped runner_id=%s", self._runner_id)

    def run_once(self) -> bool:
        now = self._clock()
        with self._session_factory() as session, session.begin():
            repository = JobRepository(session)
            repository.recover_expired_claim(
                now=now,
                job_type="export",
                requeue=True,
            )
            claimed = repository.claim_next_job(
                claimed_by=self._runner_id,
                claim_token=uuid4(),
                now=now,
                lease_expires_at=now + self._lease_duration,
                job_type="export",
            )
            if claimed is None:
                return False
            job_id = claimed.job_id
            claim_token = claimed.claim_token
        if claim_token is None:
            raise RuntimeError("Export claim token is missing")
        logger.info("export_worker_claimed job_id=%s runner_id=%s", job_id, self._runner_id)
        self._worker.execute_owned_claim(
            job_id=job_id,
            claimed_by=self._runner_id,
            claim_token=claim_token,
        )
        logger.info("export_worker_terminal job_id=%s runner_id=%s", job_id, self._runner_id)
        return True

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                processed = await asyncio.to_thread(self.run_once)
            except Exception:  # noqa: BLE001 - isolate one failed Export Job
                logger.exception("export_worker_iteration_failed runner_id=%s", self._runner_id)
                processed = True
            if processed:
                continue
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._poll_interval_seconds,
                )
