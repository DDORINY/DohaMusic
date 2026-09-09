from __future__ import annotations

import inspect
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

import backend.core.idempotency_completion as completion_module
from backend.core.idempotency_completion import (
    IDEMPOTENCY_RESULT_VERSION,
    MAX_IDEMPOTENCY_RESULT_PAYLOAD_BYTES,
    IdempotencyCompletionResult,
    IdempotencyResultType,
)
from backend.db.session import create_database_engine
from backend.models.idempotency_record import IdempotencyRecord
from backend.repositories.idempotency_repository import IdempotencyRepository


@pytest.fixture
def sessions(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'idempotency.db').as_posix()}")
    IdempotencyRecord.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _result(
    result_type: IdempotencyResultType = IdempotencyResultType.CLIP_SPLIT,
    *,
    revision: int = 6,
) -> IdempotencyCompletionResult:
    payloads = {
        IdempotencyResultType.WORKING_COMPOSITION_INITIALIZE: {
            "working_composition_id": str(uuid4())
        },
        IdempotencyResultType.CLIP_SPLIT: {
            "original_clip_id": str(uuid4()),
            "left_clip_id": str(uuid4()),
            "right_clip_id": str(uuid4()),
        },
        IdempotencyResultType.WORKING_COMPOSITION_CHECKOUT: {
            "working_composition_id": str(uuid4()),
            "base_composition_snapshot_id": str(uuid4()),
        },
        IdempotencyResultType.COMPOSITION_COMMIT: {"composition_snapshot_id": str(uuid4())},
        IdempotencyResultType.TRACK_CREATE: {"track_id": str(uuid4())},
        IdempotencyResultType.TRACK_DELETE: {"track_id": str(uuid4())},
        IdempotencyResultType.TRACK_RESTORE: {"track_id": str(uuid4())},
        IdempotencyResultType.CLIP_CREATE: {"clip_id": str(uuid4())},
        IdempotencyResultType.CLIP_COPY: {"clip_id": str(uuid4())},
        IdempotencyResultType.CLIP_GAIN_UPDATE: {"clip_id": str(uuid4())},
        IdempotencyResultType.TRACK_MIXER_UPDATE: {"track_id": str(uuid4())},
        IdempotencyResultType.MASTER_GAIN_UPDATE: {"working_composition_id": str(uuid4())},
        IdempotencyResultType.CLIP_FADE_UPDATE: {"clip_id": str(uuid4())},
        IdempotencyResultType.CLIP_LOOP_UPDATE: {"clip_id": str(uuid4())},
        IdempotencyResultType.CLIP_LOOP_RESTORE: {"clip_id": str(uuid4())},
        IdempotencyResultType.WORKING_HISTORY_UNDO: {"clip_id": str(uuid4())},
        IdempotencyResultType.WORKING_HISTORY_REDO: {"clip_id": str(uuid4())},
        IdempotencyResultType.CLIP_DELETE: {"clip_id": str(uuid4())},
        IdempotencyResultType.CLIP_RESTORE: {"clip_id": str(uuid4())},
        IdempotencyResultType.CLIP_UNSPLIT: {
            "original_clip_id": str(uuid4()),
            "left_clip_id": str(uuid4()),
            "right_clip_id": str(uuid4()),
        },
        IdempotencyResultType.CLIP_RESPLIT: {
            "original_clip_id": str(uuid4()),
            "left_clip_id": str(uuid4()),
            "right_clip_id": str(uuid4()),
        },
    }
    return IdempotencyCompletionResult.create(
        completed_revision=revision,
        result_type=result_type,
        result_payload=payloads[result_type],
    )


@pytest.mark.parametrize("result_type", list(IdempotencyResultType))
def test_each_allowlisted_result_type_has_a_typed_bounded_payload(
    result_type: IdempotencyResultType,
) -> None:
    result = _result(result_type)
    assert result.result_type is result_type
    assert result.result_version == 1
    assert result.completed_revision == 6
    assert result.payload_for_storage() == dict(result.result_payload)


def test_unknown_result_version_fails_closed() -> None:
    with pytest.raises(ValueError, match="IDEMPOTENCY_RESULT_VERSION_UNSUPPORTED"):
        IdempotencyCompletionResult(
            result_version=2,
            completed_revision=1,
            result_type=IdempotencyResultType.CLIP_DELETE,
            result_payload={"clip_id": str(uuid4())},
        )


