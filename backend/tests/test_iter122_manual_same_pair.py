"""Iter122 — manual orders have no one-position-per-pair rule and no «Máx. posiciones» cap (Bybit, HotForex, Pepperstone). The AI funnel keeps one position per pair.
Bybit REAL keeps one position per pair on the exchange: a manual order on a held pair is added to it (same side only) and its SL/TP rule the whole position."""
import os
import uuid
import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import engine as crypto
from fx.engine import FxEngine
from fx.broker import HFM, PEPPERSTONE
from models import Settings
from fx.models import FxSettings


def _sig(strategy="manual", side="long", price=100.0, sl=98.0, tp=104.0, atr=1.0):
    return {"side": side, "strategy": strategy, "price": price, "sl": sl, "tp": tp, "atr": atr, "score": 0, "reasons": [], "indicators": {"atr": atr}, "key_ts": 1, "tp_mode": "fixed"}


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=1500)
    try:
        await client.admin.command("ping")
    except Exception:
        pytest.skip("MongoDB no disponible")
    name = f"pytest_iter122_{uuid.uuid4().hex[:8]}"
    yield client[name]
    await client.drop_database(name)


async def _noop(*a, **k):
    return None


# ---------------- Bybit ----------------
@pytest_asyncio.fixture
async def bybit(db):
    eng = crypto.TradingEngine(db)
    eng.tg = _noop
    await eng._new_account(10_000.0)
    eng.market.tickers = {"BTCUSDT": {"price": 100.0}, "ETHUSDT": {"price": 100.0}}
    return eng


class Broker:
    def __init__(self):
        self.opens, self.stops = [], []

    async def open(self, symbol, side, qty, sl, tp, leverage):
        self.opens.append((symbol, side, qty, sl, tp))
        return {"order_id": f"o{len(self.opens)}", "link_id": f"l{len(self.opens)}", "qty": qty, "entry": 110.0 if len(self.opens) > 1 else 100.0, "fee": 0.5, "filled": True}

    async def set_stop(self, symbol, sl, tp=None):
        self.stops.append((symbol, sl, tp))


def _live(eng, qty=2.0):
    eng.live, eng.broker = True, Broker()

    async def size(symbol, entry, sl, risk_pct, leverage):
        return {"qty": qty, "margin": 1.0, "equity": 10_000.0}
    eng.live_size, eng._log_sizing = size, _noop
    return eng.broker


class TestBybitPaper:
    @pytest.mark.asyncio
    async def test_manual_orders_stack_on_the_same_pair_in_any_direction(self, bybit, db):
        s = Settings(max_positions=3)
        assert await bybit.open_position("BTCUSDT", _sig("ai_funnel"), s, None)
        assert await bybit.open_position("BTCUSDT", _sig(), s, None)
        assert await bybit.open_position("BTCUSDT", _sig(side="short", sl=102.0, tp=96.0), s, None)
        docs = await db.positions.find({"symbol": "BTCUSDT"}).to_list(10)
        assert [(d["strategy"], d["side"]) for d in docs] == [("ai_funnel", "long"), ("manual", "long"), ("manual", "short")]

    @pytest.mark.asyncio
    async def test_ai_trade_is_still_one_per_pair(self, bybit, db):
        s = Settings()
        assert await bybit.open_position("BTCUSDT", _sig(), s, None)
        assert await bybit.open_position("BTCUSDT", _sig("ai_funnel"), s, None) is None
        assert bybit._open_fail == "par ya abierto" and await db.positions.count_documents({}) == 1

    @pytest.mark.asyncio
    async def test_manual_ignores_max_positions_but_the_ai_does_not(self, bybit, db):
        s = Settings(max_positions=1)
        assert await bybit.open_position("BTCUSDT", _sig("ai_funnel"), s, None)
        assert await bybit.open_position("ETHUSDT", _sig("ai_funnel"), s, None) is None and "máximo de posiciones" in bybit._open_fail
        assert await bybit.open_position("ETHUSDT", _sig(), s, None) and await bybit.open_position("ETHUSDT", _sig(), s, None)
        assert await db.positions.count_documents({}) == 3


