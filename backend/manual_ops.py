"""Manual operations on open positions: partial close (all bots) and, in REAL Bybit (one-way mode), merging a manual order into the open position."""
import asyncio
from bson import ObjectId
from models import Position
from risk import FEE_RATE, SLIPPAGE, pnl, partial_qty
from fx import risk as fx_risk


async def _load(engine, pos_id: str) -> Position:
    doc = await engine.db.positions.find_one({"_id": ObjectId(pos_id)})
    if not doc:
        raise LookupError("Posición no encontrada (puede que ya se haya cerrado)")
    return Position.from_mongo(doc)


def _partial_updates(p: Position, part: float, gross: float, fee: float) -> dict:
    rest = round(p.qty - part, 8)
    return {"qty": rest, "margin": round(p.margin * rest / p.qty, 6), "fees": round(p.fees + fee, 6), "initial_qty": p.initial_qty or p.qty,
            "partial_gross": round(p.partial_gross + gross, 6), "partial_fees": round(p.partial_fees + fee, 6)}


async def _announce(engine, p: Position, pct: float, part: float, rest: float, exit_price: float, net: float, ccy: str, fmt, unit: str):
    tag = "REAL " if p.live else ""
    await engine.log("ORDER", f"PARCIAL MANUAL {tag}{p.side.upper()} {p.symbol}: cerrado {pct:g}% ({part:.6g}{unit}) @ {fmt(exit_price)} · PnL {net:+.2f} {ccy} · resto {rest:.6g}{unit}")
    await engine.tg(f"✂️ <b>PARCIAL MANUAL {tag}{p.side.upper()} {p.symbol}</b> @ {fmt(exit_price)}\nCerrado {pct:g}% ({part:.6g}{unit}) · PnL {net:+.2f} {ccy} · resto {rest:.6g}{unit}", "orders")


async def bybit_close_partial(engine, pos_id: str, pct: float) -> dict:
    """Close `pct` % of a Bybit (or Labs) position at market; the rest keeps its SL/TP and management."""
    async with engine.lock:
        p = await _load(engine, pos_id)
        lot = await engine.broker.lot(p.symbol) if p.live and engine.broker else None
        part = partial_qty(p.symbol, p.qty, pct, lot)
        if part <= 0:
            raise ValueError("El lote mínimo de Bybit no permite dividir la posición con ese porcentaje")
        price = (engine.market.tickers.get(p.symbol) or {}).get("price") or p.mark_price
        if p.live:
            if not engine.broker:
                raise ValueError("Modo REAL sin credenciales de Bybit")
            r = await engine.broker.close(p.symbol, p.side, part)
            exit_price, fee = r["exit"] or price, r["fee"]
            gross = pnl(p.side, p.entry_price, exit_price, part)
        else:
            exit_price = price * (1 - SLIPPAGE) if p.side == "long" else price * (1 + SLIPPAGE)
            fee = exit_price * part * FEE_RATE
            gross = pnl(p.side, p.entry_price, exit_price, part)
            await engine.db.account.update_one({"_id": "main"}, {"$inc": {"balance": gross - fee}})
        upd = _partial_updates(p, part, gross, fee)
        upd["unrealized_pnl"] = round(pnl(p.side, p.entry_price, price, upd["qty"]), 4)
        await engine.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": upd})
    await _announce(engine, p, pct, part, upd["qty"], exit_price, gross - fee, "USDT", lambda v: f"{v:.6g}", "")
    return {"id": p.id, "symbol": p.symbol, "closed_qty": part, "rest_qty": upd["qty"], "exit_price": exit_price, "pnl": round(gross - fee, 4)}


async def fx_close_partial(engine, pos_id: str, pct: float) -> dict:
    """Close `pct` % (in lots) of a HotForex / Pepperstone position; the rest keeps its SL/TP and management."""
    async with engine.lock:
        p = await _load(engine, pos_id)
        tickers = engine.market.tickers
        lot = await engine.broker.lot(p.symbol) if p.live and engine.broker else None
        part = fx_risk.partial_lots(p.qty, pct, lot)
        if part <= 0:
            raise ValueError("El lote mínimo del bróker no permite dividir la posición con ese porcentaje")
        price = (tickers.get(p.symbol) or {}).get("price") or p.mark_price
        gross = engine.pnl_ccy(p.symbol, p.side, p.entry_price, price, part, tickers)
        if p.live:
            if not engine.broker:
                raise ValueError("Modo REAL sin conexión con el bróker")
            r = await engine.broker.close_partial(p.order_id, part, p.link_id)
            fee = abs(r.get("commission") or 0)
        else:
            fee = fx_risk.spread_cost(p.symbol, part, tickers)
            await engine.db.account.update_one({"_id": "main"}, {"$inc": {"balance": gross - fee}})
        upd = _partial_updates(p, part, gross, fee)
        upd["unrealized_pnl"] = round(engine.pnl_ccy(p.symbol, p.side, p.entry_price, price, upd["qty"], tickers), 4)
        await engine.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": upd})
    from fx.symbols import fmt_price
    await _announce(engine, p, pct, part, upd["qty"], price, gross - fee, engine.ccy, lambda v: fmt_price(p.symbol, v), " lotes")
    return {"id": p.id, "symbol": p.symbol, "closed_qty": part, "rest_qty": upd["qty"], "exit_price": price, "pnl": round(gross - fee, 4)}


