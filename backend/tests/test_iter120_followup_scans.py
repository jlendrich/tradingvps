"""Iter120 — independent edge-case tests for the 1h follow-up scans:
  (A) next_trigger boundary conditions (exactly at a mark, 1 s before, midnight UTC roll-over, long idles).
  (B) forming_minute behaviour for non-1h timeframes and at every boundary.
  (C) live_candle degrades to None when the exchange has not yet published the new candle (frame still ends on the last closed one).
  (D) stage-1 fingerprint includes the forming minute → fresh stage1 call on every follow-up even when the price did not move.
  (E) funnel_opened skip is keyed by closed candle_ts → next hour re-evaluates automatically.
  (F) cooldown is 270 s inside 1h (min(300, 300-30)); 15m keeps the 300 s cooldown; stored Signal.context has live_candle.
"""
import asyncio
from datetime import datetime, timezone
import pandas as pd
import pytest

import funnel
from tests.test_iter103_bybit_hardening import FakeEngine, _settings, _force_in_hours


def utc(h, m=0, s=0, day=18, month=11, year=2026):
    return datetime(year, month, day, h, m, s, tzinfo=timezone.utc).timestamp()


def hms(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M:%S")


HOUR = utc(14)


# ---------------- A · next_trigger boundary conditions ----------------
class TestNextTriggerEdges:
    def test_exactly_at_mark_returns_next_mark(self):
        """`now` exactly at HH:MM:05 must yield the NEXT mark, never the current one (strict > comparison)."""
        assert hms(funnel.next_trigger("1h", utc(14, 0, 5))) == "14:05:05"
        assert hms(funnel.next_trigger("1h", utc(14, 5, 5))) == "14:10:05"
        assert hms(funnel.next_trigger("1h", utc(14, 25, 5))) == "15:00:05"

    def test_one_second_before_mark(self):
        for mm in (0, 5, 10, 15, 20, 25):
            t = funnel.next_trigger("1h", utc(14, mm, 4))
            assert hms(t) == f"14:{mm:02d}:05" and t - utc(14, mm, 4) == 1

    def test_long_idle_between_25_and_next_hour(self):
        """Any instant between HH:25:05 (exclusive) and HH+1:00:05 must wait for the next close scan."""
        for hh, mm, ss in [(14, 25, 6), (14, 30, 0), (14, 45, 0), (14, 59, 59)]:
            assert hms(funnel.next_trigger("1h", utc(hh, mm, ss))) == "15:00:05"

    def test_midnight_utc_rollover(self):
        """23:25:05 → 00:00:05 next-day, continuing the 5-min marks."""
        t, got = utc(23, 25, 5, day=17), []
        for _ in range(7):
            t = funnel.next_trigger("1h", t)
            got.append(datetime.fromtimestamp(t, timezone.utc).strftime("%d %H:%M:%S"))
        assert got[0].startswith("18 00:00:05")
        assert [g[3:] for g in got] == ["00:00:05", "00:05:05", "00:10:05", "00:15:05", "00:20:05", "00:25:05", "01:00:05"]

    @pytest.mark.parametrize("tf,step", [("1m", 60), ("5m", 300), ("15m", 900), ("30m", 1800), ("4h", 14400), ("1d", 86400)])
    def test_non_1h_timeframes_only_close_plus_5(self, tf, step):
        """No follow-ups outside the 1h mode: next_trigger is always (close + 5 s) and never produces a forming minute."""
        t, prev = utc(0, 0, 0), None
        for _ in range(5):
            t = funnel.next_trigger(tf, t)
            assert (int(t) - 5) % step == 0
            assert funnel.forming_minute(tf, t) is None
            if prev is not None:
                assert t - prev == step
            prev = t


# ---------------- B · forming_minute boundary conditions ----------------
class TestFormingMinute:
    @pytest.mark.parametrize("now,minute", [
        (utc(14, 0, 0), None), (utc(14, 0, 5), None), (utc(14, 4, 59), None),
        (utc(14, 5, 0), 5), (utc(14, 5, 5), 5), (utc(14, 9, 59), 9),
        (utc(14, 10, 0), 10), (utc(14, 24, 59), 24), (utc(14, 25, 0), 25), (utc(14, 25, 59), 25),
        (utc(14, 26, 0), None), (utc(14, 26, 5), None), (utc(14, 59, 59), None),
    ])
    def test_1h_window_5_to_25(self, now, minute):
        assert funnel.forming_minute("1h", now) == minute

    @pytest.mark.parametrize("tf", ["1m", "5m", "15m", "30m", "4h", "1d"])
    def test_other_timeframes_return_none_always(self, tf):
        for t in [utc(14, 0, 5), utc(14, 5, 5), utc(14, 15, 5), utc(14, 30, 0), utc(14, 45, 30)]:
            assert funnel.forming_minute(tf, t) is None


# ---------------- C · live_candle fallback when candle not yet published ----------------
def _hour_df(n=250, price=100.0, last_open=None, extra_forming=True):
    """Closed 1h candles ending at open_ts `last_open` (default: 13:00 close → last closed row opens at 13:00).
    When `extra_forming=True` an extra row is appended for the forming 14:00 candle."""
    last_open = last_open if last_open is not None else HOUR - 3600
    rows = n + (1 if extra_forming else 0)
    df = pd.DataFrame({
        "ts": [int((last_open - (n - 1 - k) * 3600) * 1000) for k in range(rows)],
        "open": price, "high": price + 0.5, "low": price - 0.5, "close": price, "volume": 100.0,
        "ema20": price, "ema50": price, "ema200": price, "rsi": 55.0, "macd_hist": 0.1,
        "adx": 25.0, "atr": 1.0, "vol_ratio": 2.0,
    })
    return df


class TestLiveCandleFallback:
    def test_frame_without_forming_row_degrades_to_none(self):
        """The 1h exchange has not yet published the 14:00 candle: frame ends at 13:00 (closed). live_candle must be None."""
        df = _hour_df(extra_forming=False)
        assert funnel.live_candle(df, len(df) - 1, "1h", 1.0, 100.0, 5, utc(14, 5, 5)) is None

    def test_last_row_belongs_to_wrong_hour(self):
        df = _hour_df(extra_forming=False)
        assert funnel.live_candle(df, len(df) - 2, "1h", 1.0, 100.0, 5, utc(15, 5, 5)) is None

    def test_zero_atr_returns_none(self):
        df = _hour_df()
        assert funnel.live_candle(df, len(df) - 2, "1h", 0.0, 100.0, 5, utc(14, 5, 5)) is None

    @pytest.mark.asyncio
    async def test_run_degrades_gracefully_when_forming_row_missing(self, monkeypatch):
        """If the broker frame is one candle short, the follow-up scan still runs and uses closed-candle metrics only."""
        await _force_in_hours(monkeypatch)
        monkeypatch.setattr(funnel.time, "time", lambda: utc(14, 15, 5))
        eng = FakeEngine(mtf=True, funnel_rich=True)
        eng.market.tickers["BTCUSDT"] = {"price": 100.0}

        async def frame(symbol, tf, limit=300):
            if tf == "1h":
                return _hour_df(extra_forming=False), True
            return await FakeEngine.funnel_frame(eng, symbol, tf, limit)
        eng.funnel_frame = frame

        captured = {}

        async def s1(kind, t, rows, limit, usage_kind):
            captured.update(rows)
            return {"candidates": [], "model": "fast", "ms": 1}
        monkeypatch.setattr(funnel, "stage1", s1)
        await funnel.run(eng, _settings(symbols=["BTCUSDT"], timeframe="1h"))
        m = captured["BTCUSDT"]
        assert m.get("live") is None                       # degraded: closed-candle only
        assert eng.analysis["BTCUSDT"]["live_candle"] is None


# ---------------- D · stage-1 fingerprint depends on the forming minute ----------------
class TestStage1Fingerprint:
    @pytest.mark.asyncio
    async def test_fingerprint_includes_minute_so_each_follow_up_calls_stage1(self, monkeypatch):
        """Even with zero price movement the follow-up must re-call stage1 (the forming candle changed)."""
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        eng.market.tickers["BTCUSDT"] = {"price": 100.0}

        async def frame(symbol, tf, limit=300):
            if tf == "1h":
                return _hour_df(), True
            return await FakeEngine.funnel_frame(eng, symbol, tf, limit)
        eng.funnel_frame = frame
        s1_calls = []

        async def s1(kind, t, rows, limit, usage_kind):
            s1_calls.append(hms(funnel.time.time()))
            return {"candidates": [], "model": "fast", "ms": 1}
        monkeypatch.setattr(funnel, "stage1", s1)
        for mm in (0, 5, 10, 15, 20, 25):
            monkeypatch.setattr(funnel.time, "time", lambda mm=mm: utc(14, mm, 5))
            await funnel.run(eng, _settings(symbols=["BTCUSDT"], timeframe="1h"))
        assert s1_calls == ["14:00:05", "14:05:05", "14:10:05", "14:15:05", "14:20:05", "14:25:05"]


# ---------------- E · funnel_opened skip is keyed by closed candle_ts ----------------
class TestFunnelOpenedSkipAcrossHours:
    @pytest.mark.asyncio
    async def test_next_hour_re_evaluates_with_new_candle_ts(self, monkeypatch):
        """After a trade opens at 14:10, the pair is skipped the rest of hour 14. At 15:00:05 the closed candle_ts
        changes (13:00 → 14:00) so funnel_opened[symbol] no longer matches → the pair is evaluated again."""
        await _force_in_hours(monkeypatch)

        calls = []

        async def s2(kind, symbol, tf, cand, m, *a, **kw):
            calls.append(hms(funnel.time.time()))
            return {"decision": "BUY", "confidence": 80, "rule": None, "reasoning": "ok",
                    "model": "deep", "ms": 1, "sl": m["price"] - 1.2 * m["atr"], "tp": None}
        monkeypatch.setattr(funnel, "stage2", s2)
        monkeypatch.setattr(funnel, "pullback_block", lambda m, side: None)
        monkeypatch.setattr(funnel, "rr_block", lambda *a, **k: None)

        eng = FakeEngine(mtf=True, funnel_rich=True)
        eng.market.tickers["BTCUSDT"] = {"price": 100.0}

        def make_frame(reference_hour):
            def frame_fn(symbol, tf, limit=300):
                async def _inner():
                    if tf == "1h":
                        return _hour_df(last_open=reference_hour - 3600), True
                    return await FakeEngine.funnel_frame(eng, symbol, tf, limit)
                return _inner()
            return frame_fn
        eng.funnel_frame = make_frame(HOUR)

        async def s1(kind, t, rows, limit, usage_kind):
            return {"candidates": [{"symbol": s, "side": "long", "reason": "x"} for s in rows], "model": "fast", "ms": 1}
        monkeypatch.setattr(funnel, "stage1", s1)

        # Hour 14 — open at :10:05, then :15/:20/:25 must all be skipped even if the trade closes
        monkeypatch.setattr(funnel.time, "time", lambda: utc(14, 10, 5))
        await funnel.run(eng, _settings(symbols=["BTCUSDT"], timeframe="1h"))
        assert calls == ["14:10:05"]
        assert eng.funnel_opened == {"BTCUSDT": int((HOUR - 3600) * 1000)}
        eng._open_positions.clear()
        for mm in (15, 20, 25):
            monkeypatch.setattr(funnel.time, "time", lambda mm=mm: utc(14, mm, 5))
            await funnel.run(eng, _settings(symbols=["BTCUSDT"], timeframe="1h"))
        assert calls == ["14:10:05"]

        # Hour 15 — the broker frame now has a new last-closed candle (14:00) → funnel_opened key no longer matches
        eng.funnel_frame = make_frame(HOUR + 3600)
        monkeypatch.setattr(funnel.time, "time", lambda: utc(15, 0, 5))
        await funnel.run(eng, _settings(symbols=["BTCUSDT"], timeframe="1h"))
        assert calls == ["14:10:05", "15:00:05"]


# ---------------- F · cooldown + stored Signal context ----------------
class TestCooldownAndSignalContext:
    def test_1h_cooldown_is_270s_follow_up_safe(self):
        """Cooldown must be shorter than the 5-min spacing of follow-ups so none is swallowed."""
        import inspect
        src = inspect.getsource(funnel.run)
        assert "min(DEEP_COOLDOWN_SEC, follow[0] - 30)" in src
        # Effective cooldown: min(300, 300-30) = 270
        assert min(funnel.DEEP_COOLDOWN_SEC, funnel.FOLLOW_UPS["1h"][0] - 30) == 270

    def test_15m_keeps_the_standard_300s_cooldown(self):
        assert "15m" not in funnel.FOLLOW_UPS
        assert funnel.DEEP_COOLDOWN_SEC == 300

    @pytest.mark.asyncio
    async def test_signal_context_stores_live_candle_on_follow_up_entry(self, monkeypatch):
        """When the follow-up produces a BUY, the stored Signal.context.funnel.live_candle holds the forming candle."""
        await _force_in_hours(monkeypatch)
        monkeypatch.setattr(funnel.time, "time", lambda: utc(14, 15, 5))

        async def s2(kind, symbol, tf, cand, m, *a, **kw):
            return {"decision": "BUY", "confidence": 80, "rule": None, "reasoning": "ok",
                    "model": "deep", "ms": 1, "sl": m["price"] - 1.2 * m["atr"], "tp": None}
        monkeypatch.setattr(funnel, "stage2", s2)
        monkeypatch.setattr(funnel, "pullback_block", lambda m, side: None)
        monkeypatch.setattr(funnel, "rr_block", lambda *a, **k: None)

        eng = FakeEngine(mtf=True, funnel_rich=True)
        eng.market.tickers["BTCUSDT"] = {"price": 100.0}

        async def frame(symbol, tf, limit=300):
            if tf == "1h":
                return _hour_df(), True
            return await FakeEngine.funnel_frame(eng, symbol, tf, limit)
        eng.funnel_frame = frame

        async def s1(kind, t, rows, limit, usage_kind):
            return {"candidates": [{"symbol": "BTCUSDT", "side": "long", "reason": "rechazo"}], "model": "fast", "ms": 1}
        monkeypatch.setattr(funnel, "stage1", s1)

        await funnel.run(eng, _settings(symbols=["BTCUSDT"], timeframe="1h"))
        docs = eng.db.signals.docs
        assert docs and docs[-1]["ai_decision"] == "APROBADA"
        live = docs[-1]["context"]["funnel"]["live_candle"]
        assert live is not None and live["label"] == "vela 1h en curso (minuto 15/60)"
