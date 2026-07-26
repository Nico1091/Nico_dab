"""Serie histórica propia del mercado x402.

POR QUÉ EXISTE ESTE MÓDULO. x402scan publica una **ventana móvil** de aproximadamente un mes:
lo que se cae por detrás desaparece y nadie lo guarda. El centro de operaciones toma un corte
diario y conserva la serie completa. Ése es el único dato del catálogo que **no se puede
replicar hacia atrás**: quien empiece a guardarlo en septiembre no tendrá julio jamás.

Todos los importes ya vienen convertidos de USDC atómico a dólares (x402scan publica en
unidades de 6 decimales). La verificación de que la conversión es correcta: la suma de los
tramos cuadra con el agregado global, y el ticket de dTelecom sale $2,14 contra el $2,13
publicado en los informes del ecosistema.

El fichero de datos se actualiza desde el repositorio `centro-operaciones`; este módulo solo
lee. Si el fichero falta o está corrupto, los endpoints responden 503 y NUNCA inventan una
cifra: un dato de mercado equivocado es peor que no tener dato.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DATA_PATH = Path(os.getenv("X402_MARKET_DATA",
                           str(Path(__file__).parent / "data" / "x402_market.json")))

_cache: dict | None = None
_cache_mtime: float | None = None


class SeriesError(Exception):
    """No hay datos con los que responder. Lleva el código HTTP que corresponde."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def load() -> dict:
    """Lee el fichero de serie, con caché invalidada por fecha de modificación."""
    global _cache, _cache_mtime
    try:
        mtime = DATA_PATH.stat().st_mtime
    except OSError:
        raise SeriesError(503, "market series not available yet")
    if _cache is None or _cache_mtime != mtime:
        try:
            _cache = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            raise SeriesError(503, "market series unreadable")
        _cache_mtime = mtime
    return _cache


def _norm(name: str) -> str:
    """`https://www.BlockRun.ai/` y `http://blockrun.ai` son el mismo vendedor."""
    n = name.strip().lower()
    for prefix in ("https://", "http://"):
        if n.startswith(prefix):
            n = n[len(prefix):]
    if n.startswith("www."):
        n = n[4:]
    return n.rstrip("/")


def _pct(nuevo: float | None, viejo: float | None) -> float | None:
    if not nuevo or not viejo:
        return None
    return round((nuevo / viejo - 1) * 100, 1)


def market_series(seller: str | None = None, limit: int = 0) -> dict:
    """Serie del mercado, o la ficha de un vendedor concreto con su posición.

    Sin `seller` devuelve la serie horaria completa del mercado. Con `seller`, la ficha de
    ese vendedor: volumen, llamadas, compradores únicos, ticket y en qué puesto está.
    """
    d = load()
    tramos = d.get("tramos") or []
    if not tramos:
        raise SeriesError(503, "market series is empty")

    if seller:
        objetivo = _norm(seller)
        vendedores = d.get("vendedores") or []
        fila = next((v for v in vendedores if _norm(v.get("vendedor", "")) == objetivo), None)
        if fila is None:
            # Se dice qué se buscó y cuántos hay: el comprador tiene que poder distinguir
            # "no existe" de "no lo tenemos indexado".
            raise SeriesError(404, f"seller '{seller}' not in the tracked top "
                                   f"{len(vendedores)} by volume")
        puesto = vendedores.index(fila) + 1
        return {
            "seller": fila.get("vendedor"),
            "rank_by_volume": puesto,
            "volume_usd": fila.get("volumen_usd"),
            "calls": fila.get("llamadas"),
            "unique_buyers": fila.get("compradores_unicos"),
            "ticket_usd": fila.get("ticket_usd"),
            "tracked_sellers": len(vendedores),
            "as_of": d.get("actualizado"),
        }

    serie = tramos[-limit:] if limit and limit > 0 else tramos
    return {
        "market": "x402",
        "hourly_buckets": len(serie),
        "from": serie[0].get("desde"),
        "to": serie[-1].get("desde"),
        "series": serie,
        "as_of": d.get("actualizado"),
    }


