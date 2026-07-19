"""Datos de mercado en vivo para los endpoints de betting-intel.

Fuente primaria: Binance Vision (data-api.binance.vision), el mirror público
de datos de mercado de Binance — sin API key y sin bloqueo geográfico (Render
corre en EE.UU., donde api.binance.com responde 451); fallback api.binance.com.
Cálculo en Python puro sobre velas OHLCV; sin dependencias nuevas (httpx ya
viene con el SDK x402).
"""
import statistics
from datetime import datetime, timezone

import httpx

BINANCE_HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]
KLINES_PATH = "/api/v3/klines"

# Velas por intervalo: suficiente historia para las features sin payloads gordos.
LIMITS = {"15m": 192, "1h": 168, "4h": 180, "1d": 120}
VALID_INTERVALS = tuple(LIMITS)

# Alias habituales → símbolo Binance; cualquier otro activo se intenta <ASSET>USDT.
ALIASES = {
    "BTC": "BTCUSDT", "BITCOIN": "BTCUSDT", "XBT": "BTCUSDT",
    "ETH": "ETHUSDT", "ETHEREUM": "ETHUSDT", "ETHER": "ETHUSDT",
    "SOL": "SOLUSDT", "SOLANA": "SOLUSDT",
    "TAO": "TAOUSDT", "BITTENSOR": "TAOUSDT",
    "DOGE": "DOGEUSDT", "DOGECOIN": "DOGEUSDT",
    "XRP": "XRPUSDT", "RIPPLE": "XRPUSDT",
    "ADA": "ADAUSDT", "CARDANO": "ADAUSDT",
    "BNB": "BNBUSDT", "LINK": "LINKUSDT", "CHAINLINK": "LINKUSDT",
    "AVAX": "AVAXUSDT", "AVALANCHE": "AVAXUSDT",
    "LTC": "LTCUSDT", "LITECOIN": "LTCUSDT",
    "DOT": "DOTUSDT", "POLKADOT": "DOTUSDT",
}
QUOTES = ("USDT", "USDC", "FDUSD", "BUSD")


class MarketDataError(Exception):
    """Error de datos de mercado; el endpoint lo mapea a su HTTP status.

    status 422 = culpa de la entrada (símbolo desconocido, fecha sin vela);
    status 503 = la fuente de datos no respondió. Ninguno de los dos cobra
    (x402 solo liquida respuestas 2xx).
    """

    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.status = status


def normalize_symbol(raw: str) -> str:
    s = "".join(c for c in raw.upper() if c.isalnum())
    if not s:
        raise MarketDataError("empty symbol", 422)
    if s in ALIASES:
        return ALIASES[s]
    if s.endswith(QUOTES):
        return s
    return s + "USDT"


def get_klines(symbol: str, interval: str, limit: int | None = None,
               start_ms: int | None = None) -> tuple[list[dict], str]:
    """Velas OHLCV de Binance. Devuelve (velas, url_fuente)."""
    params = {"symbol": symbol, "interval": interval,
              "limit": limit or LIMITS.get(interval, 168)}
    if start_ms is not None:
        params["startTime"] = start_ms
    last_error = None
    for host in BINANCE_HOSTS:
        url = host + KLINES_PATH
        try:
            resp = httpx.get(url, params=params, timeout=10)
            if resp.status_code in (400, 404):
                raise MarketDataError(
                    f"unknown symbol {symbol!r} on Binance spot; send e.g. "
                    "'BTC', 'ethereum' or a full pair like 'SOLUSDT'", 422)
            resp.raise_for_status()
            rows = resp.json()
            if not isinstance(rows, list):
                raise ValueError("unexpected payload")
            candles = [{
                "open_time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
                "low": float(r[3]), "close": float(r[4]), "volume": float(r[5]),
                "close_time": int(r[6]),
            } for r in rows]
            if not candles:
                raise MarketDataError(
                    f"Binance has no {interval} candles for {symbol} "
                    "in the requested window", 422)
            return candles, str(resp.request.url)
        except MarketDataError:
            raise
        except Exception as e:  # red caída / 5xx / payload raro → probar fallback
            last_error = e
    raise MarketDataError(f"market data source unavailable ({last_error})", 503)


