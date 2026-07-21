"""Cortafuegos del gateway fiat (RapidAPI).

Las rutas /fiat/* no pasan por el middleware x402 (filtra por path exacto), así
que su única barrera es el X-RapidAPI-Proxy-Secret. Si ese secreto se filtrara,
un tercero consumiría DeepSeek a nuestra costa sin límite. Aquí van los dos
topes que cierran ese agujero:

  - Tope diario de coste LLM: solo cuentan los endpoints que llaman a DeepSeek
    (/extract y /ask). El resto es cómputo puro y no gasta.
  - Límite de peticiones por minuto: ventana deslizante sobre TODAS las
    llamadas fiat, para que una ráfaga no tumbe el free tier de Render.

Al superarse cualquiera de los dos se responde 429, que en RapidAPI no se le
cobra al cliente — mismo principio que los 4xx que no cobran en x402.

Limitación asumida y consciente: el estado vive en memoria y se reinicia con
cada despliegue de Render (igual que DailyBudget en router_payer.py). Esto es
un cortafuegos contra abuso, NO contabilidad: la facturación real la lleva
RapidAPI.
"""
import os
import threading
import time
from datetime import date

# Coste por llamada al modelo, en USD. Calculado sobre deepseek-v4-flash
# ($0.14/M input cache-miss, $0.28/M output) en el PEOR caso: entrada al tope de
# ASK_MAX_CHARS (~4.000 tokens) + 1.200 de salida ≈ $0.001. El uso normal ronda
# $0.0003, así que este default frena de más a propósito: es preferible cortar
# antes que descubrir el abuso en la factura.
LLM_COST_PER_CALL_USD = float(os.getenv("FIAT_LLM_COST_PER_CALL", "0.001"))
DAILY_LLM_CAP_USD = float(os.getenv("FIAT_DAILY_LLM_CAP_USD", "2.00"))
RATE_PER_MIN = int(os.getenv("FIAT_RATE_PER_MIN", "120"))

# Endpoints del catálogo fiat que gastan en el proveedor del modelo.
LLM_PATHS = {"/fiat/extract", "/fiat/ask"}


class FiatGuard:
    """Topes diario (coste LLM) y por minuto (peticiones). Thread-safe."""

    def __init__(self, daily_cap_usd: float, rate_per_min: int):
        self.daily_cap_usd = daily_cap_usd
        self.rate_per_min = rate_per_min
        self._lock = threading.Lock()
        self._day = date.today().isoformat()
        self._llm_spent_usd = 0.0
        self._hits: list[float] = []  # timestamps de la ventana de 60 s

    def _roll_day(self) -> None:
        today = date.today().isoformat()
        if today != self._day:
            self._day, self._llm_spent_usd = today, 0.0

    def check(self, path: str) -> tuple[bool, str]:
        """Registra la llamada y decide si se permite.

        Devuelve (permitida, motivo). El motivo va vacío si se permite.
        """
        now = time.monotonic()
        with self._lock:
            self._roll_day()

            # ventana deslizante de 60 s
            self._hits = [t for t in self._hits if now - t < 60]
            if len(self._hits) >= self.rate_per_min:
                return False, (f"límite de {self.rate_per_min} peticiones por "
                               "minuto alcanzado; reintenta en unos segundos")
            self._hits.append(now)

            if path in LLM_PATHS:
                if self._llm_spent_usd + LLM_COST_PER_CALL_USD > self.daily_cap_usd:
                    return False, (f"tope diario de coste del modelo "
                                   f"(${self.daily_cap_usd:.2f}) alcanzado; "
                                   "se restablece a las 00:00 UTC")
                self._llm_spent_usd += LLM_COST_PER_CALL_USD

            return True, ""

    def snapshot(self) -> dict:
        """Estado para /health (no muta contadores salvo el roll de día)."""
        now = time.monotonic()
        with self._lock:
            self._roll_day()
            recent = len([t for t in self._hits if now - t < 60])
            return {
                "llm_spent_today_usd": round(self._llm_spent_usd, 4),
                "llm_daily_cap_usd": self.daily_cap_usd,
                "rate_per_min": self.rate_per_min,
                "hits_last_min": recent,
            }


GUARD = FiatGuard(DAILY_LLM_CAP_USD, RATE_PER_MIN)