def market_report() -> dict:
    """Informe completo: foto, tendencia medida contra el propio histórico y concentración.

    La tendencia se calcula comparando la media de los primeros tramos con la de los
    últimos. Si no hay tramos suficientes para que la comparación signifique algo, se dice
    `insufficient_history` en vez de devolver un porcentaje inventado.
    """
    d = load()
    tramos = d.get("tramos") or []
    vendedores = d.get("vendedores") or []
    if not tramos:
        raise SeriesError(503, "market series is empty")

    ventana = max(1, min(8, len(tramos) // 4))
    tendencia: dict = {}
    if len(tramos) >= 8:
        primeros, ultimos = tramos[:ventana], tramos[-ventana:]

        def media(lote, clave):
            vals = [t.get(clave) for t in lote if isinstance(t.get(clave), (int, float))]
            return sum(vals) / len(vals) if vals else None

        for clave, nombre in (("transacciones", "transactions"),
                              ("volumen_usd", "volume_usd"),
                              ("compradores", "buyers"),
                              ("vendedores", "sellers")):
            a, b = media(primeros, clave), media(ultimos, clave)
            tendencia[nombre] = {
                "first_buckets_avg": round(a, 2) if a else None,
                "last_buckets_avg": round(b, 2) if b else None,
                "change_pct": _pct(b, a),
            }
    else:
        tendencia = {"insufficient_history": True,
                     "buckets_needed": 8, "buckets_available": len(tramos)}

    total = sum(v.get("volumen_usd") or 0 for v in vendedores)
    top = vendedores[:10]
    con_demanda = [v for v in vendedores if (v.get("compradores_unicos") or 0) > 20]

    return {
        "market": "x402",
        "as_of": d.get("actualizado"),
        "totals": d.get("agregados"),
        "hourly_buckets": len(tramos),
        "covering": {"from": tramos[0].get("desde"), "to": tramos[-1].get("desde")},
        "trend": tendencia,
        "concentration": {
            "top1_share_pct": round((top[0].get("volumen_usd") or 0) / total * 100, 1)
            if top and total else None,
            "top5_share_pct": round(sum(v.get("volumen_usd") or 0 for v in vendedores[:5])
                                    / total * 100, 1) if total else None,
            "sellers_over_100_usd": sum(1 for v in vendedores
                                        if (v.get("volumen_usd") or 0) > 100),
            "sellers_over_20_buyers": len(con_demanda),
        },
        "top_sellers": [
            {"seller": v.get("vendedor"), "volume_usd": v.get("volumen_usd"),
             "calls": v.get("llamadas"), "unique_buyers": v.get("compradores_unicos"),
             "ticket_usd": v.get("ticket_usd")}
            for v in top
        ],
        "tracked_sellers": len(vendedores),
        "note": "Amounts converted from USDC atomic units. Historical buckets are kept "
                "beyond x402scan's rolling window, which is why this series cannot be "
                "reconstructed retroactively elsewhere.",
    }


def dossier(max_chars: int = 24000) -> dict:
    """El paquete que se le entrega al analista de guardia para atender una pregunta.

    Lleva las cifras **y el análisis escrito por el agente nocturno**. Esa es la división
    del trabajo: el análisis se produce de noche, sin prisa y sin coste por llamada; aquí
    solo se atiende al comprador. Se recorta a `max_chars` para que una llamada no se
    dispare de precio por tokens: el margen de este producto no puede depender del tamaño
    que tenga el informe ese día.
    """
    d = load()
    tramos = d.get("tramos") or []
    vendedores = d.get("vendedores") or []
    analisis = (d.get("analisis") or "").strip()
    return {
        "as_of": d.get("actualizado"),
        "written_analysis": analisis[:max_chars] if analisis else None,
        "analysis_author": d.get("analisis_autor"),
        "totals": d.get("agregados"),
        # La serie entera no cabe ni hace falta: los extremos y una muestra regular
        # bastan para responder sobre tendencia sin inflar el prompt.
        "series_from": tramos[0].get("desde") if tramos else None,
        "series_to": tramos[-1].get("desde") if tramos else None,
        "series_sample": tramos[:: max(1, len(tramos) // 12)] if tramos else [],
        "hourly_buckets": len(tramos),
        "top_sellers": vendedores[:25],
        "tracked_sellers": len(vendedores),
    }


def freshness() -> dict:
    """Para el escaparate gratis: qué hay, de cuándo, sin entregar la serie."""
    try:
        d = load()
    except SeriesError as e:
        return {"available": False, "reason": e.detail}
    tramos = d.get("tramos") or []
    return {
        "available": bool(tramos),
        "as_of": d.get("actualizado"),
        "hourly_buckets": len(tramos),
        "tracked_sellers": len(d.get("vendedores") or []),
        "covering": {"from": tramos[0].get("desde"), "to": tramos[-1].get("desde")}
        if tramos else None,
    }