def compute_features(candles: list[dict]) -> dict:
    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    price = closes[-1]

    def sma(n: int) -> float | None:
        return sum(closes[-n:]) / n if len(closes) >= n else None

    def pct_vs(base: float | None) -> float | None:
        return round((price - base) / base * 100, 3) if base else None

    # RSI-14 clásico con medias simples (documentado así; sin suavizado Wilder).
    rsi14 = None
    if len(closes) >= 15:
        deltas = [closes[i] - closes[i - 1] for i in range(len(closes) - 14, len(closes))]
        gains = sum(d for d in deltas if d > 0) / 14
        losses = sum(-d for d in deltas if d < 0) / 14
        rsi14 = 100.0 if losses == 0 else round(100 - 100 / (1 + gains / losses), 2)

    returns = [(closes[i] - closes[i - 1]) / closes[i - 1] * 100
               for i in range(1, len(closes)) if closes[i - 1] > 0]
    volatility = round(statistics.stdev(returns), 3) if len(returns) >= 2 else None

    peak, drawdown = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        drawdown = min(drawdown, (c - peak) / peak * 100)

    lo, hi = min(lows), max(highs)
    range_position = round((price - lo) / (hi - lo), 3) if hi > lo else None

    recent_vol = sum(volumes[-12:]) / min(12, len(volumes))
    avg_vol = sum(volumes) / len(volumes)
    volume_trend = round(recent_vol / avg_vol, 3) if avg_vol > 0 else None

    streak = 0
    for i in range(len(closes) - 1, 0, -1):
        d = closes[i] - closes[i - 1]
        if d == 0:
            break
        if streak == 0:
            streak = 1 if d > 0 else -1
        elif (d > 0) == (streak > 0):
            streak += 1 if streak > 0 else -1
        else:
            break

    sma20, sma50 = sma(20), sma(50)
    return {
        "candles": len(candles),
        "price": price,
        "rsi14": rsi14,
        "sma20": sma20,
        "sma50": sma50,
        "price_vs_sma20_pct": pct_vs(sma20),
        "price_vs_sma50_pct": pct_vs(sma50),
        "trend_window_pct": pct_vs(closes[0]),
        "momentum_3_pct": pct_vs(closes[-4]) if len(closes) >= 4 else None,
        "volatility_pct": volatility,
        "drawdown_max_pct": round(drawdown, 3),
        "range_position_0_1": range_position,
        "volume_trend": volume_trend,
        "streak": streak,
        "last_candle_utc": datetime.fromtimestamp(
            candles[-1]["open_time"] / 1000, tz=timezone.utc).isoformat(),
    }


def features_for(symbol_raw: str, interval: str) -> dict:
    if interval not in VALID_INTERVALS:
        raise MarketDataError(
            f"interval must be one of {list(VALID_INTERVALS)}", 422)
    symbol = normalize_symbol(symbol_raw)
    candles, source = get_klines(symbol, interval)
    return {"symbol": symbol, "interval": interval,
            **compute_features(candles), "source": source}


# --------------------------------------------------------------- resolución (/resolve)
RESOLUTION_RULE = ("outcome computed on the Binance spot DAILY candle CLOSE for the "
                   "stated UTC date (00:00-24:00 UTC)")


def resolve_threshold(symbol_raw: str, comparator: str, threshold: float,
                      date_str: str) -> dict:
    """¿El cierre diario UTC de `date_str` cumplió `close <comparator> threshold`?"""
    if comparator not in (">", ">=", "<", "<="):
        raise MarketDataError("comparator must be one of > >= < <=", 422)
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise MarketDataError("date must be YYYY-MM-DD", 422)
    today = datetime.now(timezone.utc).date()
    if day.date() >= today:
        raise MarketDataError(
            f"the {date_str} UTC daily candle has not closed yet; "
            "resolvable after 00:00 UTC of the next day", 422)

    symbol = normalize_symbol(symbol_raw)
    candles, source = get_klines(symbol, "1d", limit=1,
                                 start_ms=int(day.timestamp() * 1000))
    candle = candles[0]
    if candle["open_time"] != int(day.timestamp() * 1000):
        raise MarketDataError(
            f"Binance has no {symbol} daily candle for {date_str} "
            "(asset not yet listed that day?)", 422)

    close = candle["close"]
    held = {"<": close < threshold, "<=": close <= threshold,
            ">": close > threshold, ">=": close >= threshold}[comparator]
    return {
        "symbol": symbol,
        "date_utc": date_str,
        "comparator": comparator,
        "threshold": threshold,
        "observed_close": close,
        "outcome": "YES" if held else "NO",
        "rule": RESOLUTION_RULE,
        "candle": candle,
        "source": source,
    }
