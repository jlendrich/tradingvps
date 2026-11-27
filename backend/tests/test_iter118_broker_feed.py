"""Iter118 — REAL mode reads every market datum from the broker (MT bridge): candles with tick volume for the analysis, bid/ask quotes, real
spread and day change. A pair whose broker candles are missing gets no new entries (never a silent Yahoo fallback); paper keeps Yahoo.
The broker talks in-process to scripts/mt_bridge_mock.py."""
import sys
import time
import httpx
import pytest

sys.path.insert(0, "/app/scripts")
import mt_bridge_mock as mock  # noqa: E402
import funnel  # noqa: E402
from fx.engine_feed import FeedMixin, MIN_BROKER_BARS, TICK_STALE_SEC  # noqa: E402
from fx.live import FxLiveBroker  # noqa: E402
from fx.broker import HFM  # noqa: E402
from fx.config import FxConfig  # noqa: E402
from fx.symbols import SPECS  # noqa: E402
from indicators import build_df  # noqa: E402

URL = "http://bridge.test"
YAHOO_GOLD = 2680.0     # GC=F futures premium over the broker's spot
BROKER_GOLD = 2650.0


class FakeMarket:
    def __init__(self):
        self.tickers = {"XAUUSD": {"symbol": "XAUUSD", "price": YAHOO_GOLD, "change_pct": 0.1, "spread": 0.3},
                        "EURUSD": {"symbol": "EURUSD", "price": 1.2, "change_pct": 0.0, "spread": 0.00012}}
        self.kline_calls = []

    async def fetch_klines(self, symbol, tf, limit=300, cache_sec=30):
        self.kline_calls.append((symbol, tf))
        base = self.tickers.get(symbol, {}).get("price", 1.0)
        now = int(time.time()) // 3600 * 3600
        return [[(now - (limit - i) * 3600) * 1000, base, base * 1.001, base * 0.999, base, 0.0] for i in range(limit)]


class Eng(FeedMixin):
    bk, bl = "HFM", "HotForex"

    def __init__(self, live=True, broker=True, state="DEPLOYED"):
        self.live = live
        self.market = FakeMarket()
        self.cfg = FxConfig(hfm_state=state, hfm_connection="CONNECTED")
        self.broker = FxLiveBroker(URL, mock.TOKEN, "hfm_v2", "b", None, HFM.suffixes, HFM.symbol_aliases,
                                   transport=httpx.ASGITransport(app=mock.app)) if broker else None

    @staticmethod
    def _df(rows, atr_period=14):
        return build_df(rows, atr_period)


@pytest.fixture(autouse=True)
def _fresh():
    mock.reset()
    mock.state["prices"].update({s: {"XAUUSD": BROKER_GOLD, "XAGUSD": 31.0}.get(s, 150.0 if s.endswith("JPY") else 1.1) for s in SPECS})


class TestRealCandles:
    @pytest.mark.asyncio
    async def test_real_uses_broker_candles_with_tick_volume(self):
        e = Eng()
        df, vol = await e.funnel_frame("XAUUSD", "1h")
        assert vol is True and len(df) >= MIN_BROKER_BARS and df["volume"].iloc[-5:].sum() > 0
        assert abs(float(df["close"].iloc[-1]) - BROKER_GOLD) / BROKER_GOLD < 0.02      # broker spot, not the futures price
        assert e.market.kline_calls == []                                              # Yahoo never asked
        assert any("GOLDb" in r and "candles" in r for r in mock.state["requests"])    # HFM Zero alias reaches the bridge
        f = e.data_feed(["XAUUSD"], "1h")
        assert f["signals"] == "broker" and f["volume"] is True and f["required"] is True and f["blocked"] == []

    @pytest.mark.asyncio
    async def test_real_without_broker_candles_blocks_the_pair_no_yahoo(self):
        e = Eng()
        mock.state["connected"] = False
        with pytest.raises(RuntimeError) as err:
            await e.funnel_frame("EURUSD", "1h")
        assert "sin entradas nuevas" in str(err.value)
        assert e.market.kline_calls == []
        f = e.data_feed(["EURUSD"], "1h")
        assert f["signals"] == "none" and f["blocked"][0]["symbol"] == "EURUSD"

    @pytest.mark.asyncio
    async def test_real_too_few_candles_blocks(self, monkeypatch):
        e = Eng()

        async def short(*a, **k):
            return [[1, 1, 1, 1, 1, 1]] * 50
        monkeypatch.setattr(e.broker, "candles", short)
        with pytest.raises(RuntimeError) as err:
            await e.funnel_frame("EURUSD", "1h")
        assert "50 velas" in str(err.value) and e.market.kline_calls == []

    @pytest.mark.asyncio
    async def test_real_ignores_state_label_and_chart_backoff(self):
        e = Eng(state="")                      # bridge without a «state» field: REAL still reads the broker
        e.broker.chart_fail_ts = time.time()
        _, vol = await e.funnel_frame("EURUSD", "1h")
        assert vol is True and e.market.kline_calls == []

    @pytest.mark.asyncio
    async def test_review_klines_real_from_broker(self):
        e = Eng()
        rows = await e.review_klines("XAUUSD", "1h", 30)
        assert len(rows) == 30 and e.market.kline_calls == []
        mock.state["connected"] = False
        e.broker._kcache.clear()
        with pytest.raises(RuntimeError):
            await e.review_klines("XAUUSD", "1h", 31)
        assert e.market.kline_calls == []


