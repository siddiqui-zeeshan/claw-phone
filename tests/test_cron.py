"""Tests for cron scheduler and executor."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from spare_paw.cron.scheduler import CronScheduler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app_state(**overrides) -> MagicMock:
    """Build a mock AppState with sensible defaults."""
    app_state = MagicMock()
    app_state.config = MagicMock()
    app_state.config.get = MagicMock(side_effect=lambda key, default=None: {
        "models.cron": None,
        "models.main_agent": "test/model",
        "agent.system_prompt": "You are a helpful assistant. Time: {current_time}",
        "agent.max_tool_iterations": 5,
        "telegram.owner_id": 12345,
        "_tool_registry": None,
    }.get(key, default))
    app_state.tool_registry = None
    app_state.router_client = AsyncMock()
    app_state.executor = None
    app_state.application = MagicMock()
    app_state.application.bot = AsyncMock()
    app_state.backend = AsyncMock()
    return app_state


# ---------------------------------------------------------------------------
# CronScheduler CRUD
# ---------------------------------------------------------------------------

def _make_scheduler(job_defaults: dict | None = None) -> CronScheduler:
    """Create a CronScheduler with a real AsyncIOScheduler started in paused mode.

    Must be called from within a running event loop (i.e. inside an async test).
    """
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    sched = CronScheduler(MagicMock())
    if job_defaults is not None:
        sched._scheduler = AsyncIOScheduler(job_defaults=job_defaults)
    else:
        sched._scheduler = AsyncIOScheduler()
    sched._scheduler.start(paused=True)
    return sched


class TestCronSchedulerCRUD:
    """Tests for add_job / remove_job / pause_job / resume_job."""

    @pytest.mark.asyncio
    async def test_add_job(self):
        scheduler = _make_scheduler()
        try:
            await scheduler.add_job("job-1", "*/5 * * * *", "do something")
            job = scheduler._scheduler.get_job("job-1")
            assert job is not None
        finally:
            scheduler._scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_remove_job(self):
        scheduler = _make_scheduler()
        try:
            await scheduler.add_job("job-2", "0 * * * *", "hourly task")
            await scheduler.remove_job("job-2")
            assert scheduler._scheduler.get_job("job-2") is None
        finally:
            scheduler._scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_remove_nonexistent_job_does_not_raise(self):
        """Removing a job that doesn't exist should not raise."""
        scheduler = _make_scheduler()
        try:
            await scheduler.remove_job("no-such-job")  # should not raise
        finally:
            scheduler._scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_pause_job(self):
        scheduler = _make_scheduler()
        try:
            await scheduler.add_job("job-3", "0 9 * * *", "morning task")
            await scheduler.pause_job("job-3")
            job = scheduler._scheduler.get_job("job-3")
            assert job is not None
            # A paused job has next_run_time == None
            assert job.next_run_time is None
        finally:
            scheduler._scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_resume_job(self):
        scheduler = _make_scheduler()
        try:
            await scheduler.add_job("job-4", "0 9 * * *", "morning task")
            await scheduler.pause_job("job-4")
            await scheduler.resume_job("job-4")
            job = scheduler._scheduler.get_job("job-4")
            assert job is not None
            # After resume, verify the call didn't raise.
        finally:
            scheduler._scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_add_job_raises_when_scheduler_not_running(self):
        """add_job raises RuntimeError when scheduler is None."""
        sched = CronScheduler(MagicMock())
        with pytest.raises(RuntimeError, match="not running"):
            await sched.add_job("x", "* * * * *", "p")

    @pytest.mark.asyncio
    async def test_pause_noop_when_scheduler_none(self):
        """pause_job is a no-op when scheduler hasn't been started."""
        sched = CronScheduler(MagicMock())
        await sched.pause_job("x")  # should not raise

    @pytest.mark.asyncio
    async def test_resume_noop_when_scheduler_none(self):
        sched = CronScheduler(MagicMock())
        await sched.resume_job("x")  # should not raise


# ---------------------------------------------------------------------------
# Missed-job semantics and overlap prevention
# ---------------------------------------------------------------------------


