"""Pagador saliente del x402-router: compra la llamada downstream en USDC.

Reglas de seguridad del dinero (aprendidas en el trust-guard):
  - Un cliente x402 NUEVO por compra (el SDK solo paga la 1.ª petición por sesión).
  - Tope duro por llamada: MAX_ROUTE_SPEND_ATOMIC.
  - Tope diario: DAILY_SPEND_CAP_USD (contador en memoria; se reinicia con el
    proceso — el tope por llamada sigue protegiendo siempre).
El caller del /route paga más de lo que cuesta el downstream, así que spamear
el router nunca drena la wallet: cada abuso deja margen, no pérdida.
"""
import base64
import json
import os
import time
from datetime import date
from urllib.parse import urlsplit, urlencode

USDC_DECIMALS = 6
MAX_ROUTE_SPEND_ATOMIC = int(os.getenv("MAX_ROUTE_SPEND_ATOMIC", "5000"))
DAILY_SPEND_CAP_USD = float(os.getenv("DAILY_SPEND_CAP_USD", "0.25"))


class DailyBudget:
    def __init__(self, cap_usd: float):
        self.cap_atomic = int(cap_usd * 10**USDC_DECIMALS)
        self.day = date.today().isoformat()
        self.spent_atomic = 0

    def _roll(self) -> None:
        today = date.today().isoformat()
        if today != self.day:
            self.day, self.spent_atomic = today, 0

    def can_spend(self, atomic: int) -> bool:
        self._roll()
        return self.spent_atomic + atomic <= self.cap_atomic

    def add(self, atomic: int) -> None:
        self._roll()
        self.spent_atomic += atomic

    @property
    def spent_usd(self) -> float:
        self._roll()
        return self.spent_atomic / 10**USDC_DECIMALS


BUDGET = DailyBudget(DAILY_SPEND_CAP_USD)


def _tx_from_headers(headers) -> str | None:
    raw = headers.get("x-payment-response")
    if not raw:
        return None
    try:
        payload = json.loads(base64.b64decode(raw))
        return payload.get("transaction")
    except Exception:
        return None


async def buy(resource: str, payer_key: str, price_atomic: int,
              method: str = "POST", payload: dict | list | None = None,
              query_params: dict | None = None) -> dict:
    """Compra real de la llamada downstream; devuelve status, cuerpo y tx."""
    if price_atomic > MAX_ROUTE_SPEND_ATOMIC:
        return {"performed": False,
                "reason": f"price {price_atomic} exceeds per-call cap "
                          f"{MAX_ROUTE_SPEND_ATOMIC} (atomic USDC)"}

    from eth_account import Account
    from x402.clients.httpx import x402HttpxClient

    p = urlsplit(resource)
    base_url = f"{p.scheme}://{p.netloc}"
    query = p.query
    if query_params:
        extra = urlencode(query_params)
        query = f"{query}&{extra}" if query else extra
    path = p.path + (f"?{query}" if query else "")
    account = Account.from_key(payer_key)

    result = {"performed": True, "ok": False, "status": None, "latency_ms": None,
              "tx_hash": None, "spent_atomic": price_atomic,
              "spent_usd": price_atomic / 10**USDC_DECIMALS,
              "body": None, "error": None}
    t0 = time.monotonic()
    try:
        # Cliente nuevo por compra: el SDK x402 solo paga la primera petición
        # de cada sesión.
        async with x402HttpxClient(account=account, base_url=base_url,
                                   timeout=150) as client:
            if method == "GET":
                r = await client.get(path)
            else:
                r = await client.post(path,
                                      json=payload if payload is not None else {})
            body = (await r.aread()).decode(errors="replace")
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        return result
    result["latency_ms"] = round((time.monotonic() - t0) * 1000)
    result["status"] = r.status_code
    result["ok"] = 200 <= r.status_code < 300
    result["tx_hash"] = _tx_from_headers(r.headers)
    result["body"] = body
    if not result["ok"]:
        result["error"] = f"downstream returned HTTP {r.status_code}"
    return result
