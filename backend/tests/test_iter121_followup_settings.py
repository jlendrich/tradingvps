"""Iter121 — follow-up scans of the 1h bots are configurable per bot: Settings.followup_count (0–10) and followup_minutes (5–30), default 5 × 5 min."""
import inspect
from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
import funnel
from models import Settings, SettingsUpdate
from fx.models import FxSettings, FxSettingsUpdate
from tests.test_iter103_bybit_hardening import FakeEngine, _settings, _force_in_hours


def utc(h, m=0, s=0):
    return datetime(2026, 11, 18, h, m, s, tzinfo=timezone.utc).timestamp()


def hms(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M:%S")


def _seq(follow, n, start=utc(13, 59)):
    t, got = start, []
    for _ in range(n):
        t = funnel.next_trigger("1h", t, follow)
        got.append(hms(t))
    return got


class TestModels:
    @pytest.mark.parametrize("cls", [Settings, FxSettings])
    def test_defaults_keep_the_current_cycle(self, cls):
        s = cls()
        assert (s.followup_count, s.followup_minutes) == (5, 5) and funnel.follow_ups("1h", s) == funnel.FOLLOW_UPS["1h"] == (300, 5)

    @pytest.mark.parametrize("cls", [Settings, FxSettings])
    @pytest.mark.parametrize("count,minutes,fit", [(10, 5, 10), (10, 10, 5), (5, 15, 3), (3, 20, 2), (2, 30, 1), (0, 5, 0), (4, 10, 4)])
    def test_count_is_fitted_inside_the_hour(self, cls, count, minutes, fit):
        s = cls(followup_count=count, followup_minutes=minutes)
        assert s.followup_count == fit and s.followup_count * s.followup_minutes <= 55

    @pytest.mark.parametrize("cls", [SettingsUpdate, FxSettingsUpdate])
    def test_update_ranges(self, cls):
        assert cls(followup_count=0, followup_minutes=30).followup_count == 0 and cls().followup_count is None
        for bad in ({"followup_count": 11}, {"followup_count": -1}, {"followup_minutes": 4}, {"followup_minutes": 31}):
            with pytest.raises(ValidationError):
                cls(**bad)

    def test_old_stored_settings_get_the_defaults(self):
        s = Settings.model_validate({"timeframe": "1h", "symbols": ["BTCUSDT"]})
        assert (s.followup_count, s.followup_minutes) == (5, 5)


class TestFollowUps:
    @pytest.mark.parametrize("tf", ["1m", "5m", "15m", "30m", "4h", "1d"])
    def test_other_timeframes_ignore_the_settings(self, tf):
        s = Settings(followup_count=8, followup_minutes=5)
        assert funnel.follow_ups(tf, s) == (0, 0)
        t = funnel.next_trigger(tf, utc(14, 7, 30), funnel.follow_ups(tf, s))
        assert t == funnel.next_trigger(tf, utc(14, 7, 30)) and (t - 5) % funnel.TF_SECONDS[tf] == 0
        assert funnel.forming_minute(tf, utc(14, 10, 5), funnel.follow_ups(tf, s)) is None

    def test_settings_drive_the_1h_cycle(self):
        assert funnel.follow_ups("1h", _settings(timeframe="1h", followup_count=3, followup_minutes=10)) == (600, 3)
        assert funnel.follow_ups("1h", _settings(timeframe="1h", followup_count=0)) == (0, 0)
        assert funnel.follow_ups("1h") == (300, 5) and funnel.follow_ups("1h", object()) == (300, 5)

    @pytest.mark.parametrize("follow,expected", [
        ((600, 3), ["14:00:05", "14:10:05", "14:20:05", "14:30:05", "15:00:05", "15:10:05"]),
        ((300, 10), ["14:00:05", "14:05:05", "14:10:05", "14:15:05", "14:20:05", "14:25:05", "14:30:05", "14:35:05", "14:40:05", "14:45:05", "14:50:05", "15:00:05"]),
        ((1800, 1), ["14:00:05", "14:30:05", "15:00:05", "15:30:05"]),
        ((900, 3), ["14:00:05", "14:15:05", "14:30:05", "14:45:05", "15:00:05"]),
        ((0, 0), ["14:00:05", "15:00:05", "16:00:05"]),
        ((300, 5), ["14:00:05", "14:05:05", "14:10:05", "14:15:05", "14:20:05", "14:25:05", "15:00:05"]),
    ])
    def test_exact_sequences(self, follow, expected):
        assert _seq(follow, len(expected)) == expected

    def test_default_argument_is_unchanged(self):
        assert _seq(None, 7) == ["14:00:05", "14:05:05", "14:10:05", "14:15:05", "14:20:05", "14:25:05", "15:00:05"]

    @pytest.mark.parametrize("follow,now,minute", [((600, 3), utc(14, 10, 5), 10), ((600, 3), utc(14, 30, 5), 30), ((600, 3), utc(14, 5, 5), None), ((600, 3), utc(14, 31, 0), None),
                                                   ((0, 0), utc(14, 5, 5), None), ((1800, 1), utc(14, 30, 5), 30), ((300, 10), utc(14, 50, 5), 50), ((300, 10), utc(14, 0, 5), None)])
    def test_forming_minute_follows_the_settings(self, follow, now, minute):
        assert funnel.forming_minute("1h", now, follow) == minute


class _Eng:
    def __init__(self):
        self.logs = []

    async def log(self, level, text):
        self.logs.append(text)


class TestScheduleAndRun:
    @pytest.mark.asyncio
    async def test_schedule_uses_the_bot_settings_and_logs_changes(self, monkeypatch):
        monkeypatch.setattr(funnel.time, "time", lambda: utc(14, 7, 30))
        eng = _Eng()
        assert hms(await funnel.schedule(eng, "1h", _settings(timeframe="1h"))) == "14:10:05"
        assert hms(await funnel.schedule(eng, "1h", _settings(timeframe="1h"))) == "14:10:05" and len(eng.logs) == 1
        assert hms(await funnel.schedule(eng, "1h", _settings(timeframe="1h", followup_count=2, followup_minutes=15))) == "14:15:05"
        assert eng.logs[-1] == "Próximo escaneo de seguimiento de la vela 1h en curso: 14:15:05 UTC · cierre de vela + 2 seguimientos cada 15 min (hasta el minuto 30)"
        assert hms(await funnel.schedule(eng, "1h", _settings(timeframe="1h", followup_count=0))) == "15:00:05"
        assert eng.logs[-1] == "Próximo escaneo al cierre de vela 1h: 15:00:05 UTC" and len(eng.logs) == 3
        assert hms(await funnel.schedule(eng, "15m", _settings(timeframe="15m", followup_count=9))) == "14:15:05"
        assert eng.logs[-1] == "Próximo escaneo al cierre de vela 15m: 14:15:05 UTC"

    def test_engines_pass_their_settings(self):
        import engine
        from fx import engine_signals
        for mod in (engine, engine_signals):
            assert "await funnel.schedule(self, settings.timeframe, settings)" in inspect.getsource(mod)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("count,minutes,now,minute", [(3, 10, utc(14, 20, 5), 20), (3, 10, utc(14, 5, 5), None), (0, 5, utc(14, 5, 5), None), (5, 5, utc(14, 5, 5), 5)])
    async def test_run_marks_the_follow_up_scans_of_the_settings(self, monkeypatch, count, minutes, now, minute):
        await _force_in_hours(monkeypatch)
        monkeypatch.setattr(funnel.time, "time", lambda: now)
        seen = []
        monkeypatch.setattr(funnel, "live_candle", lambda df, i, tf, atr, price, mm: seen.append(mm) or None)

        async def s1(*a, **k):
            return {"candidates": [], "model": "fast", "ms": 1}
        monkeypatch.setattr(funnel, "stage1", s1)
        await funnel.run(FakeEngine(mtf=True, funnel_rich=True), _settings(symbols=["BTCUSDT"], timeframe="1h", followup_count=count, followup_minutes=minutes))
        assert seen == ([minute] if minute is not None else [])

    def test_cooldown_follows_the_interval(self):
        src = inspect.getsource(funnel.run)
        assert "min(DEEP_COOLDOWN_SEC, follow[0] - 30) if follow[1] else DEEP_COOLDOWN_SEC" in src
