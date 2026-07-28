"""TRM oficial de Colombia — el primer adaptador de la fábrica.

Por qué este y no otro
----------------------
Todo el catálogo x402 es anglosajón. **Nadie sirve datos de Colombia**, y un
agente que haga comercio, contabilidad o cumplimiento aquí no tiene forma de
convertir USDC a pesos con un valor verificable y con fecha. La TRM es el dato
público colombiano de más volumen posible: la usa cualquiera que facture,
importe, pague nómina o liquide impuestos.

Y sobre todo cumple la regla del patrón que sí vende: **el nombre es algo que
alguien ya teclea**. Nadie busca "agent-data-toolkit"; sí buscan "TRM".

Fuentes, en orden de preferencia
--------------------------------
1. **datos.gov.co** (Socrata), alimentado por la Superintendencia Financiera:
   - `ceyp-9c7c` serie completa desde 1991 (histórico)
   - `32sa-8pi3` vigente con unidad
2. **Scraping del certificado de la Superintendencia Financiera** como respaldo,
   por si el portal de datos abiertos cae.

Nunca se inventa un valor. Si ninguna fuente responde, se dice que no hay dato:
un agente que liquida dinero con una cifra estimada es peor que uno que espera.

La TRM cambia una vez al día, así que se cachea en memoria: sirve al agente al
instante y no castiga la fuente pública con una petición por llamada.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

SOCRATA_HISTORICO = "https://www.datos.gov.co/resource/ceyp-9c7c.json"
SOCRATA_VIGENTE = "https://www.datos.gov.co/resource/32sa-8pi3.json"
CERTIFICADO_SUPERFIN = "https://www.superfinanciera.gov.co/CertificadoTRM/"

FUENTE_OFICIAL = ("Superintendencia Financiera de Colombia, publicada en "
                  "datos.gov.co (Tasa de Cambio Representativa del Mercado)")

TIEMPO_CACHE_SEG = 1800  # media hora: la TRM cambia una vez al día
TIEMPO_ESPERA = 25

_cache: dict[str, tuple[float, dict]] = {}
_candado = threading.Lock()


class TRMError(Exception):
    """No se pudo obtener un valor real. Nunca se sustituye por una estimación."""


def _pedir(url: str) -> list | dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": "agent-data-toolkit/1.0 (+https://agent-data-toolkit.onrender.com)",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=TIEMPO_ESPERA) as r:
        return json.loads(r.read())


def _normaliza_fecha(valor: str | None) -> str | None:
    """Acepta 'YYYY-MM-DD' o ISO completa. Devuelve 'YYYY-MM-DD' o None."""
    if not valor:
        return None
    texto = str(valor).strip()[:10]
    try:
        datetime.strptime(texto, "%Y-%m-%d")
    except ValueError:
        raise TRMError(f"fecha no valida: {valor!r} (se espera YYYY-MM-DD)")
    return texto


def _desde_socrata_fecha(fecha: str) -> dict | None:
    """La TRM de una fecha es la que estaba vigente ese día (vigenciadesde <= fecha)."""
    consulta = urllib.parse.urlencode({
        "$where": f"vigenciadesde <= '{fecha}T00:00:00.000'",
        "$order": "vigenciadesde DESC",
        "$limit": 1,
    })
    filas = _pedir(f"{SOCRATA_HISTORICO}?{consulta}")
    if not filas:
        return None
    f = filas[0]
    return {
        "valor_cop": float(f["valor"]),
        "vigencia_desde": f.get("vigenciadesde", "")[:10],
        "vigencia_hasta": (f.get("vigenciahasta") or f.get("vigenciadesde", ""))[:10],
    }


def _desde_socrata_vigente() -> dict | None:
    filas = _pedir(f"{SOCRATA_VIGENTE}?$order=vigenciadesde%20DESC&$limit=1")
    if not filas:
        return None
    f = filas[0]
    return {
        "valor_cop": float(f["valor"]),
        "vigencia_desde": f.get("vigenciadesde", "")[:10],
        "vigencia_hasta": (f.get("vigenciahasta") or f.get("vigenciadesde", ""))[:10],
    }


def _desde_superfinanciera() -> dict | None:
    """Respaldo por scraping del certificado oficial.

    Solo se usa si datos abiertos falla. Se busca el número por FORMA (patrón de
    miles/decimales) y no por una ruta fija del HTML, para que un rediseño de la
    página degrade a 'sin dato' en vez de devolver una cifra equivocada — que en
    un dato con el que se liquida dinero es mucho peor que no responder.
    """
    req = urllib.request.Request(CERTIFICADO_SUPERFIN, headers={
        "User-Agent": "Mozilla/5.0 (compatible; agent-data-toolkit/1.0)"})
    with urllib.request.urlopen(req, timeout=TIEMPO_ESPERA) as r:
        html = r.read().decode("utf-8", errors="replace")

    # Un valor de TRM plausible: miles con coma o punto y 2 decimales.
    # Se exige el rango historico razonable para no capturar cualquier numero.
    candidatos = re.findall(r"\b([1-9]\d{0,2}[.,]?\d{3}[.,]\d{2})\b", html)
    for bruto in candidatos:
        limpio = bruto.replace(".", "").replace(",", ".")
        # si quedaron dos separadores, el ultimo es el decimal
        try:
            valor = float(limpio) if limpio.count(".") <= 1 else float(
                limpio.replace(".", "", limpio.count(".") - 1))
        except ValueError:
            continue
        if 500 <= valor <= 20000:  # rango historico de la TRM
            return {
                "valor_cop": valor,
                "vigencia_desde": date.today().isoformat(),
                "vigencia_hasta": date.today().isoformat(),
                "respaldo": True,
            }
    return None


def trm(fecha: str | None = None) -> dict:
    """TRM oficial de una fecha (o la vigente hoy).

    Devuelve siempre el valor, su vigencia, la fuente y el momento de consulta:
    un agente que liquida dinero necesita poder demostrar de dónde salió la cifra.
    """
    fecha = _normaliza_fecha(fecha)
    clave = fecha or "hoy"

    with _candado:
        guardado = _cache.get(clave)
        if guardado and time.time() - guardado[0] < TIEMPO_CACHE_SEG:
            return {**guardado[1], "cacheado": True}

    dato = fallo = None
    intentos = ([lambda: _desde_socrata_fecha(fecha)] if fecha
                else [_desde_socrata_vigente, _desde_superfinanciera])
    for intento in intentos:
        try:
            dato = intento()
            if dato:
                break
        except Exception as e:  # se prueba la siguiente fuente
            fallo = e

    if not dato:
        raise TRMError(
            f"no hay TRM disponible para {clave}"
            + (f" ({type(fallo).__name__})" if fallo else "")
        )

    resultado = {
        "trm_cop_por_usd": round(dato["valor_cop"], 2),
        "fecha": fecha or dato["vigencia_desde"],
        "vigencia_desde": dato["vigencia_desde"],
        "vigencia_hasta": dato["vigencia_hasta"],
        "moneda_base": "USD",
        "moneda_destino": "COP",
        "fuente": FUENTE_OFICIAL,
        "fuente_respaldo_usada": bool(dato.get("respaldo")),
        "consultado_en": datetime.now(timezone.utc).isoformat(),
        "cacheado": False,
    }
    with _candado:
        _cache[clave] = (time.time(), resultado)
    return resultado


def convertir(monto: float, desde: str = "USD", fecha: str | None = None) -> dict:
    """Convierte USD/USDC <-> COP a la TRM oficial, con el dato que lo respalda.

    USDC se trata como USD a la par: es lo que hace cualquier contabilidad, y se
    dice explícitamente en la respuesta para que nadie lo asuma en silencio.
    """
    unidad = (desde or "USD").strip().upper()
    if unidad not in ("USD", "USDC", "COP"):
        raise TRMError(f"moneda no soportada: {desde!r} (USD, USDC o COP)")
    try:
        monto = float(monto)
    except (TypeError, ValueError):
        raise TRMError(f"monto no numerico: {monto!r}")

    base = trm(fecha)
    tasa = base["trm_cop_por_usd"]

    if unidad == "COP":
        convertido, destino = round(monto / tasa, 6), "USD"
    else:
        convertido, destino = round(monto * tasa, 2), "COP"

    return {
        "monto": monto,
        "moneda_origen": unidad,
        "monto_convertido": convertido,
        "moneda_destino": destino,
        "trm_aplicada": tasa,
        "usdc_tratado_como_usd_a_la_par": unidad == "USDC",
        "fecha": base["fecha"],
        "vigencia_desde": base["vigencia_desde"],
        "fuente": base["fuente"],
        "consultado_en": base["consultado_en"],
    }


def serie(desde: str, hasta: str | None = None, limite: int = 400) -> dict:
    """Serie histórica de la TRM entre dos fechas, con su variación medida.

    El histórico va hasta 1991 en la fuente oficial. El límite existe para que
    una petición no se lleve treinta años por descuido.
    """
    desde = _normaliza_fecha(desde)
    if not desde:
        raise TRMError("falta la fecha inicial ('desde', YYYY-MM-DD)")
    hasta = _normaliza_fecha(hasta) or date.today().isoformat()
    if hasta < desde:
        raise TRMError("la fecha final es anterior a la inicial")
    limite = max(1, min(int(limite or 400), 2000))

    consulta = urllib.parse.urlencode({
        "$where": (f"vigenciadesde >= '{desde}T00:00:00.000' "
                   f"AND vigenciadesde <= '{hasta}T00:00:00.000'"),
        "$order": "vigenciadesde ASC",
        "$limit": limite,
    })
    filas = _pedir(f"{SOCRATA_HISTORICO}?{consulta}")
    puntos = [{"fecha": f.get("vigenciadesde", "")[:10],
               "trm_cop_por_usd": round(float(f["valor"]), 2)}
              for f in filas if f.get("valor")]
    if not puntos:
        raise TRMError(f"sin datos entre {desde} y {hasta}")

    primero, ultimo = puntos[0], puntos[-1]
    variacion = ultimo["trm_cop_por_usd"] - primero["trm_cop_por_usd"]
    valores = [p["trm_cop_por_usd"] for p in puntos]

    return {
        "desde": desde,
        "hasta": hasta,
        "puntos": puntos,
        "total_puntos": len(puntos),
        "truncado": len(filas) >= limite,
        "resumen": {
            "primera": primero,
            "ultima": ultimo,
            "variacion_cop": round(variacion, 2),
            "variacion_pct": round(variacion / primero["trm_cop_por_usd"] * 100, 2),
            "minima": min(valores),
            "maxima": max(valores),
            "promedio": round(sum(valores) / len(valores), 2),
        },
        "fuente": FUENTE_OFICIAL,
        "consultado_en": datetime.now(timezone.utc).isoformat(),
    }
