"""Iter119 — range-edge path (EXTREMO_OFERTA / EXTREMO_DEMANDA, 1:1): tighter AI stop 0.65–1.05 ATR + 0.15 ATR buffer (final 0.80–1.20 ATR),
target 0.05 ATR short of the opposite edge, and entry on the touch candle without waiting for a second turn candle. Trend rules stay untouched."""
import inspect
import pytest
import funnel
from tests.test_iter103_bybit_hardening import FakeEngine, _settings, _force_in_hours


def _s(fixed=False):
    return type("S", (), {"atr_levels_enabled": fixed, "atr_sl_mult": 2.0, "atr_tp_mult": 4.0})()


def _range_stop(side, ai_sl):
    return funnel.stop_level(side, 100.0, 1.0, _s(), ai_sl, None, funnel.RANGE_SL_BUFFER_ATR, funnel.RANGE_SL_ATR_BAND)


class TestConstants:
    def test_range_values(self):
        assert funnel.RANGE_SL_ATR_BAND == (0.65, 1.05) and funnel.RANGE_SL_BUFFER_ATR == 0.15 and funnel.RANGE_EDGE_GAP_ATR == 0.05 and funnel.RANGE_TOUCH_ATR == 0.15

    def test_trend_values_untouched(self):
        assert funnel.SL_ATR_BAND == (0.85, 4.0) and funnel.SL_BUFFER_ATR == 0.25 and funnel.RANGE_ROOM_GAP_ATR == 0.2
        assert (funnel.TREND_RR, funnel.RANGE_RR, funnel.RR_FREE_4H, funnel.RR_FREE_ENTRY) == (2.0, 1.0, 0.75, 0.50)

    def test_widest_range_stop_fits_the_narrowest_range(self):
        """Final stop ≤ 1.20 ATR + 0.05 ATR gap = 1.25 ATR ≤ the 1.3 ATR of room the edge tag requires."""
        assert funnel.RANGE_SL_ATR_BAND[1] + funnel.RANGE_SL_BUFFER_ATR + funnel.RANGE_EDGE_GAP_ATR <= funnel.RANGE_ROOM_ATR


class TestRangeStop:
    @pytest.mark.parametrize("side,ai_sl,final", [("short", 100.65, 100.80), ("short", 101.05, 101.20), ("short", 100.9, 101.05),
                                                  ("long", 99.35, 99.20), ("long", 98.95, 98.80)])
    def test_band_plus_buffer(self, side, ai_sl, final):
        sl, src = _range_stop(side, ai_sl)
        assert src == "ia" and round(sl, 6) == final

    @pytest.mark.parametrize("side,ai_sl", [("short", 100.64), ("short", 101.06), ("short", 101.3), ("long", 99.4), ("long", 98.7)])
    def test_out_of_range_band_falls_back_to_atr_stop(self, side, ai_sl):
        sl, src = _range_stop(side, ai_sl)
        assert src == "atr" and abs(100.0 - sl) == 2.0

    def test_trend_stop_still_rejects_a_tight_stop(self):
        assert funnel.stop_level("long", 100.0, 1.0, _s(), 99.3, None, funnel.SL_BUFFER_ATR) == (98.0, "atr")       # 0.70 ATR < 0.85
        assert funnel.stop_level("long", 100.0, 1.0, _s(), 99.15, None, funnel.SL_BUFFER_ATR)[0] == pytest.approx(98.9)   # 0.85 + 0.25 = 1.10

    def test_fixed_atr_toggle_ignores_the_band(self):
        assert funnel.stop_level("short", 100.0, 1.0, _s(True), 100.8, None, funnel.RANGE_SL_BUFFER_ATR, funnel.RANGE_SL_ATR_BAND) == (102.0, "atr")


def _m(demand_high=None, supply_low=None):
    return {"atr": 1.0, "tf": "15m", "supply": supply_low and {"low": supply_low, "high": supply_low + 0.4, "touches": 2},
            "demand": demand_high and {"low": demand_high - 0.4, "high": demand_high, "touches": 2}}