class TestCronJobRobustness:
    """Jobs must carry explicit misfire/coalesce/max_instances settings.

    Settings must be set per-job rather than inherited from whatever the
    scheduler's job_defaults happen to be — so a job missed while the
    process was down runs once on restart, and slow runs can't overlap.
    """

    @pytest.mark.asyncio
    async def test_add_job_sets_explicit_misfire_settings(self):
        # Hostile job_defaults: if add_job relies on scheduler defaults,
        # these leak through and the assertions below fail.
        scheduler = _make_scheduler(
            job_defaults={"misfire_grace_time": 30, "coalesce": False, "max_instances": 3}
        )
        try:
            await scheduler.add_job("job-robust", "*/5 * * * *", "do something")
            job = scheduler._scheduler.get_job("job-robust")
            assert job.misfire_grace_time == 3600
            assert job.coalesce is True
            assert job.max_instances == 1
        finally:
            scheduler._scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_start_schedules_db_jobs_with_explicit_misfire_settings(self):
        rows = [
            {
                "id": "db-job-1",
                "schedule": "59 23 31 12 *",
                "prompt": "yearly task",
                "model": None,
                "tools_allowed": None,
            }
        ]
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=rows)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_db = MagicMock()
        mock_db.execute = MagicMock(return_value=mock_ctx)

        scheduler = CronScheduler(_make_app_state())
        with patch("spare_paw.cron.scheduler.get_db", AsyncMock(return_value=mock_db)):
            await scheduler.start()
        try:
            job = scheduler._scheduler.get_job("db-job-1")
            assert job is not None
            assert job.misfire_grace_time == 3600
            assert job.coalesce is True
            assert job.max_instances == 1
        finally:
            await scheduler.stop()


# ---------------------------------------------------------------------------
# Explicit timezone
# ---------------------------------------------------------------------------


def _app_state_with_tz(tz_name: str) -> MagicMock:
    """Mock AppState whose config returns tz_name for cron.timezone."""
    app_state = _make_app_state()
    orig = app_state.config.get.side_effect
    app_state.config.get = MagicMock(
        side_effect=lambda key, default=None: (
            tz_name if key == "cron.timezone" else orig(key, default)
        )
    )
    return app_state


class TestCronTimezone:
    """CronTrigger must be built with an explicit timezone."""

    @pytest.mark.asyncio
    async def test_add_job_uses_configured_timezone(self):
        from zoneinfo import ZoneInfo

        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        sched = CronScheduler(_app_state_with_tz("Pacific/Kiritimati"))
        sched._scheduler = AsyncIOScheduler()
        sched._scheduler.start(paused=True)
        try:
            await sched.add_job("job-tz", "0 9 * * *", "morning task")
            job = sched._scheduler.get_job("job-tz")
            assert job.trigger.timezone == ZoneInfo("Pacific/Kiritimati")
        finally:
            sched._scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_add_job_falls_back_to_explicit_system_local_timezone(self):
        from datetime import datetime

        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        sched = CronScheduler(_make_app_state())  # no cron.timezone configured
        sched._scheduler = AsyncIOScheduler()
        sched._scheduler.start(paused=True)
        try:
            await sched.add_job("job-tz-local", "0 9 * * *", "morning task")
            job = sched._scheduler.get_job("job-tz-local")
            assert job.trigger.timezone == datetime.now().astimezone().tzinfo
        finally:
            sched._scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_invalid_timezone_falls_back_to_local(self):
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        sched = CronScheduler(_app_state_with_tz("Not/AZone"))
        sched._scheduler = AsyncIOScheduler()
        sched._scheduler.start(paused=True)
        try:
            await sched.add_job("job-tz-bad", "0 9 * * *", "morning task")
            job = sched._scheduler.get_job("job-tz-bad")
            assert job is not None  # bad config must not break scheduling
        finally:
            sched._scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# execute_cron — success path
# ---------------------------------------------------------------------------

class TestExecuteCronSuccess:
    """Test execute_cron sends the result to Telegram on success."""

    @pytest.mark.asyncio
    @patch(
        "spare_paw.cron.executor._claim_one_shot",
        new_callable=AsyncMock,
        return_value=(False, {}),
    )
    @patch("spare_paw.cron.executor._update_cron_result", new_callable=AsyncMock)
    @patch("spare_paw.cron.executor.run_tool_loop", new_callable=AsyncMock)
    async def test_sends_result_to_telegram(self, mock_tool_loop, mock_update, _mock_claim):
        from spare_paw.cron.executor import execute_cron

        mock_tool_loop.return_value = "Cron result text"
        app_state = _make_app_state()

        await execute_cron(app_state, "cron-1", "what is the weather?", None, None)

        # Tool loop was called
        mock_tool_loop.assert_awaited_once()

        # Backend sent the result
        app_state.backend.send_text.assert_awaited_once()
        sent_text = app_state.backend.send_text.call_args[0][0]
        assert "Cron result text" in sent_text

        # DB was updated with the result
        mock_update.assert_awaited_once()
        _ = mock_update.call_args[1] if mock_update.call_args[1] else {}
        positional = mock_update.call_args[0]
        # First positional arg is cron_id
        assert positional[0] == "cron-1"