def test_completion_result_roundtrip_replays_original_revision_and_split_ids(
    sessions,
) -> None:
    now = datetime.now(UTC)
    first = _result(revision=6)
    with sessions() as session, session.begin():
        repository = IdempotencyRepository(session)
        claim = repository.claim_with_result(
            scope="owner:project:working:clip-split",
            key="same-key",
            fingerprint="a" * 64,
            now=now,
        )
        assert claim.replayed is False
        repository.complete_with_result(
            claim.record,
            resource_type="composition_clip",
            resource_id=first.result_payload["original_clip_id"],
            response_status=200,
            completion_result=first,
        )

    with sessions.kw["bind"].begin() as connection:
        connection.execute(text("CREATE TABLE current_aggregate (revision INTEGER)"))
        connection.execute(text("INSERT INTO current_aggregate VALUES (8)"))

    with sessions() as session:
        record = session.scalar(select(IdempotencyRecord))
        assert record is not None
        assert record.completed_revision == 6
        assert record.result_type == "CLIP_SPLIT"
        assert record.result_version == IDEMPOTENCY_RESULT_VERSION
        assert record.result_payload == dict(first.result_payload)

        replay = IdempotencyRepository(session).claim_with_result(
            scope="owner:project:working:clip-split",
            key="same-key",
            fingerprint="a" * 64,
            now=now,
        )
        assert replay.replayed is True
        assert replay.completion_result == first
        assert replay.completion_result.completed_revision == 6
        assert session.scalar(text("SELECT revision FROM current_aggregate")) == 8
        assert dict(replay.completion_result.result_payload) == dict(first.result_payload)
        assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1


@pytest.mark.parametrize(
    "result_type",
    [
        IdempotencyResultType.TRACK_RESTORE,
        IdempotencyResultType.CLIP_RESTORE,
        IdempotencyResultType.CLIP_LOOP_RESTORE,
        IdempotencyResultType.CLIP_UNSPLIT,
        IdempotencyResultType.CLIP_RESPLIT,
    ],
)
def test_inverse_result_types_replay_first_identity_and_revision(
    sessions, result_type: IdempotencyResultType
) -> None:
    now = datetime.now(UTC)
    first = _result(result_type, revision=7)
    scope = f"inverse:{result_type.value}"
    with sessions() as session, session.begin():
        repository = IdempotencyRepository(session)
        claim = repository.claim_with_result(
            scope=scope,
            key="same-key",
            fingerprint="1" * 64,
            now=now,
        )
        repository.complete_with_result(
            claim.record,
            resource_type="working_composition_inverse",
            resource_id=next(iter(first.result_payload.values())),
            response_status=200,
            completion_result=first,
        )

    with sessions() as session:
        replay = IdempotencyRepository(session).claim_with_result(
            scope=scope,
            key="same-key",
            fingerprint="1" * 64,
            now=now,
        )
        assert replay.replayed is True
        assert replay.completion_result == first
        assert replay.completion_result.completed_revision == 7
        assert dict(replay.completion_result.result_payload) == dict(first.result_payload)


@pytest.mark.parametrize(
    "result_type",
    [
        IdempotencyResultType.WORKING_COMPOSITION_CHECKOUT,
        IdempotencyResultType.COMPOSITION_COMMIT,
    ],
)
def test_checkout_and_commit_replay_stored_identity_not_current_state(
    sessions, result_type: IdempotencyResultType
) -> None:
    now = datetime.now(UTC)
    first = _result(result_type, revision=4)
    scope = f"result:{result_type.value}"
    with sessions() as session, session.begin():
        repository = IdempotencyRepository(session)
        claim = repository.claim_with_result(
            scope=scope,
            key="key",
            fingerprint="b" * 64,
            now=now,
        )
        repository.complete_with_result(
            claim.record,
            resource_type="working_composition",
            resource_id=str(uuid4()),
            response_status=200,
            completion_result=first,
        )

    with sessions() as session:
        replay = IdempotencyRepository(session).claim_with_result(
            scope=scope,
            key="key",
            fingerprint="b" * 64,
            now=now,
        )
        assert replay.completion_result == first
        assert replay.completion_result.completed_revision == 4


def test_legacy_completed_record_remains_replayable_but_new_contract_fails_closed(
    sessions,
) -> None:
    now = datetime.now(UTC)
    with sessions() as session, session.begin():
        repository = IdempotencyRepository(session)
        claim = repository.claim(scope="legacy", key="key", fingerprint="c" * 64, now=now)
        repository.complete(
            claim.record,
            resource_type="workspace_job",
            resource_id=str(uuid4()),
            response_status=201,
        )

    with sessions() as session:
        legacy = IdempotencyRepository(session).claim(
            scope="legacy", key="key", fingerprint="c" * 64, now=now
        )
        assert legacy.replayed is True
        assert legacy.completion_result is None
        with pytest.raises(ValueError, match="IDEMPOTENCY_RESULT_REQUIRED"):
            IdempotencyRepository(session).claim_with_result(
                scope="legacy", key="key", fingerprint="c" * 64, now=now
            )


