"""Tests for the epoch generation admin feature."""

import json
import os
import queue
import sqlite3
import tempfile
import threading
import time

import pytest

# Ensure project root on path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.database import get_db, init_schema
from src.web import create_app
from src.web.services import epoch_service


@pytest.fixture
def app(tmp_path):
    """Create a test Flask app with a fresh temp database."""
    db_path = str(tmp_path / "test.db")
    # Initialize DB with schema
    conn = get_db(db_path)
    conn.close()

    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def authed_client(client, app):
    """Client with admin session pre-authenticated."""
    with client.session_transaction() as sess:
        sess["admin_authenticated"] = True
        sess["admin_user"] = "test-operator"
    return client


@pytest.fixture(autouse=True)
def reset_epoch_service():
    """Reset epoch_service module state between tests."""
    epoch_service._generation_running.clear()
    epoch_service._generation_result.clear()
    while not epoch_service._generation_log.empty():
        try:
            epoch_service._generation_log.get_nowait()
        except queue.Empty:
            break
    yield
    epoch_service._generation_running.clear()
    epoch_service._generation_result.clear()
    while not epoch_service._generation_log.empty():
        try:
            epoch_service._generation_log.get_nowait()
        except queue.Empty:
            break


class TestEpochPageAuth:
    """Test admin auth on epoch page."""

    def test_epoch_page_requires_auth(self, client):
        resp = client.get("/admin/epoch")
        assert resp.status_code == 302
        assert "/admin/login" in resp.headers["Location"]

    def test_epoch_page_with_auth(self, authed_client):
        resp = authed_client.get("/admin/epoch")
        assert resp.status_code == 200
        assert b"Epoch Management" in resp.data

    def test_generate_requires_auth(self, client):
        resp = client.post("/admin/epoch/generate")
        assert resp.status_code == 302

    def test_stream_requires_auth(self, client):
        resp = client.get("/admin/epoch/generate/stream")
        assert resp.status_code == 302


class TestEpochPageContent:
    """Test epoch page content rendering."""

    def test_shows_no_epoch(self, authed_client):
        resp = authed_client.get("/admin/epoch")
        assert resp.status_code == 200
        # Should show dash for no epoch
        assert b"\xe2\x80\x94" in resp.data

    def test_shows_backend_indicator(self, authed_client):
        resp = authed_client.get("/admin/epoch")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "backend-indicator" in html
        # Default should be DummyBackend
        assert "DummyBackend" in html

    def test_shows_generate_button(self, authed_client):
        resp = authed_client.get("/admin/epoch")
        html = resp.data.decode("utf-8")
        assert "Initialize New Epoch" in html


class TestGenerateTrigger:
    """Test POST /admin/epoch/generate."""

    def test_trigger_returns_202(self, authed_client, app):
        resp = authed_client.post("/admin/epoch/generate", data={
            "endgame_mode": "",
            "breach_type": "",
        })
        assert resp.status_code == 202
        data = json.loads(resp.data)
        assert data["status"] == "started"
        assert data["epoch_number"] == 1

        # Wait for generation to finish
        for _ in range(100):
            if not epoch_service.is_running():
                break
            time.sleep(0.1)

    def test_conflict_when_running(self, authed_client, app):
        # Manually set running flag
        epoch_service._generation_running.set()
        try:
            resp = authed_client.post("/admin/epoch/generate", data={})
            assert resp.status_code == 409
            data = json.loads(resp.data)
            assert "already in progress" in data["error"]
        finally:
            epoch_service._generation_running.clear()

    def test_generate_increments_epoch(self, authed_client, app):
        """When an epoch exists, new generation should be epoch_number + 1."""
        # Create an existing epoch
        db_path = app.config["MMUD_DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT OR REPLACE INTO epoch (id, epoch_number, start_date, end_date, "
            "endgame_mode, breach_type) VALUES (1, 3, '2026-01-01', '2026-01-31', "
            "'hold_the_line', 'heist')"
        )
        conn.commit()
        conn.close()

        resp = authed_client.post("/admin/epoch/generate", data={})
        data = json.loads(resp.data)
        assert data["epoch_number"] == 4

        # Wait for generation to finish
        for _ in range(100):
            if not epoch_service.is_running():
                break
            time.sleep(0.1)