class TestBybitReal:
    @pytest.mark.asyncio
    async def test_same_side_is_added_to_the_position(self, bybit, db):
        broker = _live(bybit)
        s = Settings(leverage=5)
        first = await bybit.open_position("BTCUSDT", _sig("ai_funnel", sl=97.0, tp=106.0), s, None)
        bybit.market.tickers["BTCUSDT"]["price"] = 110.0
        pos = await bybit.open_position("BTCUSDT", _sig(price=110.0, sl=105.0, tp=120.0), s, None)
        docs = await db.positions.find({}).to_list(10)
        assert len(docs) == 1 and pos.id == first.id and len(broker.opens) == 2
        d = docs[0]
        assert d["qty"] == 4.0 and d["initial_qty"] == 4.0 and d["entry_price"] == pytest.approx(105.0) and d["fees"] == pytest.approx(1.0) and d["strategy"] == "ai_funnel"
        assert d["sl"] == pytest.approx(pos.sl) and d["tp"] == pytest.approx(pos.tp) and d["sl"] == pytest.approx(105.0, rel=1e-2) and d["tp"] == pytest.approx(120.0, rel=1e-2)
        assert broker.stops == [("BTCUSDT", d["sl"], d["tp"])] and d["margin"] == pytest.approx(4.0 * 105.0 / 5)
        assert d["initial_sl"] == first.initial_sl          # new stop sits above the averaged entry: the R reference stays
        assert await db.logs.count_documents({"message": {"$regex": "AMPLIADA REAL LONG BTCUSDT"}}) == 1

    @pytest.mark.asyncio
    async def test_stop_below_the_average_becomes_the_r_reference(self, bybit, db):
        _live(bybit)
        s = Settings()
        await bybit.open_position("BTCUSDT", _sig("ai_funnel"), s, None)
        bybit.market.tickers["BTCUSDT"]["price"] = 110.0
        pos = await bybit.open_position("BTCUSDT", _sig(price=110.0, sl=101.0, tp=125.0), s, None)
        assert pos.initial_sl == pos.sl and pos.breakeven is False

    @pytest.mark.asyncio
    async def test_opposite_side_is_rejected_without_sending_anything(self, bybit, db):
        broker = _live(bybit)
        s = Settings()
        await bybit.open_position("BTCUSDT", _sig("ai_funnel"), s, None)
        assert await bybit.open_position("BTCUSDT", _sig(side="short", sl=102.0, tp=96.0), s, None) is None
        assert "una sola posición por par" in bybit._open_fail and len(broker.opens) == 1 and (await db.positions.find_one({}))["qty"] == 2.0

    @pytest.mark.asyncio
    async def test_cross_test_position_is_not_extended(self, bybit, db):
        broker = _live(bybit)
        s = Settings()
        await bybit.open_position("BTCUSDT", _sig("ema_cross"), s, None, exit_mode="cross")
        assert await bybit.open_position("BTCUSDT", _sig(), s, None) is None and "test cruce EMA" in bybit._open_fail and len(broker.opens) == 1

    @pytest.mark.asyncio
    async def test_manual_on_a_free_pair_with_full_slots_opens_normally(self, bybit, db):
        broker = _live(bybit)
        s = Settings(max_positions=1)
        await bybit.open_position("BTCUSDT", _sig("ai_funnel"), s, None)
        assert await bybit.open_position("ETHUSDT", _sig(), s, None)
        assert await db.positions.count_documents({}) == 2 and len(broker.opens) == 2 and not broker.stops

    @pytest.mark.asyncio
    async def test_rejected_order_leaves_the_position_untouched(self, bybit, db):
        broker = _live(bybit)
        s = Settings()
        await bybit.open_position("BTCUSDT", _sig("ai_funnel"), s, None)

        async def boom(*a):
            raise RuntimeError("insufficient balance")
        broker.open = boom
        assert await bybit.open_position("BTCUSDT", _sig(), s, None) is None and "orden REAL rechazada" in bybit._open_fail
        d = await db.positions.find_one({})
        assert d["qty"] == 2.0 and d["sl"] == pytest.approx(98.0, rel=1e-2)


