"""Unit tests for z3rno_server.workers.ingest pure helpers (Phase B.1).

End-to-end coverage of the actual Celery task (running against a real
Postgres + working IngestPipeline) lives in
``test_api_ingest_integration.py`` (Task 37).
"""

from __future__ import annotations

import os
from unittest.mock import patch
from uuid import uuid4

import pytest

from z3rno_server.workers.ingest import (
    _build_post_ingest,
    _decode_input,
    _make_storage,
    _summary_to_dict,
    ingest_run,
)


@pytest.fixture(autouse=True)
def _disable_ingest_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test sets INGEST_ENABLED itself; never inherit a value."""
    monkeypatch.delenv("INGEST_ENABLED", raising=False)
    monkeypatch.delenv("DISTILL_ENABLED", raising=False)


class TestDecodeInput:
    def test_text(self) -> None:
        inp = _decode_input({"kind": "text", "text": "hello"})
        assert inp.kind == "text"
        assert inp.text == "hello"
        assert inp.content is None

    def test_url(self) -> None:
        inp = _decode_input({"kind": "url", "url": "https://example.com"})
        assert inp.kind == "url"
        assert inp.url == "https://example.com"

    def test_file_hex_round_trip(self) -> None:
        inp = _decode_input(
            {
                "kind": "file",
                "content_hex": "deadbeef",
                "filename": "x.bin",
                "content_type": "application/octet-stream",
            }
        )
        assert inp.kind == "file"
        assert inp.content == bytes.fromhex("deadbeef")
        assert inp.filename == "x.bin"

    def test_file_without_content_hex(self) -> None:
        inp = _decode_input({"kind": "file"})
        assert inp.content is None


class TestMakeStorage:
    def test_local(self, monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
        monkeypatch.setenv("STORAGE_LOCAL_DIR", str(tmp_path))
        from z3rno_server.config import Settings

        s = Settings()
        backend = _make_storage(s)
        assert backend.name == "local"

    def test_unsupported_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("STORAGE_BACKEND", "s3")
        from z3rno_server.config import Settings

        s = Settings()
        with pytest.raises(ValueError, match="unsupported STORAGE_BACKEND"):
            _make_storage(s)


class TestBuildPostIngest:
    def _settings(
        self,
        *,
        ingest_auto_distill: bool,
        distill_enabled: bool,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("INGEST_AUTO_DISTILL", "true" if ingest_auto_distill else "false")
        monkeypatch.setenv("DISTILL_ENABLED", "true" if distill_enabled else "false")
        from z3rno_server.config import Settings

        return Settings()

    def test_returns_none_when_auto_distill_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        s = self._settings(ingest_auto_distill=False, distill_enabled=True, monkeypatch=monkeypatch)
        cb = _build_post_ingest(s, org_id=uuid4(), agent_id=uuid4(), request_id=None)
        assert cb is None

    def test_returns_none_when_distill_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        s = self._settings(ingest_auto_distill=True, distill_enabled=False, monkeypatch=monkeypatch)
        cb = _build_post_ingest(s, org_id=uuid4(), agent_id=uuid4(), request_id=None)
        assert cb is None


class TestSummaryToDict:
    def test_round_trip(self) -> None:
        from z3rno_core.ingest import IngestRunSummary

        s = IngestRunSummary(
            job_id=uuid4(),
            status="completed",
            memory_ids=[uuid4()],
            skipped_existing=[],
            source_uri="file:///x",
            content_type="text/plain",
            filename="x.txt",
            file_size=10,
        )
        out = _summary_to_dict(s)
        assert isinstance(out["job_id"], str)
        assert out["status"] == "completed"
        assert isinstance(out["memory_ids"][0], str)
        assert out["distill_job_id"] is None
        assert out["error"] is None


class TestFlagOffRejection:
    def test_ingest_run_rejects_when_flag_off(self) -> None:
        os.environ.pop("INGEST_ENABLED", None)
        result = ingest_run.run(
            job_id=str(uuid4()),
            org_id=str(uuid4()),
            agent_id=str(uuid4()),
            payload={"kind": "text", "text": "hi"},
        )
        assert result["status"] == "rejected"
        assert result["reason"] == "ingest_disabled"


class TestFlagOnDispatchesForge:
    """Confirms the post_ingest hook calls forge_distill.apply_async with
    the new memory_ids — without actually running the full pipeline."""

    def test_post_ingest_dispatches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from z3rno_core.ingest import IngestRunSummary

        monkeypatch.setenv("INGEST_AUTO_DISTILL", "true")
        monkeypatch.setenv("DISTILL_ENABLED", "true")
        from z3rno_server.config import Settings

        s = Settings()
        org_id = uuid4()
        agent_id = uuid4()

        # Mock both the engine factory (so no DB connection is opened) AND
        # insert_distill_job + set_org_context (so the callback can run end-to-end).
        fake_engine = MagicMock()
        fake_conn = MagicMock()
        fake_conn.run_sync = AsyncMock(return_value=None)
        fake_engine.begin.return_value.__aenter__ = AsyncMock(return_value=fake_conn)
        fake_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        fake_engine.dispose = AsyncMock(return_value=None)

        with (
            patch("z3rno_server.workers.ingest._make_engine", return_value=fake_engine),
            patch(
                "z3rno_server.workers.ingest.insert_distill_job",
                new_callable=AsyncMock,
            ) as mock_insert,
            patch("z3rno_server.workers.ingest.forge_distill.apply_async") as mock_dispatch,
        ):
            mock_insert.return_value = None
            cb = _build_post_ingest(s, org_id=org_id, agent_id=agent_id, request_id="rid")
            assert cb is not None
            mid = uuid4()
            distill_id = asyncio.run(
                cb(
                    IngestRunSummary(
                        job_id=uuid4(),
                        status="completed",
                        memory_ids=[mid],
                    )
                )
            )

        assert distill_id is not None
        mock_insert.assert_called_once()
        mock_dispatch.assert_called_once()
        kwargs = mock_dispatch.call_args.kwargs["kwargs"]
        assert kwargs["org_id"] == str(org_id)
        assert kwargs["agent_id"] == str(agent_id)
        assert kwargs["memory_ids"] == [str(mid)]
        assert kwargs["request_id"] == "rid"

    def test_post_ingest_skips_empty_memory_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import asyncio

        from z3rno_core.ingest import IngestRunSummary

        monkeypatch.setenv("INGEST_AUTO_DISTILL", "true")
        monkeypatch.setenv("DISTILL_ENABLED", "true")
        from z3rno_server.config import Settings

        s = Settings()
        with patch("z3rno_server.workers.ingest.forge_distill.apply_async") as mock_dispatch:
            cb = _build_post_ingest(s, org_id=uuid4(), agent_id=uuid4(), request_id=None)
            assert cb is not None
            distill_id = asyncio.run(
                cb(IngestRunSummary(job_id=uuid4(), status="completed", memory_ids=[]))
            )

        assert distill_id is None
        mock_dispatch.assert_not_called()