class TestRangeRoom:
    def test_one_to_one_reaches_005_atr_from_the_opposite_edge(self):
        m = _m(demand_high=98.75)   # opposite demand 1.25 ATR away
        assert funnel.rr_block(m, "short", 100.0, 1.20, funnel.RANGE_RR, True, funnel.RANGE_EDGE_GAP_ATR) is None
        why = funnel.rr_block(m, "short", 100.0, 1.21, funnel.RANGE_RR, True, funnel.RANGE_EDGE_GAP_ATR)
        assert "1:1" in why and "0.05 ATR de margen" in why

    def test_long_side(self):
        m = _m(supply_low=100.85)   # opposite supply 0.85 ATR away
        assert funnel.rr_block(m, "long", 100.0, 0.80, funnel.RANGE_RR, True, funnel.RANGE_EDGE_GAP_ATR) is None
        assert funnel.rr_block(m, "long", 100.0, 0.81, funnel.RANGE_RR, True, funnel.RANGE_EDGE_GAP_ATR)

    def test_todays_case_now_fits(self):
        """Range 1.3 ATR wide: the old stop (1.3 ATR + 0.25) needed 1.75 ATR; the range stop (1.0 ATR + 0.15) needs 1.20 ATR."""
        m = _m(demand_high=98.7)
        assert funnel.rr_block(m, "short", 100.0, 1.55, funnel.RANGE_RR, True)
        sl, _ = _range_stop("short", 101.0)
        assert funnel.rr_block(m, "short", 100.0, sl - 100.0, funnel.RANGE_RR, True, funnel.RANGE_EDGE_GAP_ATR) is None

    def test_default_gap_stays_02_for_choppy_regime_without_edge(self):
        m = _m(demand_high=98.55)
        assert funnel.rr_block(m, "short", 100.0, 1.25, funnel.RANGE_RR, True) is None
        assert "0.2 ATR de margen" in funnel.rr_block(_m(demand_high=98.6), "short", 100.0, 1.25, funnel.RANGE_RR, True)

    def test_trend_margin_untouched(self):
        assert "2:1" in funnel.rr_block(_m(supply_low=101.24), "long", 100.0, 1.25, funnel.TREND_RR, False)
        assert funnel.rr_block(_m(supply_low=101.25), "long", 100.0, 1.25, funnel.TREND_RR, False) is None