class TestSSEStream:
    """Test SSE endpoint streaming."""

    def test_stream_sends_events(self, authed_client, app):
        """Trigger generation and verify SSE stream has log lines."""
        # Start generation
        resp = authed_client.post("/admin/epoch/generate", data={})
        assert resp.status_code == 202

        # Read from SSE stream
        stream_resp = authed_client.get("/admin/epoch/generate/stream")
        assert stream_resp.content_type.startswith("text/event-stream")

        # Collect events (stream is blocking, so read what's available)
        events = []
        for chunk in stream_resp.response:
            line = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            if line.startswith("data: "):
                data = json.loads(line[6:].strip())
                events.append(data)
                if data["type"] == "complete":
                    break

        # Should have at least some log events and a complete event
        log_events = [e for e in events if e["type"] == "log"]
        complete_events = [e for e in events if e["type"] == "complete"]

        assert len(log_events) > 0, "Should have log events"
        assert len(complete_events) == 1, "Should have exactly one complete event"

        # Complete event should have result
        result = complete_events[0]["result"]
        assert result["success"] is True
        assert result["rooms"] > 0


class TestEpochServiceUnit:
    """Unit tests for epoch_service module functions."""

    def test_is_running_default_false(self):
        assert epoch_service.is_running() is False

    def test_get_result_default_empty(self):
        assert epoch_service.get_result() == {}

    def test_start_returns_false_when_running(self):
        epoch_service._generation_running.set()
        try:
            result = epoch_service.start_generation("fake.db")
            assert result is False
        finally:
            epoch_service._generation_running.clear()

    def test_generation_completes(self, tmp_path):
        """Full generation via epoch_service produces valid results."""
        db_path = str(tmp_path / "gen.db")
        conn = get_db(db_path)
        conn.close()

        started = epoch_service.start_generation(
            db_path=db_path,
            epoch_number=1,
            endgame_mode="hold_the_line",
            breach_type="heist",
        )
        assert started is True

        # Wait for completion
        for _ in range(200):
            if not epoch_service.is_running():
                break
            time.sleep(0.05)

        assert epoch_service.is_running() is False

        result = epoch_service.get_result()
        assert result["success"] is True
        assert result["epoch_number"] == 1
        assert result["rooms"] > 0
        assert result["monsters"] > 0
        assert result["secrets"] > 0
        assert result["validation_errors"] == 0

    def test_log_queue_has_sentinel(self, tmp_path):
        """Log queue should end with None sentinel after generation."""
        db_path = str(tmp_path / "gen2.db")
        conn = get_db(db_path)
        conn.close()

        epoch_service.start_generation(db_path=db_path, epoch_number=1)

        # Drain the log queue
        messages = []
        sentinel_found = False
        for _ in range(500):
            try:
                msg = epoch_service._generation_log.get(timeout=1)
                if msg is None:
                    sentinel_found = True
                    break
                messages.append(msg)
            except queue.Empty:
                break

        assert sentinel_found, "Log queue should end with None sentinel"
        assert len(messages) > 0, "Should have log messages before sentinel"
        assert any("COMPLETE" in m for m in messages), "Should have completion message"

    def test_backend_detection(self, tmp_path):
        """Generation result should report DummyBackend."""
        db_path = str(tmp_path / "gen3.db")
        conn = get_db(db_path)
        conn.close()

        epoch_service.start_generation(db_path=db_path, epoch_number=1)

        # Wait for completion
        for _ in range(200):
            if not epoch_service.is_running():
                break
            time.sleep(0.05)

        result = epoch_service.get_result()
        assert result["backend"] == "DummyBackend"


class TestGenerationErrorHandling:
    """Test that generation errors are caught and logged."""

    def test_bad_db_path_reports_error(self):
        """Generation with nonexistent parent dir should fail gracefully."""
        started = epoch_service.start_generation(
            db_path="/nonexistent/path/to/db.sqlite",
            epoch_number=1,
        )
        assert started is True

        # Wait for completion
        for _ in range(100):
            if not epoch_service.is_running():
                break
            time.sleep(0.05)

        result = epoch_service.get_result()
        assert result.get("success") is False
        assert "error" in result
