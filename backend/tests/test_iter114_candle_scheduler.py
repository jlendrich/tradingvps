"""Iter114 — the funnel scan follows the candle close of each bot's timeframe (+5 s, UTC), always evaluates closed candles and adapts the
higher-timeframe context (15m → 4H/1H · 1h → 1D/4H)."""
import asyncio
import inspect
from datetime import datetime, timezone
import pandas as pd
import pytest
import funnel
from tests.test_iter103_bybit_hardening import FakeEngine, _settings, _force_in_hours


def utc(h, m=0, s=0, day=18):
    return datetime(2026, 11, day, h, m, s, tzinfo=timezone.utc).timestamp()


def hms(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M:%S")


class TestNextTrigger:
    def test_delay_is_5_seconds(self):
        assert funnel.TRIGGER_DELAY_SEC == 5 and not hasattr(funnel, "TRIGGER_MIN")

    @pytest.mark.parametrize("tf,now,expected,wait", [
        ("15m", utc(14, 7, 30), "14:15:05", 455), ("15m", utc(14, 0, 0), "14:00:05", 5), ("15m", utc(14, 0, 5), "14:15:05", 900), ("15m", utc(14, 44, 59), "14:45:05", 6),
        ("15m", utc(14, 45, 6), "15:00:05", 899),
        ("1h", utc(14, 7, 30), "14:10:05", 155), ("1h", utc(14, 15, 5), "14:20:05", 300), ("1h", utc(14, 59, 59), "15:00:05", 6), ("1h", utc(15, 0, 5), "15:05:05", 300),
        ("1h", utc(14, 25, 5), "15:00:05", 2100), ("1h", utc(14, 25, 4), "14:25:05", 1), ("1h", utc(14, 40), "15:00:05", 1205), ("1h", utc(14, 0, 0), "14:00:05", 5),
        ("4h", utc(14, 7, 30), "16:00:05", 6755), ("4h", utc(0, 0, 0), "00:00:05", 5), ("4h", utc(3, 59, 59), "04:00:05", 6), ("4h", utc(20, 0, 5), "00:00:05", 14400),
        ("5m", utc(14, 7, 30), "14:10:05", 155), ("30m", utc(14, 7, 30), "14:30:05", 1355), ("1m", utc(14, 7, 30), "14:08:05", 35),
    ])
    def test_exact_wait(self, tf, now, expected, wait):
        t = funnel.next_trigger(tf, now)
        assert hms(t) == expected and t - now == wait

    def test_15m_marks(self):
        t, got = utc(14, 0, 0), []
        for _ in range(5):
            t = funnel.next_trigger("15m", t)
            got.append(hms(t))
        assert got == ["14:00:05", "14:15:05", "14:30:05", "14:45:05", "15:00:05"]

    def test_1h_close_plus_five_follow_ups_then_next_hour(self):
        """Exact 1h sequence: close scan at :00:05, follow-ups every 5 min up to :25:05, then nothing until the next close."""
        assert funnel.FOLLOW_UPS == {"1h": (300, 5)}
        t, got = utc(13, 59, 0), []
        for _ in range(14):
            t = funnel.next_trigger("1h", t)
            got.append(hms(t))
        assert got == ["14:00:05", "14:05:05", "14:10:05", "14:15:05", "14:20:05", "14:25:05",
                       "15:00:05", "15:05:05", "15:10:05", "15:15:05", "15:20:05", "15:25:05", "16:00:05", "16:05:05"]

    def test_1h_sequence_across_midnight(self):
        t, got = utc(23, 24, 0, day=17), []
        for _ in range(4):
            t = funnel.next_trigger("1h", t)
            got.append(hms(t))
        assert got == ["23:25:05", "00:00:05", "00:05:05", "00:10:05"]

    @pytest.mark.parametrize("tf", ["1m", "5m", "15m", "30m", "4h", "1d"])
    def test_only_1h_has_follow_ups(self, tf):
        step, t = funnel.TF_SECONDS[tf], utc(0, 0, 0)
        for _ in range(6):
            nxt = funnel.next_trigger(tf, t)
            assert (nxt - 5) % step == 0 and funnel.forming_minute(tf, nxt) is None
            t = nxt

    @pytest.mark.parametrize("now,minute", [(utc(14, 0, 5), None), (utc(14, 4, 59), None), (utc(14, 5, 5), 5), (utc(14, 10, 5), 10), (utc(14, 15, 5), 15),
                                            (utc(14, 20, 5), 20), (utc(14, 25, 5), 25), (utc(14, 25, 59), 25), (utc(14, 26, 0), None), (utc(14, 45), None)])
    def test_forming_minute_marks_the_follow_up_scans(self, now, minute):
        assert funnel.forming_minute("1h", now) == minute and funnel.forming_minute("15m", now) is None

    def test_4h_utc_marks(self):
        t, got = utc(23, 0, 0, day=17), []
        for _ in range(6):
            t = funnel.next_trigger("4h", t)
            got.append(hms(t))
        assert got == ["00:00:05", "04:00:05", "08:00:05", "12:00:05", "16:00:05", "20:00:05"]

    def test_default_is_15m(self):
        assert funnel.next_trigger(now=utc(14, 7)) == funnel.next_trigger("15m", utc(14, 7))


class _Eng:
    def __init__(self):
        self.logs = []

    async def log(self, level, text):
        self.logs.append((level, text))


class TestSchedule:
    @pytest.mark.asyncio
    async def test_logs_on_start_and_on_timeframe_change_only(self, monkeypatch):
        monkeypatch.setattr(funnel.time, "time", lambda: utc(14, 7, 30))
        eng = _Eng()
        assert hms(await funnel.schedule(eng, "15m")) == "14:15:05"
        assert eng.logs == [("INFO", "Próximo escaneo al cierre de vela 15m: 14:15:05 UTC")]
        await funnel.schedule(eng, "15m")
        assert len(eng.logs) == 1
        assert hms(await funnel.schedule(eng, "1h")) == "14:10:05"
        assert eng.logs[-1] == ("INFO", "Próximo escaneo de seguimiento de la vela 1h en curso: 14:10:05 UTC · cierre de vela + 5 seguimientos cada 5 min (hasta el minuto 25)")
        assert len(eng.logs) == 2
        assert hms(await funnel.schedule(eng, "4h")) == "16:00:05" and eng.logs[-1][1].endswith("vela 4h: 16:00:05 UTC")

    def test_both_engines_schedule_with_their_own_timeframe(self):
        import engine
        from fx import engine_signals
        for mod in (engine, engine_signals):
            src = inspect.getsource(mod)
            assert "await funnel.schedule(self, settings.timeframe)" in src and "next_trigger()" not in src

    @pytest.mark.asyncio
    async def test_scan_loop_recalculates_without_restart(self, monkeypatch):
        """Changing the timeframe (update_settings sets scan_event) wakes the loop, which reschedules with the new timeframe."""
        import engine as crypto
        monkeypatch.setattr(funnel.time, "time", lambda: utc(14, 7, 30))
        real_sleep = asyncio.sleep
        monkeypatch.setattr(crypto.asyncio, "sleep", lambda s: real_sleep(0))

        class Loop(_Eng):
            def __init__(self):
                super().__init__()
                self.tf, self.scan_event, self.scans, self.next_scan_ts = "15m", asyncio.Event(), [], None

            async def get_settings(self):
                return _settings(timeframe=self.tf)

            async def scan_once(self, settings):
                self.scans.append(settings.timeframe)
        eng = Loop()
        task = asyncio.create_task(crypto.TradingEngine._scan_loop(eng))
        for _ in range(20):
            await real_sleep(0)
        assert eng.scans == ["15m"] and hms(eng.next_scan_ts) == "14:15:05"
        eng.tf = "1h"
        eng.scan_event.set()
        for _ in range(20):
            await real_sleep(0)
        task.cancel()
        assert eng.scans == ["15m", "1h"] and hms(eng.next_scan_ts) == "14:10:05"
        assert [t for _, t in eng.logs] == ["Próximo escaneo al cierre de vela 15m: 14:15:05 UTC",
                                            "Próximo escaneo de seguimiento de la vela 1h en curso: 14:10:05 UTC · cierre de vela + 5 seguimientos cada 5 min (hasta el minuto 25)"]

    @pytest.mark.asyncio
    async def test_scan_loop_follows_the_1h_sequence(self, monkeypatch):
        """The loop scans at every mark of the hour and then sleeps until the next close."""
        import engine as crypto
        clock = {"t": utc(13, 59, 0)}
        monkeypatch.setattr(funnel.time, "time", lambda: clock["t"])
        real_sleep = asyncio.sleep
        monkeypatch.setattr(crypto.asyncio, "sleep", lambda s: real_sleep(0))

        async def wait_for(aw, timeout):
            aw.close()
            clock["t"] += timeout
            raise asyncio.TimeoutError
        monkeypatch.setattr(crypto.asyncio, "wait_for", wait_for)

        class Loop(_Eng):
            def __init__(self):
                super().__init__()
                self.scan_event, self.scans, self.next_scan_ts = asyncio.Event(), [], None

            async def get_settings(self):
                return _settings(timeframe="1h")

            async def scan_once(self, settings):
                self.scans.append(hms(clock["t"]))
                if len(self.scans) == 9:
                    raise asyncio.CancelledError
        eng = Loop()
        with pytest.raises(asyncio.CancelledError):
            await crypto.TradingEngine._scan_loop(eng)
        assert eng.scans == ["13:59:00", "14:00:05", "14:05:05", "14:10:05", "14:15:05", "14:20:05", "14:25:05", "15:00:05", "15:05:05"]


def _df(last_open_ts, n=10, step=900):
    return pd.DataFrame({"ts": [int((last_open_ts - (n - 1 - k) * step) * 1000) for k in range(n)]})


class TestClosedCandles:
    def test_forming_candle_is_skipped(self):
        """t + 5 s with the new candle already published: evaluate the one that just closed."""
        df = _df(utc(14, 15))
        assert funnel.closed_index(df, "15m", utc(14, 15, 5)) == len(df) - 2
        assert funnel.closed_index(df, "15m", utc(14, 29, 59)) == len(df) - 2

    def test_new_candle_not_published_yet(self):
        """t + 5 s and the exchange still ends on the candle that just closed: that is the one to evaluate, not the previous one."""
        df = _df(utc(14, 0))
        assert funnel.closed_index(df, "15m", utc(14, 15, 5)) == len(df) - 1
        assert funnel.closed_index(df, "15m", utc(14, 14, 59)) == len(df) - 2

    @pytest.mark.parametrize("tf,step", [("1h", 3600), ("4h", 14400), ("1d", 86400)])
    def test_other_timeframes(self, tf, step):
        df = _df(utc(12), step=step)
        assert funnel.closed_index(df, tf, utc(12) + step - 1) == len(df) - 2
        assert funnel.closed_index(df, tf, utc(12) + step + 5) == len(df) - 1

    @pytest.mark.parametrize("tf,now,default,expected", [("15m", utc(14, 15, 5), 30, 4), ("15m", utc(14, 20), 30, 30), ("1h", utc(15, 0, 5), 45, 4),
                                                         ("1h", utc(15, 0, 40), 60, 39), ("4h", utc(16, 0, 5), 300, 4), ("4h", utc(17), 300, 300), ("15m", utc(14, 15, 0), 30, 0)])
    def test_cache_never_older_than_the_candle_close(self, tf, now, default, expected):
        assert funnel.fresh_sec(tf, default, now) == expected

    def test_engines_refetch_after_the_close(self):
        import engine
        from fx import engine_feed
        assert "cache_sec=funnel.fresh_sec(tf, 30)" in inspect.getsource(engine.TradingEngine.funnel_frame)
        assert inspect.getsource(engine_feed.FeedMixin.funnel_frame).count("funnel.fresh_sec(tf,") == 3   # REAL broker · paper broker · Yahoo

    @pytest.mark.asyncio
    async def test_run_evaluates_the_closed_candle(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        seen, real = [], funnel.metrics

        def spy(df, i, has_volume, p, rich=False):
            seen.append((i, len(df), int(df["ts"].iloc[-1]) // 1000))
            return real(df, i, has_volume, p, rich)
        monkeypatch.setattr(funnel, "metrics", spy)

        async def none(*a, **k):
            return {"candidates": [], "model": "fast", "ms": 1}
        monkeypatch.setattr(funnel, "stage1", none)
        await funnel.run(eng, _settings(symbols=["BTCUSDT"]))
        i, n, last = seen[0]
        assert i == n - 1                                   # synthetic frame ends long ago: its last row is closed
        monkeypatch.setattr(funnel.time, "time", lambda: last + 5)   # 5 s into the last row: it is the forming candle
        seen.clear()
        await funnel.run(eng, _settings(symbols=["BTCUSDT"]))
        assert seen[0][0] == n - 2


class TestContextHierarchy:
    @pytest.mark.parametrize("tf,ctx", [("15m", ("4h", "1h")), ("5m", ("4h", "1h")), ("30m", ("4h", "1h")), ("1h", ("1d", "4h")), ("4h", ("1d", "4h"))])
    def test_mtf_of(self, tf, ctx):
        assert funnel.mtf_of(tf) == ctx

    def test_ttl_for_every_context_frame(self):
        assert all(t in funnel.MTF_TTL and t in funnel.TF_SECONDS for ctx in (*funnel.MTF_BY_TF.values(), funnel.MTF_DEFAULT) for t in ctx)

    def test_relabel(self):
        txt = "Evalúa primero 4H, después 1H; a favor de 4H/1H"
        assert funnel._relabel(txt, ("4h", "1h")) == txt and funnel._relabel(txt, None) == txt
        assert funnel._relabel(txt, ("1d", "4h")) == "Evalúa primero 1D, después 4H; a favor de 1D/4H"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tf,frames", [("15m", ["4h", "1h"]), ("1h", ["1d", "4h"])])
    async def test_run_fetches_the_right_frames(self, monkeypatch, tf, frames):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        asked, real = [], eng.funnel_frame

        async def frame(symbol, t, limit=300):
            asked.append(t)
            return await real(symbol, t, limit)
        eng.funnel_frame = frame
        got = {}

        async def s1(kind, t, rows, limit, usage_kind):
            got.update(rows)
            return {"candidates": [], "model": "fast", "ms": 1}
        monkeypatch.setattr(funnel, "stage1", s1)
        await funnel.run(eng, _settings(symbols=["BTCUSDT"], timeframe=tf))
        assert asked == [tf, *frames]
        m = got["BTCUSDT"]
        assert list(m["mtf"]) == frames and "demand" in m["mtf"][frames[0]] and "demand" not in m["mtf"][frames[1]]
        assert eng.analysis["BTCUSDT"]["mtf"].keys() == set(frames)
        assert f"lado a favor de {frames[0].upper()}" in funnel._mtf_txt(m)
        assert any(f"Embudo {'/'.join(f.upper() for f in frames)} → {tf}" in t for lv, t in eng.logs if lv == "SCAN")

    def test_locks_follow_the_context(self):
        m = {"atr": 1.0, "price": 100.0, "close": 100.0, "ema20": 100.2, "ema50": 99.4, "swing_high": 101.5, "swing_low": 98.5, "turn": None,
             "mtf": {"1d": {"trend": "ALCISTA"}, "4h": {"trend": "ALCISTA"}}}
        assert funnel.pullback_block(m, "long") is None
        m["mtf"]["1d"]["trend"] = "MIXTA"
        assert funnel.pullback_block(m, "long") == "1D y 4H no apoyan el lado"
        z = {"low": 101.5, "high": 102.0, "touches": 2, "dist_atr": 1.5}
        r = {"atr": 1.0, "tf": "1h", "supply": None, "demand": None, "mtf": {"1d": {"supply": z, "demand": None}, "4h": {}}}
        assert "oferta de 1d" in funnel.rr_block(r, "long", 100.0, 1.25, 2.0, False) and "75%" in funnel.rr_block(r, "long", 100.0, 1.25, 2.0, False)

    def test_monitor_loops_untouched(self):
        import engine
        from fx import engine as fxe
        for cls in (engine.TradingEngine, fxe.FxEngine):
            src = inspect.getsource(cls._monitor_loop)
            assert "asyncio.sleep(5)" in src and "next_trigger" not in src and "schedule" not in src


# ---------- 1h follow-up scans: forming candle next to the closed ones ----------
HOUR = utc(14)


def _hour_df(n=250, price=100.0, high=100.5, low=99.5, last=100.0):
    """Closed 1h candles up to 13:00 plus the candle that opened at 14:00 (forming)."""
    df = pd.DataFrame({"ts": [int((HOUR - (n - 1 - k) * 3600) * 1000) for k in range(n)], "open": price, "high": price + 0.5, "low": price - 0.5, "close": price, "volume": 100.0,
                       "ema20": price, "ema50": price, "ema200": price, "rsi": 55.0, "macd_hist": 0.1, "adx": 25.0, "atr": 1.0, "vol_ratio": 2.0})
    df.loc[n - 1, ["high", "low", "close", "rsi"]] = [high, low, last, 63.0]
    return df


def _hour_engine(monkeypatch, now, price=100.0, **candle):
    monkeypatch.setattr(funnel.time, "time", lambda: now)
    eng = FakeEngine(mtf=True, funnel_rich=True)
    eng.market.tickers["BTCUSDT"] = {"price": price}

    async def frame(symbol, tf, limit=300):
        return (_hour_df(**candle) if tf == "1h" else (await FakeEngine.funnel_frame(eng, symbol, tf, limit))[0]), True
    eng.funnel_frame = frame
    return eng


async def _rows_of(monkeypatch, eng, tf="1h", candidates=()):
    got = {}

    async def s1(kind, t, rows, limit, usage_kind):
        got.update(rows)
        return {"candidates": [dict(c) for c in candidates], "model": "fast", "ms": 1}
    monkeypatch.setattr(funnel, "stage1", s1)
    await funnel.run(eng, _settings(symbols=["BTCUSDT"], timeframe=tf))
    return got


class TestLiveCandle:
    def test_live_candle_fields_and_label(self):
        df = _hour_df(high=101.4, low=99.8)
        lc = funnel.live_candle(df, len(df) - 2, "1h", 1.0, 100.3, 15, utc(14, 15, 5))
        assert lc["label"] == "vela 1h en curso (minuto 15/60)" and (lc["minute"], lc["of"]) == (15, 60)
        assert (lc["open"], lc["high"], lc["low"], lc["price"]) == (100.0, 101.4, 99.8, 100.3) and lc["ts"] == int(HOUR * 1000)
        assert lc["upper_wick_atr"] == 1.1 and lc["lower_wick_atr"] == 0.2 and lc["body_atr"] == 0.3 and lc["rsi"] == 63.0

    def test_live_price_extends_the_range(self):
        df = _hour_df(high=100.5, low=99.5)
        lc = funnel.live_candle(df, len(df) - 2, "1h", 1.0, 100.9, 5, utc(14, 5, 5))
        assert lc["high"] == 100.9 and lc["upper_wick_atr"] == 0.0 and lc["body_atr"] == 0.9

    def test_no_forming_row_means_no_live_candle(self):
        df = _hour_df()
        assert funnel.live_candle(df, len(df) - 1, "1h", 1.0, 100.0, 5, utc(14, 5, 5)) is None          # frame ends on a closed candle
        assert funnel.live_candle(df, len(df) - 2, "1h", 1.0, 100.0, 5, utc(15, 5, 5)) is None          # last row belongs to another hour
        assert funnel.live_candle(df, len(df) - 2, "1h", 0.0, 100.0, 5, utc(14, 5, 5)) is None

    def test_text_says_it_is_not_closed(self):
        df = _hour_df(high=101.4, low=99.8)
        txt = funnel._live_txt(funnel.live_candle(df, len(df) - 2, "1h", 1.0, 100.3, 20, utc(14, 20, 5)))
        assert txt.startswith("vela 1h en curso (minuto 20/60), NO cerrada:") and "máximo 101.4" in txt and "mínimo 99.8" in txt and "precio actual 100.3" in txt
        assert "mecha superior 1.1 ATR" in txt and "RSI en vivo 63.0" in txt

    @pytest.mark.asyncio
    async def test_close_scan_uses_closed_candles_only(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = _hour_engine(monkeypatch, utc(14, 0, 5))
        m = (await _rows_of(monkeypatch, eng))["BTCUSDT"]
        assert not m.get("live") and m["candle_ts"] == int((HOUR - 3600) * 1000) and "en curso" not in funnel._row_txt("BTCUSDT", m)
        assert eng.analysis["BTCUSDT"]["live_candle"] is None
        assert not any("seguimiento" in t for _, t in eng.logs)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("minute", [5, 10, 15, 20, 25])
    async def test_follow_up_scan_adds_the_labelled_forming_candle(self, monkeypatch, minute):
        await _force_in_hours(monkeypatch)
        eng = _hour_engine(monkeypatch, utc(14, minute, 5), price=100.3, high=101.4, low=99.8)
        m = (await _rows_of(monkeypatch, eng))["BTCUSDT"]
        assert m["live"]["label"] == f"vela 1h en curso (minuto {minute}/60)" and m["live"]["high"] == 101.4 and m["live"]["price"] == 100.3
        assert m["candle_ts"] == int((HOUR - 3600) * 1000) and m["rsi"] == 55.0 and m["close"] == 100.0     # indicators still come from the closed candle
        assert f"vela 1h en curso (minuto {minute}/60), NO cerrada" in funnel._row_txt("BTCUSDT", m)
        assert eng.analysis["BTCUSDT"]["live_candle"]["minute"] == minute
        assert any(f"seguimiento con vela 1h en curso (minuto {minute}/60)" in t for lv, t in eng.logs if lv == "SCAN")

    @pytest.mark.asyncio
    async def test_after_minute_25_and_on_15m_nothing_changes(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = _hour_engine(monkeypatch, utc(14, 40, 0))
        assert not (await _rows_of(monkeypatch, eng))["BTCUSDT"].get("live")
        eng = FakeEngine(mtf=True, funnel_rich=True)
        monkeypatch.setattr(funnel.time, "time", lambda: utc(14, 10, 5))
        m = (await _rows_of(monkeypatch, eng, "15m"))["BTCUSDT"]
        assert "live" not in m and eng.analysis["BTCUSDT"]["live_candle"] is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("now,live", [(utc(14, 0, 5), False), (utc(14, 15, 5), True)])
    async def test_prompts_label_the_forming_candle(self, monkeypatch, now, live):
        await _force_in_hours(monkeypatch)
        eng = _hour_engine(monkeypatch, now, price=100.3, high=101.4, low=99.8)
        seen = {"system": [], "prompt": []}
        monkeypatch.setattr(funnel, "_chat", lambda system, *a: seen["system"].append(system) or type("C", (), {})())

        async def ask(chat, prompt):
            seen["prompt"].append(prompt)
            return ('{"candidates":[{"symbol":"BTCUSDT","side":"short","reason":"rechazo"}]}' if len(seen["prompt"]) == 1 else
                    '{"decision":"PASS","confidence":10,"sl":null,"tp":null,"rule":0,"reasoning":"x"}')
        monkeypatch.setattr(funnel, "_ask", ask)
        monkeypatch.setattr(funnel, "pullback_block", lambda m, side: None)
        await funnel.run(eng, _settings(symbols=["BTCUSDT"], timeframe="1h"))
        assert len(seen["prompt"]) == 2
        fast, deep = seen["prompt"]
        assert ("VELA EN CURSO" in seen["system"][0]) is live and ("vela 1h en curso (minuto 15/60), NO cerrada" in fast) is live
        assert ("VELA EN CURSO · vela 1h en curso (minuto 15/60), NO cerrada" in deep) is live and ("no cuenta como vela cerrada" in deep) is live
        closed = deep.split("ÚLTIMAS VELAS CERRADAS")[1].split("VELA EN CURSO")[0]
        assert "18 13:00" in closed and "18 14:00" not in closed and "H101.4" not in closed      # the forming candle is never listed as closed


class TestFollowUpGuards:
    CAND = [{"symbol": "BTCUSDT", "side": "long", "reason": "x"}]

    def _deep(self, monkeypatch, decision="BUY"):
        calls = []

        async def s2(kind, symbol, tf, cand, m, *a, **kw):
            calls.append(hms(funnel.time.time()))
            return {"decision": decision, "confidence": 80, "rule": None, "reasoning": "ok", "model": "deep", "ms": 1, "sl": m["price"] - 1.2 * m["atr"], "tp": None}
        monkeypatch.setattr(funnel, "stage2", s2)
        monkeypatch.setattr(funnel, "pullback_block", lambda m, side: None)
        monkeypatch.setattr(funnel, "rr_block", lambda *a, **k: None)
        return calls

    @pytest.mark.asyncio
    async def test_every_follow_up_reaches_the_strong_model(self, monkeypatch):
        """Marks are exactly 5 min apart: the re-scan cool-down must not swallow any of them, and the fast model is asked again each time."""
        await _force_in_hours(monkeypatch)
        calls, asked = self._deep(monkeypatch, "PASS"), []
        eng = _hour_engine(monkeypatch, utc(14, 0, 5))
        for mm in (0, 5, 10, 15, 20, 25):
            monkeypatch.setattr(funnel.time, "time", lambda mm=mm: utc(14, mm, 5))
            got = await _rows_of(monkeypatch, eng, candidates=self.CAND)
            asked.append(bool(got))
        assert calls == ["14:00:05", "14:05:05", "14:10:05", "14:15:05", "14:20:05", "14:25:05"] and all(asked)

    @pytest.mark.asyncio
    async def test_pair_opened_this_hour_is_not_re_evaluated(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        calls = self._deep(monkeypatch)
        eng = _hour_engine(monkeypatch, utc(14, 5, 5))
        await _rows_of(monkeypatch, eng, candidates=self.CAND)
        assert len(eng.opened) == 1 and calls == ["14:05:05"] and eng.funnel_opened == {"BTCUSDT": int((HOUR - 3600) * 1000)}
        eng._open_positions.clear()                                    # the trade closed (stop hit) inside the same hour
        for mm in (10, 15, 20, 25):
            monkeypatch.setattr(funnel.time, "time", lambda mm=mm: utc(14, mm, 5))
            assert await _rows_of(monkeypatch, eng, candidates=self.CAND) == {}
        assert calls == ["14:05:05"] and "Ya se abrió una operación" in eng.analysis["BTCUSDT"]["funnel"]["text"]

    @pytest.mark.asyncio
    async def test_max_positions_blocks_the_follow_ups(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        calls = self._deep(monkeypatch)
        eng = _hour_engine(monkeypatch, utc(14, 10, 5))

        async def full(settings):
            return {"acc": {"equity": 500.0, "daily_pnl_pct": 0.0, "daily_paused": False}, "open": {"ETHUSDT", "SOLUSDT", "XRPUSDT"}, "free": 0,
                    "block": "máximo de posiciones alcanzado (3)"}
        eng.funnel_state = full
        assert await _rows_of(monkeypatch, eng, candidates=self.CAND) == {} and not calls

    def test_15m_bots_keep_the_5_minute_cooldown(self):
        assert funnel.DEEP_COOLDOWN_SEC == 300 and "15m" not in funnel.FOLLOW_UPS
        src = inspect.getsource(funnel.run)
        assert "if tf in FOLLOW_UPS else DEEP_COOLDOWN_SEC" in src and "tf in FOLLOW_UPS and engine.__dict__.get(\"funnel_opened\"" in src