# ---------------------------------------------------------------------------
# execute_cron — error path
# ---------------------------------------------------------------------------

class TestExecuteCronError:
    """Test execute_cron handles errors and sends warning message."""

    @pytest.mark.asyncio
    @patch(
        "spare_paw.cron.executor._claim_one_shot",
        new_callable=AsyncMock,
        return_value=(False, {}),
    )
    @patch("spare_paw.cron.executor._update_cron_result", new_callable=AsyncMock)
    @patch("spare_paw.cron.executor.run_tool_loop", new_callable=AsyncMock)
    async def test_sends_warning_on_failure(self, mock_tool_loop, mock_update, _mock_claim):
        from spare_paw.cron.executor import execute_cron

        mock_tool_loop.side_effect = RuntimeError("model exploded")
        app_state = _make_app_state()

        await execute_cron(app_state, "cron-err", "broken prompt", None, None)

        # Backend sent a warning message
        app_state.backend.send_text.assert_awaited_once()
        sent_text = app_state.backend.send_text.call_args[0][0]
        assert "cron-err" in sent_text
        assert "failed" in sent_text.lower() or "RuntimeError" in sent_text

        # DB was updated with the error
        mock_update.assert_awaited_once()
        positional = mock_update.call_args[0]
        assert positional[0] == "cron-err"
        # The error kwarg should be set
        assert "error" in (mock_update.call_args[1] or {}) or (
            len(positional) >= 3 and positional[2] is None  # result=None
        )

    @pytest.mark.asyncio
    @patch(
        "spare_paw.cron.executor._claim_one_shot",
        new_callable=AsyncMock,
        return_value=(False, {}),
    )
    @patch("spare_paw.cron.executor._update_cron_result", new_callable=AsyncMock)
    @patch("spare_paw.cron.executor.run_tool_loop", new_callable=AsyncMock)
    async def test_error_notification_failure_does_not_propagate(self, mock_tool_loop, mock_update, _mock_claim):  # noqa: E501
        """If sending the error notification itself fails, execute_cron still doesn't raise."""
        from spare_paw.cron.executor import execute_cron

        mock_tool_loop.side_effect = RuntimeError("model down")
        app_state = _make_app_state()
        # Make the backend raise when trying to send the warning
        app_state.backend.send_text = AsyncMock(
            side_effect=Exception("Telegram down")
        )

        # Should not raise even though both tool_loop and notification failed
        await execute_cron(app_state, "cron-x", "prompt", None, None)

        # DB update should still have been attempted
        mock_update.assert_awaited_once()


# ---------------------------------------------------------------------------
# One-shot crash safety (claim-then-run)
# ---------------------------------------------------------------------------


def _make_mock_db(row, rowcount: int = 1, events: list[str] | None = None) -> MagicMock:
    """Mock aiosqlite DB: fetchone returns `row`; DELETEs are recorded in `events`."""
    mock_cursor = AsyncMock()
    mock_cursor.fetchone = AsyncMock(return_value=row)
    mock_cursor.rowcount = rowcount

    async def _execute(sql, *args, **kwargs):
        if events is not None and "DELETE" in sql:
            events.append("delete")
        return mock_cursor

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(side_effect=_execute)
    mock_db.commit = AsyncMock()
    return mock_db


class TestClaimOneShot:
    """_claim_one_shot consumes the row of once=true crons before execution."""

    @pytest.mark.asyncio
    async def test_claims_and_deletes_once_cron(self):
        from spare_paw.cron.executor import _claim_one_shot

        mock_db = _make_mock_db({"metadata": '{"once": true}'})
        with patch("spare_paw.cron.executor.get_db", AsyncMock(return_value=mock_db)):
            once, metadata = await _claim_one_shot("cron-once-1")

        assert once is True
        assert metadata == {"once": True}
        delete_calls = [
            call for call in mock_db.execute.call_args_list
            if "DELETE" in str(call)
        ]
        assert len(delete_calls) == 1
        assert "cron-once-1" in str(delete_calls[0])

    @pytest.mark.asyncio
    async def test_non_once_cron_is_not_deleted(self):
        """A cron without once=true in metadata should NOT be deleted."""
        from spare_paw.cron.executor import _claim_one_shot

        mock_db = _make_mock_db({"metadata": '{"repeat": true}'})
        with patch("spare_paw.cron.executor.get_db", AsyncMock(return_value=mock_db)):
            once, metadata = await _claim_one_shot("cron-repeat")

        assert once is False
        assert metadata == {"repeat": True}
        assert "DELETE" not in str(mock_db.execute.call_args_list)

    @pytest.mark.asyncio
    async def test_missing_row_reports_already_claimed(self):
        from spare_paw.cron.executor import _claim_one_shot

        mock_db = _make_mock_db(None)
        with patch("spare_paw.cron.executor.get_db", AsyncMock(return_value=mock_db)):
            once, _metadata = await _claim_one_shot("cron-gone")

        assert once is None

    @pytest.mark.asyncio
    async def test_lost_delete_race_reports_already_claimed(self):
        """If the DELETE claims no row, another run already consumed the cron."""
        from spare_paw.cron.executor import _claim_one_shot

        mock_db = _make_mock_db({"metadata": '{"once": true}'}, rowcount=0)
        with patch("spare_paw.cron.executor.get_db", AsyncMock(return_value=mock_db)):
            once, _metadata = await _claim_one_shot("cron-raced")

        assert once is None