# ---------------- HotForex / Pepperstone ----------------
@pytest.mark.asyncio
@pytest.mark.parametrize("profile", [HFM, PEPPERSTONE])
async def test_fx_manual_orders_stack_and_skip_the_cap(db, profile):
    eng = FxEngine(db, profile)
    eng.tg = _noop
    await eng._new_account(10_000.0)
    eng.market.tickers = {"EURUSD": {"price": 1.1, "bid": 1.1, "ask": 1.1001, "spread": 0.0001}, "GBPUSD": {"price": 1.3, "bid": 1.3, "ask": 1.3001, "spread": 0.0001}}
    s = FxSettings(max_positions=1)
    buy = dict(price=1.1, sl=1.095, tp=1.11, atr=0.002)
    pos, why = await eng.open_position("EURUSD", _sig("ai_funnel", **buy), s, None)
    assert pos, why
    assert (await eng.open_position("EURUSD", _sig("ai_funnel", **buy), FxSettings(max_positions=2), None))[1] == "par ya abierto"
    assert "máximo de posiciones" in (await eng.open_position("GBPUSD", _sig("ai_funnel", price=1.3, sl=1.295, tp=1.31, atr=0.002), s, None))[1]
    for sig in (_sig(**buy), _sig(side="short", price=1.1, sl=1.105, tp=1.09, atr=0.002)):
        pos, why = await eng.open_position("EURUSD", sig, s, None)
        assert pos, why
    docs = await eng.db.positions.find({"symbol": "EURUSD"}).to_list(10)
    assert [(d["strategy"], d["side"]) for d in docs] == [("ai_funnel", "long"), ("manual", "long"), ("manual", "short")]
    assert len({str(d["_id"]) for d in docs}) == 3


