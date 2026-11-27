"""1h bots (Bybit, HotForex): after the candle-close scan, N follow-up scans every M minutes inside the same hour. They re-check the
closed-candle signals that were not executed, adding the live (still forming) 1h candle to the AI context, clearly labelled as such."""
import time
from datetime import datetime, timezone

HOUR = 3600
WINDOW_SEC = 240  # a follow-up missed by more than this (restart, long scan) is skipped


def enabled(engine, settings) -> bool:
    return (getattr(engine, "followups", False) and not getattr(engine, "candle_only", False) and settings.timeframe == "1h"
            and int(getattr(settings, "followup_scans", 0) or 0) > 0)


def times(now: float, delay: float, n: int, every: int) -> list:
    """Follow-up timestamps of the current hour: close scan (hh:00 + delay) + k × every minutes, never past the next close."""
    start = now - now % HOUR + delay
    return [start + k * every * 60 for k in range(1, n + 1) if k * every < 60]


def pending(now: float, delay: float, settings, last: float):
    """(index, total, ts) of the follow-up that is due now, or None."""
    ts = times(now, delay, int(settings.followup_scans), int(settings.followup_every_min))
    due = [t for t in ts if t <= now < t + WINDOW_SEC and t > last]
    return (ts.index(due[-1]) + 1, len(ts), due[-1]) if due else None


def next_ts(now: float, delay: float, settings):
    ts = [t for t in times(now, delay, int(settings.followup_scans), int(settings.followup_every_min)) if t > now]
    return ts[0] if ts else None


async def pairs_to_skip(engine, settings):
    """None = «Máx. posiciones» reached (the whole follow-up is skipped); otherwise the pairs with an open position or opened this hour."""
    docs = await engine.db.positions.find({}, {"symbol": 1, "horizon": 1, "exit_mode": 1}).to_list(300)
    docs += await engine.db.pending_entries.find({}, {"symbol": 1, "horizon": 1}).to_list(100)  # queued maker entries hold the pair and a slot
    slots = [d for d in docs if d.get("exit_mode") != "cross" and d.get("horizon", "short") != "long"]
    if len(slots) >= settings.max_positions:
        return None
    hour = datetime.fromtimestamp(time.time() - time.time() % HOUR, timezone.utc).isoformat()
    return {d["symbol"] for d in docs} | set(await engine.db.trades.distinct("symbol", {"opened_at": {"$gte": hour}}))


def live_candle(df, price: float = None, now: float = None, tf: str = "1h") -> dict:
    """The forming candle (last row of df) updated with the live price; ATR from the last closed candle."""
    row, atr = df.iloc[-1], float(df.iloc[-2]["atr"])
    atr = atr if atr == atr and atr > 0 else 0.0  # NaN with a short history
    o, h, lo, c = float(row["open"]), float(row["high"]), float(row["low"]), float(price or row["close"])
    h, lo = max(h, c), min(lo, c)
    minute = max(1, min(60, int(((now or time.time()) - int(row["ts"]) / 1000) // 60) + 1))
    rng = h - lo
    up, down, body = h - max(o, c), min(o, c) - lo, abs(c - o)
    pct = (lambda x: round(x / rng * 100)) if rng > 0 else (lambda x: 0)  # noqa: E731
    if rng > 0 and up >= 0.5 * rng and (not atr or rng >= 0.3 * atr):
        rej = f"rechazo bajista en vivo: mecha superior del {pct(up)} % del rango"
    elif rng > 0 and down >= 0.5 * rng and (not atr or rng >= 0.3 * atr):
        rej = f"rechazo alcista en vivo: mecha inferior del {pct(down)} % del rango"
    else:
        rej = "sin rechazo claro en vivo"
    return {"label": f"vela {tf} en curso (minuto {minute}/60)", "minute": minute, "open": o, "high": h, "low": lo, "price": c,
            "direction": "alcista" if c > o else "bajista" if c < o else "plana", "range_atr": round(rng / atr, 2) if atr else None,
            "body_pct": pct(body), "upper_wick_pct": pct(up), "lower_wick_pct": pct(down), "rejection": rej}


def prompt_line(lc: dict) -> str:
    if not lc:
        return ""
    return (f"{lc['label'].upper()} — NO es una vela cerrada (todas las demás velas, indicadores y acción del precio son de velas CERRADAS): "
            f"apertura {lc['open']:.6g} · máximo {lc['high']:.6g} · mínimo {lc['low']:.6g} · precio actual {lc['price']:.6g} · {lc['direction']}"
            + (f" · rango {lc['range_atr']} ATR" if lc.get("range_atr") is not None else "")
            + f" · cuerpo {lc['body_pct']} % · mecha sup. {lc['upper_wick_pct']} % · mecha inf. {lc['lower_wick_pct']} % · {lc['rejection']}. "
            "Valora el rechazo en vivo sabiendo que la vela aún puede cambiar antes de cerrar.\n")