class TestPrompts:
    def test_system_prompt_range_touch_entry(self):
        p = funnel.DEEP_SYSTEM
        assert "ENTRADA EN BORDE DE RANGO" in p and "NO esperes una vela adicional de giro" in p and "<= 0.15 ATR" in p
        assert "RSI >= 58" in p and "RSI <= 42" in p and "cuerpo completo fuera del rango" in p and "0.05 ATR de margen" in p

    def test_brake_candle_rule_kept_for_trend(self):
        p = funnel.DEEP_SYSTEM
        assert "VELA DE FRENO OBLIGATORIA" in p and "Sin vela de freno o rechazo confirmada, responde PASS." in p
        assert p.index("VELA DE FRENO OBLIGATORIA") < p.index("la ÚNICA excepción es la ENTRADA EN BORDE DE RANGO") < p.index("ENTRADA EN BORDE DE RANGO (solo")
        assert "exige vela de freno en la EMA20/EMA50" in inspect.getsource(funnel.stage2)   # orderly pullback (2:1)

    def test_system_prompt_formats(self):
        out = funnel.DEEP_SYSTEM.format(market="forex", tf="15m", levels="x")
        assert "{tf}" not in out and "ÚLTIMA vela cerrada de 15m ya tocó o entró" in out

    @pytest.mark.asyncio
    @pytest.mark.parametrize("edge", ["EXTREMO_OFERTA", None])
    async def test_stage2_prompt_by_path(self, monkeypatch, edge):
        seen = {}
        monkeypatch.setattr(funnel, "_chat", lambda system, kind: seen.setdefault("system", system) and None)

        async def ask(chat, prompt):
            seen["prompt"] = prompt
            return '{"decision":"PASS","confidence":10,"sl":null,"tp":null,"rule":0,"reasoning":"x"}'
        monkeypatch.setattr(funnel, "_ask", ask)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        df, has_volume = await eng.funnel_frame("BTCUSDT", "15m", 300)
        i = len(df) - 2
        m = funnel.metrics(df, i, has_volume, 100.0, True)
        m["mtf"] = None
        htf = {"tf": "1h", "trend": "MIXTA", "rsi": 50, "adx": 20, "dist_ema50_atr": 0}
        cand = {"symbol": "BTCUSDT", "side": "short", "reason": "rechazo", **({"range_edge": edge} if edge else {})}
        await funnel.stage2("fx", "BTCUSDT", "15m", cand, m, df, i, has_volume, htf, [], {}, _settings(atr_levels_enabled=False), "fx")
        ranged = "entre 0.65 y 1.05 ATR" in seen["system"]
        assert ranged is bool(edge) and "entre 0.85 y 4 ATR" in seen["system"]
        assert ("no esperes otra vela de giro" in seen["prompt"]) is bool(edge) and ("0.05 ATR antes de la zona opuesta" in seen["prompt"]) is bool(edge)


# ---------- integration: funnel.run → _deep ----------
def _run(monkeypatch, *, side, edge, ai_sl_atr, opposite_atr, regime=None):
    eng = FakeEngine(mtf=True, funnel_rich=True)
    real, d = funnel.metrics, (1 if side == "long" else -1)

    def fake(df, i, has_volume, p, rich=False):
        m = real(df, i, has_volume, p, rich)
        a, px = m["atr"], m["price"]
        near, far = {"low": px - 0.3 * a, "high": px + 0.3 * a, "touches": 3, "dist_atr": 0.0}, None
        edge_px = px + d * opposite_atr * a
        far = {"low": min(edge_px, edge_px + d * a), "high": max(edge_px, edge_px + d * a), "touches": 3, "dist_atr": opposite_atr}
        m.update(adx=25, activity=2.0, rsi=50, dist_ema20_atr=0.5, fast_cross="ALCISTA" if side == "long" else "BAJISTA", turn=None, regime=regime,
                 supply=far if side == "long" else near, demand=near if side == "long" else far)
        return m
    monkeypatch.setattr(funnel, "metrics", fake)
    monkeypatch.setattr(funnel, "range_edge", lambda m: edge)

    async def mtf(engine, symbol, price):
        return {"4h": {"tf": "4h", "trend": "MIXTA", "rsi": 50, "adx": 25, "dist_ema50_atr": 0.5, "demand": None, "supply": None},
                "1h": {"tf": "1h", "trend": "MIXTA", "rsi": 50, "adx": 20, "dist_ema50_atr": 0.3}}
    monkeypatch.setattr(funnel, "_mtf", mtf)

    async def s1(kind, tf, rows, limit, usage_kind):
        return {"candidates": [{"symbol": "BTCUSDT", "side": side, "reason": "x"}], "model": "fast", "ms": 1}

    async def s2(kind, symbol, tf, cand, m, *a, **kw):
        return {"decision": "BUY" if side == "long" else "SELL", "confidence": 80, "rule": None, "reasoning": "ok", "model": "deep", "ms": 1,
                "sl": m["price"] - d * ai_sl_atr * m["atr"], "tp": None}
    monkeypatch.setattr(funnel, "stage1", s1)
    monkeypatch.setattr(funnel, "stage2", s2)
    return eng, _settings(atr_levels_enabled=False)


def _levels(eng):
    doc = eng.db.signals.docs[0]
    a = doc["indicators"]["atr"]
    return abs(doc["price"] - doc["sl"]) / a, abs(doc["tp"] - doc["price"]) / a, doc