# ---------------- Iter123: manual positions use no slot · manual partial close ----------------
class TestManualUsesNoSlot:
    @pytest.mark.asyncio
    async def test_bybit_manual_positions_leave_the_slots_to_the_ai(self, bybit, db):
        s = Settings(max_positions=1)
        assert await bybit.open_position("BTCUSDT", _sig(), s, None) and await bybit.open_position("BTCUSDT", _sig(), s, None)
        st = await bybit.funnel_state(s)
        assert st["free"] == 1 and st["block"] is None and st["open"] == {"BTCUSDT"}
        assert await bybit.open_position("ETHUSDT", _sig("ai_funnel"), s, None)
        st = await bybit.funnel_state(s)
        assert st["free"] == 0 and "máximo de posiciones alcanzado (1)" in st["block"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("profile", [HFM, PEPPERSTONE])
    async def test_fx_manual_positions_leave_the_slots_to_the_ai(self, db, profile, monkeypatch):
        from fx import engine_signals
        monkeypatch.setattr(engine_signals.filters, "rollover_block", lambda *a: None)
        monkeypatch.setattr(engine_signals.trade_guard, "friday", lambda *a: {"block": False})
        eng = await _fx(db, profile)
        s = FxSettings(max_positions=1)
        for _ in range(2):
            assert (await eng.open_position("EURUSD", _sig(**BUY), s, None))[0]
        st = await eng.funnel_state(s)
        assert st["free"] == 1 and st["block"] is None
        pos, why = await eng.open_position("GBPUSD", _sig("ai_funnel", price=1.3, sl=1.295, tp=1.31, atr=0.002), s, None)
        assert pos, why
        assert "máximo de posiciones alcanzado (1)" in (await eng.funnel_state(s))["block"]


BUY = dict(price=1.1, sl=1.095, tp=1.11, atr=0.002)


async def _fx(db, profile):
    eng = FxEngine(db, profile)
    eng.tg = _noop
    await eng._new_account(10_000.0)
    eng.market.tickers = {"EURUSD": {"price": 1.1, "bid": 1.1, "ask": 1.1001, "spread": 0.0001}, "GBPUSD": {"price": 1.3, "bid": 1.3, "ask": 1.3001, "spread": 0.0001}}
    return eng


class TestPartialClose:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("pct", [25, 50, 75])
    async def test_bybit_paper(self, bybit, db, pct):
        pos = await bybit.open_position("BTCUSDT", _sig("ai_funnel"), Settings(), None)
        bal = (await db.account.find_one({"_id": "main"}))["balance"]
        bybit.market.tickers["BTCUSDT"]["price"] = 103.0
        out, why = await bybit.partial_close(pos.id, pct)
        assert out, why
        d = await db.positions.find_one({})
        part = pos.qty - d["qty"]
        assert part == pytest.approx(pos.qty * pct / 100, rel=0.02) and d["initial_qty"] == pos.qty and d["margin"] == pytest.approx(pos.margin * d["qty"] / pos.qty, rel=1e-4)
        assert d["partial_gross"] > 0 and d["partial_fees"] > 0 and d["partial_done"] is False and (d["sl"], d["tp"]) == (pos.sl, pos.tp)
        assert (await db.account.find_one({"_id": "main"}))["balance"] == pytest.approx(bal + d["partial_gross"] - d["partial_fees"])
        assert await db.logs.count_documents({"message": {"$regex": f"PARCIAL MANUAL LONG BTCUSDT: cerrado {pct}%"}}) == 1
        trade = await bybit.close_position(pos.id)
        assert trade.pnl == pytest.approx(d["partial_gross"] + crypto.pnl("long", pos.entry_price, trade.exit_price, d["qty"]) - trade.fees, abs=0.02)

    @pytest.mark.asyncio
    async def test_bybit_can_be_repeated_and_handles_errors(self, bybit, db):
        pos = await bybit.open_position("BTCUSDT", _sig("ai_funnel"), Settings(), None)
        for _ in range(2):
            assert (await bybit.partial_close(pos.id, 50))[0]
        assert (await db.positions.find_one({}))["qty"] == pytest.approx(pos.qty / 4, rel=0.02)
        assert (await bybit.partial_close("0" * 24, 50)) == (None, "Posición no encontrada")
        await db.positions.update_one({}, {"$set": {"qty": 0.001}})
        out, why = await bybit.partial_close(pos.id, 50)
        assert out is None and "lote mínimo" in why and (await db.positions.find_one({}))["qty"] == 0.001

    @pytest.mark.asyncio
    async def test_bybit_real_sends_a_reduce_order(self, bybit, db):
        broker = _live(bybit)
        closes = []

        async def close(symbol, side, qty):
            closes.append((symbol, side, qty))
            return {"exit": 104.0, "fee": 0.2}
        broker.close = close
        pos = await bybit.open_position("BTCUSDT", _sig("ai_funnel"), Settings(), None)
        out, why = await bybit.partial_close(pos.id, 25)
        assert out and closes == [("BTCUSDT", "long", 0.5)] and out.qty == 1.5
        d = await db.positions.find_one({})
        assert d["qty"] == 1.5 and d["partial_gross"] == pytest.approx(2.0) and d["partial_fees"] == pytest.approx(0.2)

        async def boom(*a):
            raise RuntimeError("timeout")
        broker.close = boom
        out, why = await bybit.partial_close(pos.id, 50)
        assert out is None and "Bybit rechazó" in why and (await db.positions.find_one({}))["qty"] == 1.5

    @pytest.mark.asyncio
    @pytest.mark.parametrize("profile", [HFM, PEPPERSTONE])
    async def test_fx_paper(self, db, profile):
        eng = await _fx(db, profile)
        pos, why = await eng.open_position("EURUSD", _sig(**BUY), FxSettings(), None)
        assert pos, why
        bal = (await eng.db.account.find_one({"_id": "main"}))["balance"]
        eng.market.tickers["EURUSD"].update(price=1.105, bid=1.105, ask=1.1051)
        out, why = await eng.partial_close(pos.id, 50)
        assert out, why
        d = await eng.db.positions.find_one({})
        assert d["qty"] == pytest.approx(pos.qty / 2, abs=0.011) and d["partial_gross"] > 0 and d["initial_qty"] == pos.qty and d["partial_done"] is False
        assert (await eng.db.account.find_one({"_id": "main"}))["balance"] == pytest.approx(bal + d["partial_gross"] - d["partial_fees"])
        assert await eng.db.logs.count_documents({"message": {"$regex": "PARCIAL MANUAL LONG EURUSD: cerrado 50%"}}) == 1

    @pytest.mark.asyncio
    async def test_fx_real_uses_the_ticket_and_keeps_the_new_one(self, db):
        eng = await _fx(db, HFM)
        pos, _ = await eng.open_position("EURUSD", _sig(**BUY), FxSettings(), None)
        await eng.db.positions.update_one({}, {"$set": {"live": True, "order_id": "T1", "link_id": "L1", "qty": 0.4}})
        calls = []

        class B:
            async def lot(self, symbol):
                return {"min": 0.01, "step": 0.01}

            async def close_partial(self, pid, lots, link):
                calls.append((pid, lots, link))
                return {"order_id": "x", "commission": 0.3, "position_id": "T2"}
        eng.broker = B()
        out, why = await eng.partial_close(pos.id, 25)
        d = await eng.db.positions.find_one({})
        assert out and calls == [("T1", 0.1, "L1")] and d["qty"] == pytest.approx(0.3) and d["order_id"] == "T2" and d["partial_fees"] == pytest.approx(0.3)

    def test_route_and_model(self):
        import inspect
        from pydantic import ValidationError
        from models import PartialClose
        from routers import shared_routes
        assert PartialClose(pct=50).pct == 50
        for bad in (0, 4, 96, 100):
            with pytest.raises(ValidationError):
                PartialClose(pct=bad)
        assert '@router.post("/positions/{pos_id}/partial-close")' in inspect.getsource(shared_routes)