class TestRealQuotes:
    @pytest.mark.asyncio
    async def test_broker_quotes_override_yahoo(self):
        e = Eng()
        await e.refresh_broker_ticks(["XAUUSD", "EURUSD"])
        t = e.tickers["XAUUSD"]
        assert t["source"] == "broker" and abs(t["price"] - BROKER_GOLD) < 1 and t["ask"] > t["bid"]
        assert t["spread"] == pytest.approx(t["ask"] - t["bid"])
        assert t["change_pct"] is not None and t["low"] <= t["price"] <= t["high"]
        assert e.funnel_price("XAUUSD", 1.0) == t["price"] and e.funnel_ticker("XAUUSD")["source"] == "broker"
        assert e.tickers["EURUSD"]["source"] == "broker"
        f = e.data_feed(["XAUUSD", "EURUSD"], "1h")
        assert f["prices"] == "broker" and f["no_quote"] == []

    @pytest.mark.asyncio
    async def test_stale_quote_in_real_never_falls_back_to_yahoo(self):
        e = Eng()
        await e.refresh_broker_ticks(["XAUUSD"])
        e.broker_ticks["XAUUSD"]["_ts"] -= TICK_STALE_SEC + 1
        assert e.funnel_price("XAUUSD", 2649.5) == 2649.5           # candle close from the broker, not Yahoo's 2680
        assert e.funnel_ticker("XAUUSD") == {}
        assert e.data_feed(["XAUUSD"], "1h")["prices"] == "none"

    @pytest.mark.asyncio
    async def test_quote_errors_are_reported(self, monkeypatch):
        e = Eng()
        from fx.bridge import BridgeError

        async def down(*a, **k):
            raise BridgeError(0, "El puente MT no responde: ReadTimeout", kind="transient")
        monkeypatch.setattr(e.broker.api, "tick", down)
        await e.refresh_broker_ticks(["XAUUSD"], force=True)
        f = e.data_feed(["XAUUSD"], "1h")
        assert f["prices"] == "none" and f["no_quote"][0]["symbol"] == "XAUUSD"

    def test_funnel_price_helpers_use_engine_feed(self):
        e = Eng()
        e.broker_ticks = {"XAUUSD": {"price": 2651.0, "spread": 0.2, "source": "broker", "_ts": time.time()}}
        assert funnel._price(e, "XAUUSD", 1.0) == 2651.0 and funnel._ticker(e, "XAUUSD")["spread"] == 0.2

        class Crypto:   # engines without their own feed keep the market tickers
            market = FakeMarket()
        assert funnel._price(Crypto(), "XAUUSD", 1.0) == YAHOO_GOLD


class TestPaper:
    @pytest.mark.asyncio
    async def test_paper_without_bridge_uses_yahoo(self):
        e = Eng(live=False, broker=False)
        df, vol = await e.funnel_frame("EURUSD", "1h")
        assert vol is False and e.market.kline_calls == [("EURUSD", "1h")]
        assert e.tickers["XAUUSD"]["price"] == YAHOO_GOLD and e.funnel_price("XAUUSD", 1.0) == YAHOO_GOLD
        f = e.data_feed(["EURUSD"], "1h")
        assert f["signals"] == "yahoo" and f["prices"] == "yahoo" and f["required"] is False

    @pytest.mark.asyncio
    async def test_paper_with_connected_bridge_prefers_broker_but_falls_back(self):
        e = Eng(live=False)
        _, vol = await e.funnel_frame("EURUSD", "1h")
        assert vol is True and e.market.kline_calls == []
        mock.state["connected"] = False
        e.broker._kcache.clear()
        _, vol = await e.funnel_frame("EURUSD", "4h")
        assert vol is False and e.market.kline_calls == [("EURUSD", "4h")]
        assert e.data_feed(["EURUSD"], "4h")["signals"] == "yahoo"
