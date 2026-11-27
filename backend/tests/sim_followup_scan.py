# Simulation: one Bybit follow-up scan (scan_once(followup=(1,5))) against a scratch DB, with stubbed strategy/AI/open
import asyncio
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
from motor.motor_asyncio import AsyncIOMotorClient
import engine as eng_mod
import ai_funnel
import ai_rules

SCRATCH = "TEST_scratch_followup_sim"


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[SCRATCH]
    await client.drop_database(SCRATCH)  # scratch DB only, created by this script
    e = eng_mod.TradingEngine(db)
    e.running = True
    from models import Settings
    await db.settings.insert_one({"_id": "main", **Settings().model_dump()})
    s = await e.get_settings()
    assert s.followup_scans == 5 and s.followup_every_min == 5
    s.symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    s.max_positions = 5
    s.htf_filter = False
    s.entry_order_type = "market"
    captured, opened = [], []

    def fake_eval(df, i, *a, **k):
        row = df.iloc[i]
        p, atr = float(row["close"]), float(row["atr"])
        return {"strategy": "trend_ema", "side": "long", "key_ts": int(row["ts"]), "score": 3, "price": p, "sl": p - 2 * atr, "tp": p + 4 * atr,
                "default_sl": p - 2 * atr, "default_tp": p + 4 * atr, "reasons": ["sim"], "indicators": {"atr": atr}, "atr": atr}

    async def fake_rules(*a, **k):
        return [{"id": "r1", "text": "regla sim"}]

    async def fake_check(symbol, tf, sig, ctx, rules):
        captured.append((symbol, ctx))
        return {"model": "sim", "ms": 1, "violates": False, "rule": None, "reason": "", "sl": None, "tp": None}

    async def fake_open(symbol, sig, settings, sid, **k):
        opened.append(symbol)
        return {"id": "x"}

    async def noop(*a, **k):
        return None

    eng_mod.evaluate = fake_eval
    ai_rules.active = fake_rules
    ai_funnel.rules_check_retry = fake_check
    e.open_position = fake_open
    e.tg = noop

    async def fake_acc():
        return {"daily_paused": False, "equity": 1000.0, "daily_pnl_pct": 0.0}
    e.account_summary = fake_acc
    shadows = []
    eng_mod.open_shadow = lambda *a, **k: shadows.append(a) or noop()
    eng_mod.price_action.late_entry = lambda *a, **k: ""  # make sure the signal is not dropped as late
    await e.market.fetch_tickers() if hasattr(e.market, "fetch_tickers") else None
    # ETHUSDT already has an open position → must be skipped
    from models import Position
    pdoc = Position(symbol="ETHUSDT", side="long", strategy="trend_ema", entry_price=3000.0, qty=0.01, leverage=5, margin=6.0, sl=2900.0, tp=3200.0, initial_sl=2900.0, atr=30.0, fees=0.0, mark_price=3000.0).to_mongo() if hasattr(Position, "to_mongo") else None
    await db.positions.insert_one(pdoc)
    await e.scan_once(s, normal=False, followup=(1, 5))
    print("captured", [c[0] for c in captured], "opened", opened, "shadows", len(shadows))
    for sym, ctx in captured:
        print(sym, "live_candle:", ctx.get("live_candle", {}).get("label"), "| scan:", ctx.get("scan"), "| rejection:", ctx.get("live_candle", {}).get("rejection"))
    keys = [d["key"] for d in await db.signals.find({}, {"key": 1}).to_list(10)]
    print("signal keys", keys)
    assert "ETHUSDT" not in [c[0] for c in captured], "ETH should be skipped"
    assert all(k.endswith(":seg1") for k in keys) and keys
    assert not shadows
    # second identical follow-up with the same index must be de-duplicated
    n = len(captured)
    await e.scan_once(s, normal=False, followup=(1, 5))
    print("dup re-run new captures:", len(captured) - n)
    # max positions reached → whole follow-up skipped
    s.max_positions = 1
    n = len(captured)
    await e.scan_once(s, normal=False, followup=(2, 5))
    logs = [d["message"] for d in await db.logs.find({}, {"message": 1}).to_list(200)]
    print("maxpos new captures:", len(captured) - n, "| skip log:", [m for m in logs if "omitido" in m])
    print("follow-up logs:", [m for m in logs if "Seguimiento" in m])
    # prompt contains the live candle line
    import followup
    print("PROMPT:", followup.prompt_line(captured[0][1]["live_candle"])[:200])
    await client.drop_database(SCRATCH)
    print("SIM OK")

asyncio.run(main())
