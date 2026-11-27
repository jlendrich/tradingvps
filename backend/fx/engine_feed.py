"""FxEngine mixin · market data feed: where candles, prices and spread come from.

REAL mode  → everything the analysis and the orders use comes from the MetaTrader bridge (broker candles with tick volume, bid/ask, real
             spread). If the bridge cannot deliver a pair's candles the pair is skipped (no new entries) instead of silently using Yahoo.
PAPER mode → broker candles/quotes while a bridge account is connected (optional), Yahoo Finance otherwise.
The feed of every frame and quote is recorded so the panel can show the real source (status.data_feed).
"""
import time
import asyncio
import funnel
from fx.engine_core import logger
from fx.market import TF_SECONDS

MIN_BROKER_BARS = 220      # EMA200 + warm-up
TICK_STALE_SEC = 30        # a broker quote older than this is not used for trading decisions
TICK_REFRESH_SEC = 5


class FeedMixin:
    # ---------- state ----------
    @property
    def _ticks(self) -> dict:
        return self.__dict__.setdefault("broker_ticks", {})

    @property
    def _feed(self) -> dict:
        return self.__dict__.setdefault("feed", {})

    def broker_feed_on(self) -> bool:
        """True when broker data should be used: always in REAL; in paper only while the bridge account looks connected (60 s back-off after a failure)."""
        b = self.broker
        if not b:
            return False
        if self.live:
            return True
        return bool(self.cfg and self.cfg.hfm_state == "DEPLOYED" and time.time() - b.chart_fail_ts > 60)

    def broker_tick(self, symbol: str) -> dict | None:
        t = self._ticks.get(symbol)
        return t if t and time.time() - t["_ts"] < TICK_STALE_SEC else None

    @property
    def tickers(self) -> dict:
        """Prices used by the engine: Yahoo as the base layer (conversion pairs, paper without bridge) with fresh broker quotes on top."""
        base = self.market.tickers
        if not self._ticks or not self.broker_feed_on():
            return base
        fresh = {s: t for s, t in self._ticks.items() if time.time() - t["_ts"] < TICK_STALE_SEC}
        return {**base, **fresh} if fresh else base

    # ---------- quotes ----------
    async def refresh_broker_ticks(self, symbols: list, force: bool = False):
        if not self.broker_feed_on():
            return
        now = time.time()
        if not force:
            if now - self.__dict__.get("_ticks_ts", 0.0) < TICK_REFRESH_SEC - 0.5:
                return
            self._ticks_ts = now
        sem = asyncio.Semaphore(4)

        async def one(sym):
            async with sem:
                try:
                    self._ticks[sym] = await self.broker.ticker(sym, cache_sec=TICK_REFRESH_SEC - 1)
                    self.__dict__.setdefault("tick_errors", {}).pop(sym, None)
                except Exception as e:
                    self.__dict__.setdefault("tick_errors", {})[sym] = str(getattr(e, "msg", e))[:140]
        await asyncio.gather(*(one(s) for s in symbols))

    def funnel_price(self, symbol: str, fallback: float) -> float:
        """Current price for the analysis: the broker quote when broker data is in use (REAL: never Yahoo → the candle close if no fresh quote)."""
        t = self.broker_tick(symbol) if self.broker_feed_on() else None
        if t:
            return t["price"]
        if self.live:
            return fallback
        return self.market.tickers.get(symbol, {}).get("price") or fallback

    def funnel_ticker(self, symbol: str) -> dict:
        if self.broker_feed_on():
            t = self.broker_tick(symbol)
            if t or self.live:
                return t or {}
        return self.market.tickers.get(symbol, {})

    # ---------- candles ----------
    def _note_feed(self, symbol: str, tf: str, source: str, bars: int = 0, volume: bool = False, error: str = None):
        self._feed[(symbol, tf)] = {"symbol": symbol, "tf": tf, "source": source, "bars": bars, "volume": volume, "error": error, "live": self.live, "ts": time.time()}

    async def funnel_frame(self, symbol: str, tf: str, limit: int = 300):
        """Candles for the analysis. REAL: broker candles (with tick volume) or an error → the pair gets no new entries this cycle.
        Paper: broker candles while the bridge is connected, Yahoo (no volume → candle range) otherwise."""
        b = self.broker
        if self.live:
            if not b:
                self._note_feed(symbol, tf, "none", error="modo REAL sin puente MT configurado")
                raise RuntimeError(f"modo REAL sin puente MT configurado · sin entradas nuevas en {symbol}")
            try:
                rows = await b.candles(symbol, tf, None, limit, cache_sec=funnel.fresh_sec(tf, 45))
            except Exception as e:
                msg = str(getattr(e, "msg", e))[:140]
                self._note_feed(symbol, tf, "none", error=msg)
                raise RuntimeError(f"velas {tf} de {self.bk} no disponibles ({msg}) · sin entradas nuevas en este par hasta que el puente las entregue")
            if not rows or len(rows) < MIN_BROKER_BARS:
                n = len(rows or [])
                self._note_feed(symbol, tf, "none", bars=n, error=f"solo {n} velas {tf} (mínimo {MIN_BROKER_BARS})")
                raise RuntimeError(f"el puente MT solo entregó {n} velas {tf} de {self.bk} (mínimo {MIN_BROKER_BARS}) · sin entradas nuevas en este par")
            vol = sum(r[5] for r in rows[-50:]) > 0
            self._note_feed(symbol, tf, "broker", len(rows), vol)
            return self._df(rows), vol
        if b and self.broker_feed_on():
            try:
                rows = await b.candles(symbol, tf, None, limit, cache_sec=funnel.fresh_sec(tf, 45))
                if rows and len(rows) >= MIN_BROKER_BARS:
                    vol = sum(r[5] for r in rows[-50:]) > 0
                    self._note_feed(symbol, tf, "broker", len(rows), vol)
                    return self._df(rows), vol
            except Exception as e:
                b.chart_fail_ts = time.time()
                logger.warning(f"velas {self.bk} {symbol} {tf}: {e} → usando Yahoo")
        rows = await self.market.fetch_klines(symbol, tf, limit, cache_sec=funnel.fresh_sec(tf, 60))
        self._note_feed(symbol, tf, "yahoo", len(rows), False)
        return self._df(rows), False

    async def review_klines(self, symbol: str, tf: str, limit: int) -> list:
        """Candles for the AI review of open positions: the broker's in REAL (same prices as the position), Yahoo in paper without bridge."""
        if self.live or self.broker_feed_on():
            try:
                rows = await self.broker.candles(symbol, tf, None, limit, cache_sec=60)
                if rows:
                    return rows
                raise RuntimeError("sin velas")
            except Exception as e:
                if self.live:
                    raise RuntimeError(f"velas {tf} de {self.bk} no disponibles para la revisión: {str(getattr(e, 'msg', e))[:120]}")
        return await self.market.fetch_klines(symbol, tf, limit, cache_sec=60)

    # ---------- panel ----------
    def data_feed(self, symbols: list, tf: str) -> dict:
        """Real source of the last analysis of every active pair at the entry timeframe + quotes, for the header badge."""
        horizon = max(TF_SECONDS.get(tf, 3600) * 3, 900)
        rows, now = [], time.time()
        for s in symbols:
            f = self._feed.get((s, tf))
            if f and f["live"] == self.live and now - f["ts"] < horizon:
                rows.append({k: f[k] for k in ("symbol", "source", "bars", "volume", "error")} | {"age_sec": int(now - f["ts"])})
        srcs = {r["source"] for r in rows if r["source"] != "none"}
        signals = None if not rows else ("broker" if srcs == {"broker"} else "yahoo" if srcs == {"yahoo"} else "mixed" if srcs else "none")
        on = self.broker_feed_on()
        quoted = [s for s in symbols if on and self.broker_tick(s)]
        prices = "broker" if quoted and len(quoted) == len(symbols) else "mixed" if quoted else ("none" if self.live else "yahoo")
        errs = self.__dict__.get("tick_errors", {})
        return {"required": self.live, "signals": signals, "prices": prices, "broker": self.bk, "tf": tf,
                "volume": any(r["volume"] for r in rows if r["source"] == "broker"),
                "blocked": [{"symbol": r["symbol"], "error": r["error"]} for r in rows if r["source"] == "none"],
                "no_quote": [{"symbol": s, "error": errs.get(s) or "sin cotización reciente"} for s in symbols if on and s not in quoted],
                "pairs": rows}