async def bybit_merge_live(engine, doc: dict, sig: dict, settings, manage: dict = None):
    """REAL Bybit (one-way mode): a manual order on a pair that is already open joins that position, as Bybit does.
    Same side → adds size (average entry; the new SL/TP apply to the whole position). Opposite side → reduces it, closes it,
    or closes it and opens the excess in the new direction. Returns (Position | None, text)."""
    p = Position.from_mongo(doc)
    if not engine.broker:
        raise ValueError("Modo REAL sin credenciales de Bybit")
    price = (engine.market.tickers.get(p.symbol) or {}).get("price") or sig["price"]
    size = await engine.live_size(p.symbol, price, sig["sl"], settings.risk_per_trade, settings.leverage)
    if not size:
        raise ValueError(getattr(engine, "_open_fail", None) or "tamaño por debajo del lote mínimo")
    q = size["qty"]
    if sig["side"] == p.side:
        async with engine.lock:
            r = await engine.broker.open(p.symbol, p.side, q, sig["sl"], sig["tp"], settings.leverage)
            add, fill = r["qty"], r["entry"] or price
            qty = round(p.qty + add, 8)
            entry = (p.entry_price * p.qty + fill * add) / qty
            upd = {"qty": qty, "initial_qty": round((p.initial_qty or p.qty) + add, 8), "entry_price": entry, "sl": sig["sl"], "tp": sig["tp"],
                   "initial_sl": sig["sl"], "breakeven": False, "margin": round(qty * entry / settings.leverage, 6), "fees": round(p.fees + r["fee"], 6),
                   "mark_price": price, **({"manage": manage} if manage else {})}
            await engine.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": upd})
        await engine.log("ORDER", f"AMPLIADA REAL {p.side.upper()} {p.symbol}: +{add:.6g} @ {fill:.6g} · tamaño {p.qty:.6g} → {qty:.6g} · entrada media {entry:.6g} · SL {sig['sl']:.6g} · TP {sig['tp']:.6g} (toda la posición)")
        await engine.tg(f"➕ <b>AMPLIADA REAL {p.side.upper()} {p.symbol}</b> @ {fill:.6g}\nTamaño {p.qty:.6g} → {qty:.6g} · entrada media {entry:.6g}\nSL {sig['sl']:.6g} · TP {sig['tp']:.6g}", "orders")
        return Position.from_mongo(await engine.db.positions.find_one({"_id": ObjectId(p.id)})), "ampliada"
    lot = await engine.broker.lot(p.symbol)
    if round(p.qty - q, 8) >= lot[0]:  # reduce: an opposite order nets against the open position
        res = await bybit_close_partial_qty(engine, p, q)
        return Position.from_mongo(await engine.db.positions.find_one({"_id": ObjectId(p.id)})), f"reducida {res['closed_qty']:.6g}"
    await engine.close_position(p.id, reason="MANUAL")
    rest = round(q - p.qty, 8)
    if rest < lot[0]:
        return None, "cerrada (la orden contraria cubría toda la posición)"
    await asyncio.sleep(0.5)
    pos = await engine.open_position(p.symbol, sig, settings, None, pair_limit=False, qty_override=rest, **({"manage": manage} if manage else {}))
    return pos, "cerrada y girada con el exceso" if pos else "cerrada; el exceso no se pudo abrir"


async def bybit_close_partial_qty(engine, p: Position, part: float) -> dict:
    pct = part / p.qty * 100
    async with engine.lock:
        r = await engine.broker.close(p.symbol, p.side, part)
        exit_price = r["exit"] or p.mark_price
        gross = pnl(p.side, p.entry_price, exit_price, part)
        upd = _partial_updates(p, part, gross, r["fee"])
        await engine.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": upd})
    await _announce(engine, p, round(pct, 1), part, upd["qty"], exit_price, gross - r["fee"], "USDT", lambda v: f"{v:.6g}", "")
    return {"closed_qty": part, "rest_qty": upd["qty"]}