class TestOneShotClaimThenRun:
    """One-shot crons are consumed from the DB BEFORE their work executes.

    Deleting the row after execution means a crash after the work (or a
    restart mid-run) fires the job twice; claiming first makes that impossible.
    """

    @pytest.mark.asyncio
    @patch("spare_paw.cron.executor._update_cron_result", new_callable=AsyncMock)
    @patch("spare_paw.cron.executor.run_tool_loop", new_callable=AsyncMock)
    async def test_row_deleted_before_work_executes(self, mock_tool_loop, _mock_update):
        from spare_paw.cron.executor import execute_cron

        events: list[str] = []
        mock_db = _make_mock_db({"metadata": '{"once": true}'}, events=events)

        async def record_work(*args, **kwargs):
            events.append("work")
            return "one-shot result"

        mock_tool_loop.side_effect = record_work

        app_state = _make_app_state()
        app_state.scheduler = AsyncMock()

        with patch("spare_paw.cron.executor.get_db", AsyncMock(return_value=mock_db)):
            await execute_cron(app_state, "cron-once", "do it", None, None)

        assert "delete" in events and "work" in events, events
        assert events.index("delete") < events.index("work"), events

    @pytest.mark.asyncio
    @patch("spare_paw.cron.executor._update_cron_result", new_callable=AsyncMock)
    @patch("spare_paw.cron.executor.run_tool_loop", new_callable=AsyncMock)
    async def test_skipped_when_row_already_gone(self, mock_tool_loop, _mock_update):
        """If the cron row is already gone (consumed or deleted), do not execute."""
        from spare_paw.cron.executor import execute_cron

        mock_db = _make_mock_db(None)
        app_state = _make_app_state()
        app_state.scheduler = AsyncMock()

        with patch("spare_paw.cron.executor.get_db", AsyncMock(return_value=mock_db)):
            await execute_cron(app_state, "cron-gone", "do it", None, None)

        mock_tool_loop.assert_not_awaited()
        app_state.backend.send_text.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("spare_paw.cron.executor._update_cron_result", new_callable=AsyncMock)
    @patch("spare_paw.cron.executor.run_tool_loop", new_callable=AsyncMock)
    async def test_skipped_when_claim_loses_race(self, mock_tool_loop, _mock_update):
        """If the DELETE claims no row, another run owns the job — skip."""
        from spare_paw.cron.executor import execute_cron

        mock_db = _make_mock_db({"metadata": '{"once": true}'}, rowcount=0)
        app_state = _make_app_state()
        app_state.scheduler = AsyncMock()

        with patch("spare_paw.cron.executor.get_db", AsyncMock(return_value=mock_db)):
            await execute_cron(app_state, "cron-raced", "do it", None, None)

        mock_tool_loop.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("spare_paw.cron.executor._update_cron_result", new_callable=AsyncMock)
    @patch("spare_paw.cron.executor.run_tool_loop", new_callable=AsyncMock)
    async def test_scheduler_cleanup_runs_even_on_failure(self, mock_tool_loop, _mock_update):
        """The scheduler.remove_job cleanup stays in a finally block."""
        from spare_paw.cron.executor import execute_cron

        mock_db = _make_mock_db({"metadata": '{"once": true}'})
        mock_tool_loop.side_effect = RuntimeError("model exploded")

        app_state = _make_app_state()
        app_state.scheduler = AsyncMock()

        with patch("spare_paw.cron.executor.get_db", AsyncMock(return_value=mock_db)):
            await execute_cron(app_state, "cron-once-f", "do it", None, None)

        app_state.scheduler.remove_job.assert_awaited_once_with("cron-once-f")