def test_partial_or_unknown_completion_result_fails_closed(sessions) -> None:
    now = datetime.now(UTC)
    with sessions() as session, session.begin():
        repository = IdempotencyRepository(session)
        claim = repository.claim(scope="partial", key="key", fingerprint="d" * 64, now=now)
        repository.complete(
            claim.record,
            resource_type="composition_clip",
            resource_id=str(uuid4()),
            response_status=200,
        )
        claim.record.completed_revision = 2

    with (
        sessions() as session,
        pytest.raises(ValueError, match="IDEMPOTENCY_RESULT_INCOMPLETE"),
    ):
        IdempotencyRepository(session).claim(
            scope="partial", key="key", fingerprint="d" * 64, now=now
        )


def test_inverse_fingerprint_and_in_progress_conflicts_are_preserved(sessions) -> None:
    now = datetime.now(UTC)
    with sessions() as session, session.begin():
        repository = IdempotencyRepository(session)
        for result_type in (
            IdempotencyResultType.TRACK_RESTORE,
            IdempotencyResultType.CLIP_RESTORE,
            IdempotencyResultType.CLIP_UNSPLIT,
            IdempotencyResultType.CLIP_RESPLIT,
        ):
            scope = f"conflict:{result_type.value}"
            repository.claim(scope=scope, key="key", fingerprint="e" * 64, now=now)
            with pytest.raises(ValueError, match="IDEMPOTENCY_IN_PROGRESS"):
                repository.claim(scope=scope, key="key", fingerprint="e" * 64, now=now)
            with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
                repository.claim(scope=scope, key="key", fingerprint="f" * 64, now=now)


def test_result_payload_schema_uuid_and_utf8_size_are_bounded(monkeypatch) -> None:
    with pytest.raises(ValueError, match="IDEMPOTENCY_RESULT_PAYLOAD_INVALID"):
        IdempotencyCompletionResult.create(
            completed_revision=1,
            result_type=IdempotencyResultType.TRACK_DELETE,
            result_payload={"track_id": "경로와 토큰은 저장하지 않음"},
        )
    with pytest.raises(ValueError, match="IDEMPOTENCY_RESULT_PAYLOAD_INVALID"):
        IdempotencyCompletionResult.create(
            completed_revision=1,
            result_type=IdempotencyResultType.TRACK_DELETE,
            result_payload={"track_id": str(uuid4()), "secret": str(uuid4())},
        )

    monkeypatch.setattr(completion_module, "MAX_IDEMPOTENCY_RESULT_PAYLOAD_BYTES", 1)
    with pytest.raises(ValueError, match="IDEMPOTENCY_RESULT_PAYLOAD_TOO_LARGE"):
        IdempotencyCompletionResult.create(
            completed_revision=1,
            result_type=IdempotencyResultType.TRACK_DELETE,
            result_payload={"track_id": str(uuid4())},
        )
    assert MAX_IDEMPOTENCY_RESULT_PAYLOAD_BYTES == 8_192


def test_domain_revision_and_completion_result_rollback_together(sessions) -> None:
    engine = sessions.kw["bind"]
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE revision_probe (revision INTEGER NOT NULL)"))
        connection.execute(text("INSERT INTO revision_probe VALUES (5)"))

    with (
        pytest.raises(RuntimeError, match="forced rollback"),
        sessions() as session,
        session.begin(),
    ):
        session.execute(text("UPDATE revision_probe SET revision = 6"))
        repository = IdempotencyRepository(session)
        claim = repository.claim_with_result(
            scope="atomic", key="key", fingerprint="0" * 64, now=datetime.now(UTC)
        )
        result = _result(revision=6)
        repository.complete_with_result(
            claim.record,
            resource_type="composition_clip",
            resource_id=result.result_payload["original_clip_id"],
            response_status=200,
            completion_result=result,
        )
        raise RuntimeError("forced rollback")

    with engine.connect() as connection:
        assert connection.scalar(text("SELECT revision FROM revision_probe")) == 5
        assert connection.scalar(text("SELECT count(*) FROM idempotency_records")) == 0


def test_idempotency_repository_does_not_commit_or_rollback() -> None:
    source = inspect.getsource(IdempotencyRepository)
    assert ".commit(" not in source
    assert ".rollback(" not in source