@pytest.mark.asyncio
@pytest.mark.parametrize("side,edge", [("short", "EXTREMO_OFERTA"), ("long", "EXTREMO_DEMANDA")])
async def test_range_edge_opens_with_tight_stop_inside_a_13_atr_range(monkeypatch, side, edge):
    await _force_in_hours(monkeypatch)
    eng, s = _run(monkeypatch, side=side, edge=edge, ai_sl_atr=1.0, opposite_atr=1.3)
    await funnel.run(eng, s)
    assert len(eng.opened) == 1
    risk, reward, doc = _levels(eng)
    assert risk == pytest.approx(1.15, abs=1e-3) and reward == pytest.approx(risk, abs=1e-3)   # 1.0 ATR + 0.15 buffer, exact 1:1
    assert doc["context"]["funnel"]["rr_target"] == 1.0 and doc["context"]["funnel"]["range_edge"] == edge and doc["ai_levels_applied"] is True


@pytest.mark.asyncio
async def test_range_edge_tightest_stop_is_080_atr(monkeypatch):
    await _force_in_hours(monkeypatch)
    eng, s = _run(monkeypatch, side="short", edge="EXTREMO_OFERTA", ai_sl_atr=0.65, opposite_atr=1.3)
    await funnel.run(eng, s)
    risk, reward, _ = _levels(eng)
    assert len(eng.opened) == 1 and risk == pytest.approx(0.80, abs=1e-3) and reward == pytest.approx(0.80, abs=1e-3)


@pytest.mark.asyncio
async def test_range_edge_still_blocked_when_the_range_is_too_narrow(monkeypatch):
    await _force_in_hours(monkeypatch)
    eng, s = _run(monkeypatch, side="short", edge="EXTREMO_OFERTA", ai_sl_atr=1.05, opposite_atr=1.2)   # 1.20 + 0.05 > 1.20
    await funnel.run(eng, s)
    assert not eng.opened and any("falta de recorrido para 1:1 en rango" in t and "0.05 ATR de margen" in t for _, t in eng.logs)


@pytest.mark.asyncio
async def test_trend_trade_keeps_the_085_floor_and_025_buffer(monkeypatch):
    """Same 0.70 ATR stop outside the range path: rejected (below 0.85 ATR) → ATR stop, 2:1 target."""
    await _force_in_hours(monkeypatch)
    eng, s = _run(monkeypatch, side="long", edge=None, ai_sl_atr=0.70, opposite_atr=10)
    await funnel.run(eng, s)
    risk, reward, doc = _levels(eng)
    assert doc["ai_levels_applied"] is False and risk == pytest.approx(s.atr_sl_mult, abs=1e-3) and reward == pytest.approx(2 * risk, abs=1e-3)
    assert doc["context"]["funnel"]["rr_target"] == 2.0


@pytest.mark.asyncio
async def test_trend_trade_stop_gets_the_025_buffer(monkeypatch):
    await _force_in_hours(monkeypatch)
    eng, s = _run(monkeypatch, side="long", edge=None, ai_sl_atr=1.0, opposite_atr=10)
    await funnel.run(eng, s)
    risk, reward, _ = _levels(eng)
    assert risk == pytest.approx(1.25, abs=1e-3) and reward == pytest.approx(2.5, abs=1e-3)


@pytest.mark.asyncio
async def test_choppy_regime_without_edge_keeps_trend_band_and_02_gap(monkeypatch):
    await _force_in_hours(monkeypatch)
    eng, s = _run(monkeypatch, side="short", edge=None, ai_sl_atr=1.0, opposite_atr=1.3, regime="RANGO / CHOPPY")   # 1.25 + 0.2 > 1.3
    await funnel.run(eng, s)
    assert not eng.opened and any("0.2 ATR de margen" in t for _, t in eng.logs)
