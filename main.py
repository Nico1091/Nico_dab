"""json-repair-api — servicios de datos para agentes de IA, cobrados en USDC vía x402.

Endpoints de pago (categoría "data", la de mayor demanda del ecosistema x402):
  POST /repair    $0.001  repara JSON malformado (cómputo puro)
  POST /validate  $0.001  valida datos contra un JSON Schema y lista los errores
  POST /csv       $0.001  convierte una lista de registros JSON a CSV
  POST /markdown  $0.002  convierte HTML a Markdown limpio
  POST /extract   $0.005  extrae JSON estructurado de texto libre (trabajador: DeepSeek)
  POST /promote   $0.02   kit publicitario x402 para el servicio del cliente (DeepSeek)
  POST /ask       $0.004  respuesta LLM de propósito general en JSON (DeepSeek)
  POST /features  $0.003  features numéricas en vivo de un activo cripto (Binance)
  POST /resolve   $0.008  dato objetivo de liquidación de una apuesta de precio
                          (cierre diario UTC + fuente pública; oráculo de datos)
  POST /edge      $0.02   probabilidad+confianza para una pregunta de apuesta:
                          consejo de 3 analistas DeepSeek EN PARALELO + juez
                          (patrón consejo.js), con features de mercado en vivo
  POST /route     $0.01   x402-router: enruta al mejor servicio verificado del
                          catálogo, PAGA el downstream en USDC y devuelve la
                          respuesta + tx on-chain (dormido sin PAYER_PK; el
                          catálogo gratis vive en GET /catalog)

Gateway fiat (El Cambista): los mismos endpoints bajo /fiat/*, sin x402 — se
venden por suscripción en RapidAPI; cada llamada del marketplace trae el header
X-RapidAPI-Proxy-Secret, que se compara con RAPIDAPI_PROXY_SECRET (sin esa env
var el gateway queda dormido y responde 503).

Endpoints gratis: GET / y GET /ads — escaparate con autopromoción que DeepSeek
regenera cada PROMO_INTERVAL_HOURS horas (24 por defecto; el propio /promote
aplicado a este servicio) — y GET /health.

Config por variables de entorno (.env soportado):
  PAY_TO            — dirección pública de tu wallet en Base (0x...), obligatoria en producción
  NETWORK           — "base" (mainnet) o "base-sepolia" (pruebas)
  DEEPSEEK_API_KEY  — clave de https://platform.deepseek.com (para /extract)
  CDP_API_KEY_ID / CDP_API_KEY_SECRET — claves del portal CDP de Coinbase; con ellas
                      se usa el facilitador de Coinbase y el servicio aparece en el
                      Bazaar x402 (sin ellas, facilitador genérico: cobra pero no lista)
  RAPIDAPI_PROXY_SECRET — secreto del panel de proveedor de RapidAPI; activa el
                      gateway fiat /fiat/* (dormido si falta)
  PAYER_PK          — clave privada de la wallet que paga los downstream del
                      /route; activa el router (dormido si falta). Topes:
                      MAX_ROUTE_SPEND_ATOMIC (5000) y DAILY_SPEND_CAP_USD (0.25)
"""
import csv
import hmac
import io
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

load_dotenv()

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from json_repair import repair_json
from markdownify import markdownify
import jsonschema

from agentcash_discovery import build_openapi, example_for, input_body_fields, \
    input_schema_for, output_schema_for, resource_schema_for
from brain import get_brain
from council import run_council
from fiat_guard import GUARD as FIAT_GUARD
from market_data import MarketDataError, VALID_INTERVALS, features_for, resolve_threshold
from router_brain import choose as router_choose
from router_catalog import Catalog, MAX_ROUTE_SPEND_ATOMIC, TRUST_GUARD_URL
from router_payer import BUDGET, buy as router_buy
from x402_series import SeriesError, dossier as series_dossier, \
    freshness as series_freshness, market_report, market_series

PAY_TO = os.getenv("PAY_TO", "")
NETWORK = os.getenv("NETWORK", "base")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "200000"))
# /ask limita más la entrada para que el coste DeepSeek nunca coma el margen.
ASK_MAX_CHARS = int(os.getenv("ASK_MAX_CHARS", "16000"))
RAPIDAPI_PROXY_SECRET = os.getenv("RAPIDAPI_PROXY_SECRET", "")

PRICES = {
    "/repair": os.getenv("PRICE_REPAIR", "$0.001"),
    "/validate": os.getenv("PRICE_VALIDATE", "$0.001"),
    "/csv": os.getenv("PRICE_CSV", "$0.001"),
    "/markdown": os.getenv("PRICE_MARKDOWN", "$0.002"),
    "/extract": os.getenv("PRICE_EXTRACT", "$0.005"),
    "/promote": os.getenv("PRICE_PROMOTE", "$0.02"),
    "/ask": os.getenv("PRICE_ASK", "$0.004"),
    "/features": os.getenv("PRICE_FEATURES", "$0.003"),
    "/resolve": os.getenv("PRICE_RESOLVE", "$0.008"),
    "/edge": os.getenv("PRICE_EDGE", "$0.02"),
    # Serie histórica propia del mercado x402. Precio en la franja que este mercado SÍ paga
    # ($0,94 apify · $1,86 stabledomains · $2,14 dTelecom · $4,31 purch), no en el suelo de
    # $0,001 donde vive el procesamiento de texto: esto no es un cálculo replicable en una
    # tarde, es un histórico que no se puede reconstruir hacia atrás.
    "/x402series": os.getenv("PRICE_X402SERIES", "$1.00"),
    "/x402report": os.getenv("PRICE_X402REPORT", "$3.00"),
    # El unico de los tres que gasta modelo: DeepSeek atiende la pregunta con el
    # dossier que escribio el agente nocturno. Precio intermedio: hay coste real
    # detras (tokens), pero acotado por el recorte del dossier y max_tokens.
    "/x402ask": os.getenv("PRICE_X402ASK", "$2.00"),
}

DESCRIPTIONS = {
    "/repair": "Repair malformed JSON (truncated, single quotes, trailing "
               "commas, LLM output artifacts) into valid JSON.",
    "/validate": "Validate JSON data against a JSON Schema and return a "
                 "detailed list of violations.",
    "/csv": "Convert a list of JSON records into CSV with automatic "
            "header detection.",
    "/markdown": "Convert raw HTML into clean Markdown (scripts and "
                 "styles stripped).",
    "/extract": "Extract structured JSON matching a given schema from "
                "free-form text using an LLM worker.",
    "/promote": "Generate a truthful promotion kit for your x402 service "
                "(tagline, discovery-optimized description, tweet, README "
                "blurb) using an LLM copywriter.",
    "/ask": "Ask an LLM worker anything — question answering, rewriting, "
            "summarizing, classification, brainstorming — and get a JSON "
            "answer. No account or API key needed, pay per call.",
    "/features": "Live numeric market features for a crypto asset from "
                 "Binance candles: RSI, SMAs, momentum, volatility, drawdown, "
                 "range position, volume trend. Ready-made model inputs for "
                 "trading and betting agents.",
    "/resolve": "Settlement-grade resolution data for crypto price bets: did "
                "ASSET close above/below X on DATE? Returns YES/NO from the "
                "Binance UTC daily close with the public source URL. Facts, "
                "not opinion — built for prediction-market agents.",
    "/edge": "Betting edge for a crypto price question: a 3-analyst AI "
             "council (YES advocate, NO advocate, quant) debates live market "
             "features in parallel and a judge returns probability, "
             "confidence and key reasons. Signals, not certainties.",
    "/x402series": "Historical time series of the x402 market itself: hourly "
                   "transactions, volume, buyers and active sellers — or the "
                   "full record of one seller (volume, calls, unique buyers, "
                   "ticket, rank). Kept beyond x402scan's rolling window, so "
                   "it cannot be reconstructed retroactively anywhere else.",
    "/x402report": "Full x402 market report: totals, measured trend against "
                   "our own history (transactions, volume, buyers, sellers), "
                   "concentration (top-1 and top-5 share, how many sellers "
                   "actually bill over $100) and the top sellers with their "
                   "real ticket. The numbers you need before pricing an "
                   "x402 service or entering a niche.",
    "/x402ask": "Ask anything about the x402 market and get an answer grounded "
                "ONLY in our own research package: historical series, seller "
                "table and a written analysis produced offline by our night "
                "agent. Every reply carries the figures it used and a "
                "`grounded_in_dossier` flag — when the data does not cover your "
                "question, it says so instead of guessing.",
}


def _atomic(price: str) -> str:
    from decimal import Decimal
    return str(int(Decimal(price.lstrip("$")) * 1_000_000))


# ------------------------------------------------------ router integrado (/route)
# El "OpenRouter del Bazaar x402": /route recibe la necesidad del agente, elige
# el mejor servicio verificado del catálogo, PAGA la llamada downstream en USDC
# y devuelve la respuesta con su prueba on-chain. Peaje: cobra PRICE_ROUTE y
# gasta ≤ MAX_ROUTE_SPEND_ATOMIC. Sin PAYER_PK duerme: ni cobra ni se lista.
PAYER_PK = os.getenv("PAYER_PK", "")
PRICE_ROUTE = os.getenv("PRICE_ROUTE", "$0.01")
RESPONSE_MAX_CHARS = int(os.getenv("RESPONSE_MAX_CHARS", "20000"))
ROUTER_ENABLED = bool(PAYER_PK)
if ROUTER_ENABLED:
    PRICES["/route"] = PRICE_ROUTE
    DESCRIPTIONS["/route"] = (
        "One call that routes your request to the best verified x402 service, "
        "pays the downstream for you in USDC and returns its response plus "
        "the on-chain settlement proof. Body: {'query': natural language} or "
        "{'resource': url from GET /catalog}; optional 'payload', 'params', "
        "'dry_run'.")

app = FastAPI(
    title="agent-data-toolkit",
    version="2.0.0",
    description="Pay-per-call data utilities for AI agents (x402 / USDC on Base).",
)

# Middleware x402: responde HTTP 402 con instrucciones de pago; el agente paga en
# USDC y reintenta con el header X-PAYMENT. Sin PAY_TO corre gratis (solo dev).
import pago_v2

if PAY_TO and pago_v2.disponible():
    # Camino v2: el único que AgentCash acepta (v1 lo rechaza con
    # X402_VERSION_V1_NOT_SUPPORTED). Un solo middleware cubre todas las rutas.
    app.middleware("http")(
        pago_v2.construir_middleware(
            prices=PRICES, descriptions=DESCRIPTIONS, pay_to=PAY_TO,
            network=NETWORK, base_url=os.getenv(
                "PUBLIC_URL", "https://agent-data-toolkit.onrender.com"),
            input_body_fields=input_body_fields,
            output_schema_for=output_schema_for,
            example_for=example_for,
            resource_schema_for=resource_schema_for,
        )
    )
elif PAY_TO:
    from x402.fastapi.middleware import require_payment

    # Con claves CDP se usa el facilitador de Coinbase (lista el servicio en el
    # Bazaar x402); sin ellas, el facilitador genérico (cobra pero no lista).
    facilitator = None
    if os.getenv("CDP_API_KEY_ID") and os.getenv("CDP_API_KEY_SECRET"):
        from cdp.x402 import create_facilitator_config

        _cdp = create_facilitator_config(
            os.getenv("CDP_API_KEY_ID"), os.getenv("CDP_API_KEY_SECRET")
        )
        # cdp-sdk entrega create_headers síncrona, pero x402<2 la await-ea
        _sync_headers = _cdp["create_headers"]

        async def _create_headers():
            return _sync_headers()

        facilitator = {"url": _cdp["url"], "create_headers": _create_headers}

    for path, price in PRICES.items():
        app.middleware("http")(
            require_payment(path=path, price=price,
                            pay_to_address=PAY_TO, network=NETWORK,
                            description=DESCRIPTIONS[path],
                            # Sin esquema de salida el validador de AgentCash
                            # rechaza el recurso (SCHEMA_OUTPUT_MISSING): un
                            # agente no compra algo cuya respuesta no conoce.
                            input_schema=input_schema_for(path),
                            output_schema=output_schema_for(path),
                            facilitator_config=facilitator)
        )


def _check_size(*texts: str) -> None:
    if sum(len(t) for t in texts) > MAX_INPUT_CHARS:
        raise HTTPException(413, f"entrada supera {MAX_INPUT_CHARS} caracteres")


def _validate_against(data, schema: dict | None, result: dict) -> dict:
    if schema is not None:
        try:
            jsonschema.validate(data, schema)
            result["schema_valid"] = True
        except jsonschema.ValidationError as e:
            result["schema_valid"] = False
            result["schema_error"] = e.message
    return result


# --------------------------------------------------------------------------- repair
class RepairRequest(BaseModel):
    # El campo se llama schema_ porque "schema" choca con un atributo de
    # BaseModel; el alias acepta ambos nombres en el JSON de entrada.
    model_config = ConfigDict(populate_by_name=True)
    broken: str
    schema_: dict | None = Field(None, alias="schema")


def _repair_is_dubious(fixed) -> bool:
    """Detecta artefactos típicos del reparador mecánico (p.ej. '42,' como string)."""
    if isinstance(fixed, dict):
        return any(_repair_is_dubious(v) for v in fixed.values())
    if isinstance(fixed, list):
        return any(_repair_is_dubious(v) for v in fixed)
    return isinstance(fixed, str) and fixed.rstrip(",. ") != fixed


@app.post("/repair")
def repair(req: RepairRequest):
    _check_size(req.broken)
    fixed_str = repair_json(req.broken)
    try:
        fixed = json.loads(fixed_str)
        mechanical_ok = True
    except json.JSONDecodeError:
        fixed, mechanical_ok = None, False

    # Escalada agéntica: si lo mecánico falló o dejó artefactos, DeepSeek decide.
    brain = get_brain()
    if brain and req.broken.strip() and (not mechanical_ok or _repair_is_dubious(fixed)):
        try:
            fixed = brain.run("repair", req.broken, req.schema_)
            result = _validate_against(fixed, req.schema_,
                                       {"ok": True, "repaired": fixed})
            result["worker"] = "deepseek"
            return result
        except Exception:
            pass  # si el LLM falla, caemos al resultado mecánico

    if fixed is None:
        return {"ok": False, "error": "irreparable"}
    return _validate_against(fixed, req.schema_, {"ok": True, "repaired": fixed})


# ------------------------------------------------------------------------- validate
class ValidateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    data: dict | list
    schema_: dict = Field(alias="schema")


@app.post("/validate")
def validate(req: ValidateRequest):
    validator = jsonschema.Draft202012Validator(req.schema_)
    errors = [
        {"path": "/".join(str(p) for p in e.absolute_path), "message": e.message}
        for e in validator.iter_errors(req.data)
    ]
    return {"ok": True, "valid": not errors, "errors": errors}


# ------------------------------------------------------------------------------ csv
class CsvRequest(BaseModel):
    records: list[dict] = Field(min_length=1)
    delimiter: str = ","


@app.post("/csv")
def to_csv(req: CsvRequest):
    _check_size(json.dumps(req.records))
    fields: list[str] = []
    for r in req.records:
        for k in r:
            if k not in fields:
                fields.append(k)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, delimiter=req.delimiter,
                            extrasaction="ignore")
    writer.writeheader()
    writer.writerows(req.records)
    return {"ok": True, "csv": buf.getvalue(), "rows": len(req.records)}


# ------------------------------------------------------------------------- markdown
class MarkdownRequest(BaseModel):
    html: str


@app.post("/markdown")
def to_markdown(req: MarkdownRequest):
    _check_size(req.html)
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(req.html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    md = markdownify(str(soup), heading_style="ATX")
    return {"ok": True, "markdown": md.strip()}


# -------------------------------------------------------------------------- extract
class ExtractRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    text: str
    schema_: dict = Field(alias="schema")
    instructions: str | None = None


@app.post("/extract")
def extract(req: ExtractRequest):
    brain = get_brain()
    if brain is None:
        raise HTTPException(503, "DEEPSEEK_API_KEY no configurada")
    _check_size(req.text)
    try:
        data = brain.run("extract", req.text, req.schema_, req.instructions)
    except ValueError as e:
        raise HTTPException(502, str(e))
    except Exception:
        raise HTTPException(502, "el proveedor del modelo no respondió")
    return _validate_against(data, req.schema_, {"ok": True, "data": data})


# -------------------------------------------------------------------------- promote
class PromoteRequest(BaseModel):
    name: str
    url: str
    what_it_does: str
    pricing: str | None = None
    audience: str | None = None
    language: str = "en"


@app.post("/promote")
def promote(req: PromoteRequest):
    brain = get_brain()
    if brain is None:
        raise HTTPException(503, "DEEPSEEK_API_KEY no configurada")
    _check_size(req.what_it_does)
    try:
        kit = brain.run("promote", json.dumps(req.model_dump(exclude_none=True)),
                        instructions=f"Write the kit in this language: {req.language}")
    except ValueError as e:
        raise HTTPException(502, str(e))
    except Exception:
        raise HTTPException(502, "el proveedor del modelo no respondió")
    return {"ok": True, "kit": kit}


# ------------------------------------------------------------------------------ ask
class AskRequest(BaseModel):
    prompt: str
    instructions: str | None = None


@app.post("/ask")
def ask(req: AskRequest):
    brain = get_brain()
    if brain is None:
        raise HTTPException(503, "DEEPSEEK_API_KEY no configurada")
    if len(req.prompt) + len(req.instructions or "") > ASK_MAX_CHARS:
        raise HTTPException(413, f"entrada supera {ASK_MAX_CHARS} caracteres")
    try:
        out = brain.run("ask", req.prompt, instructions=req.instructions,
                        max_tokens=1200)
    except ValueError as e:
        raise HTTPException(502, str(e))
    except Exception:
        raise HTTPException(502, "el proveedor del modelo no respondió")
    return {"ok": True, "answer": out.get("answer", out), "worker": DEEPSEEK_MODEL}


# ----------------------------------------- betting intel (/features /resolve /edge)
# Vende a los agentes que YA apuestan (Polymarket/sportsbooks vía x402) lo que
# necesitan: features vivas, dato objetivo de liquidación y una probabilidad
# razonada. Señales honestas, nunca promesas de ganancia. Los errores de
# entrada/datos responden 4xx/503 y por tanto NO cobran (x402 liquida solo 2xx).
BET_DISCLAIMER = ("Probabilistic signal for research; not financial advice and "
                  "never a guarantee — any bet can lose.")
EDGE_MAX_CHARS = int(os.getenv("EDGE_MAX_CHARS", "2000"))
DEFAULT_HORIZON_HOURS = 72.0


class FeaturesRequest(BaseModel):
    symbol: str
    interval: str = "1h"


@app.post("/features")
def market_features(req: FeaturesRequest):
    try:
        data = features_for(req.symbol, req.interval)
    except MarketDataError as e:
        raise HTTPException(e.status, str(e))
    return {"ok": True, **data}


class ResolveRequest(BaseModel):
    question: str


def _parse_market_question(question: str) -> dict:
    brain = get_brain()
    if brain is None:
        raise HTTPException(503, "DEEPSEEK_API_KEY no configurada")
    if not question.strip():
        raise HTTPException(422, "question is empty")
    if len(question) > EDGE_MAX_CHARS:
        raise HTTPException(413, f"question exceeds {EDGE_MAX_CHARS} characters")
    try:
        parsed = brain.run("market_parse", question, max_tokens=300)
    except Exception:
        raise HTTPException(502, "el proveedor del modelo no respondió")
    if not parsed.get("supported") or not parsed.get("symbol"):
        raise HTTPException(422, "unsupported question — only single-crypto-asset "
                            f"price questions for now ({parsed.get('reason', '')})")
    return parsed


@app.post("/resolve")
def resolve_bet(req: ResolveRequest):
    parsed = _parse_market_question(req.question)
    if parsed.get("comparator") is None or parsed.get("threshold") is None \
            or not parsed.get("date"):
        raise HTTPException(422, "resolution needs an asset, a comparator "
                            "(above/below), a threshold and a past UTC date, "
                            "e.g. 'Did BTC close above $100,000 on 2026-07-01?'")
    try:
        result = resolve_threshold(parsed["symbol"], parsed["comparator"],
                                   float(parsed["threshold"]), parsed["date"])
    except MarketDataError as e:
        raise HTTPException(e.status, str(e))
    return {"ok": True, "question": req.question, **result}


class EdgeRequest(BaseModel):
    question: str
    horizon_hours: float | None = None  # override opcional del horizonte


@app.post("/edge")
def betting_edge(req: EdgeRequest):
    t0 = time.perf_counter()
    parsed = _parse_market_question(req.question)
    symbol = parsed["symbol"]

    # Multiparámetro en paralelo: la vista micro (1h) y la macro (1d) a la vez.
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            f1h, f1d = pool.map(lambda iv: features_for(symbol, iv), ("1h", "1d"))
    except MarketDataError as e:
        raise HTTPException(e.status, str(e))

    horizon = req.horizon_hours or parsed.get("horizon_hours") or DEFAULT_HORIZON_HOURS
    features = {"interval_1h": f1h, "interval_1d": f1d}
    try:
        council = run_council(get_brain(), req.question, horizon, features)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    except Exception:
        raise HTTPException(502, "el proveedor del modelo no respondió")

    return {
        "ok": True,
        "question": req.question,
        "symbol": f1h["symbol"],
        "horizon_hours": horizon,
        **council,
        "features": features,
        "worker": "deepseek-council-3+judge",
        "latency_ms": round((time.perf_counter() - t0) * 1000),
        "disclaimer": BET_DISCLAIMER,
    }


# ------------------------------------------- serie histórica del mercado x402
# El único dato del catálogo que NO se puede replicar hacia atrás: x402scan publica una
# ventana móvil de ~1 mes y lo anterior desaparece. Nosotros lo conservamos desde el
# 26-jul-2026. No gasta LLM: es lectura de disco, así que el margen es del 100 % y no
# depende del coste por token de ningún proveedor.

class SeriesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seller: str | None = Field(
        None, description="Domain of one seller, e.g. 'blockrun.ai'. Omit for the "
                          "whole-market hourly series.")
    limit: int = Field(0, ge=0, le=5000,
                       description="Keep only the last N hourly buckets. 0 = all.")


@app.post("/x402series")
def x402_market_series(req: SeriesRequest):
    try:
        return {"ok": True, **market_series(req.seller, req.limit)}
    except SeriesError as e:
        raise HTTPException(e.status, e.detail)


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


@app.post("/x402report")
def x402_market_report(req: ReportRequest | None = None):
    try:
        return {"ok": True, **market_report()}
    except SeriesError as e:
        raise HTTPException(e.status, e.detail)


class X402AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(..., min_length=3, max_length=2000,
                          description="What you want to know about the x402 market.")


@app.post("/x402ask")
def x402_market_ask(req: X402AskRequest):
    """Pregunta libre sobre el mercado x402, respondida SOLO con el dossier.

    Aquí sí interviene el modelo, pero como vendedor del contenido, no como autor: el
    análisis lo escribió el agente nocturno y este endpoint únicamente lo atiende. Por eso
    devuelve `grounded`: si la respuesta no estaba en el dossier, el comprador se entera.
    """
    brain = get_brain()
    if brain is None:
        raise HTTPException(503, "DEEPSEEK_API_KEY no configurada")
    try:
        paquete = series_dossier()
    except SeriesError as e:
        raise HTTPException(e.status, e.detail)
    contenido = json.dumps({"dossier": paquete, "question": req.question},
                           ensure_ascii=False)
    try:
        out = brain.run("x402_analyst", contenido, max_tokens=900)
    except Exception:
        raise HTTPException(502, "el proveedor del modelo no respondió")
    return {
        "ok": True,
        "question": req.question,
        "answer": out.get("answer"),
        "figures": out.get("figures", []),
        # Honestidad explícita: si el dossier no daba para responder, se dice.
        "grounded_in_dossier": bool(out.get("grounded")),
        "as_of": paquete.get("as_of"),
        "analysis_author": paquete.get("analysis_author"),
        "worker": os.getenv("DEEPSEEK_MODEL", "deepseek"),
    }


@app.get("/x402series/freshness")
def x402_series_freshness():
    """Gratis a propósito: qué hay y de cuándo, sin entregar la serie. Es el gancho —
    cobrar por decir 'tengo datos' ahuyenta al comprador antes de que pruebe nada."""
    return series_freshness()


# ------------------------------------------------------- gateway fiat (El Cambista)
# Los mismos servicios vendidos por suscripción a humanos en RapidAPI: el
# marketplace pone la tarjeta y las cuotas; aquí solo se aceptan llamadas que
# traigan el secreto que RapidAPI añade a cada request que proxea. Sin
# RAPIDAPI_PROXY_SECRET el gateway queda dormido (503) y no expone nada.
def _fiat_auth(request: Request) -> None:
    if not RAPIDAPI_PROXY_SECRET:
        raise HTTPException(503, "fiat gateway dormido: falta RAPIDAPI_PROXY_SECRET")
    got = request.headers.get("X-RapidAPI-Proxy-Secret", "")
    if not hmac.compare_digest(got, RAPIDAPI_PROXY_SECRET):
        raise HTTPException(401, "la llamada no viene del gateway de RapidAPI")


# El secreto es la única barrera de /fiat/*, así que detrás va el cortafuegos:
# tope diario de coste del modelo + límite por minuto (ver fiat_guard.py).
def _fiat_guard(request: Request) -> None:
    ok, motivo = FIAT_GUARD.check(request.url.path)
    if not ok:
        raise HTTPException(429, motivo)


# Catálogo fiat = el motor de datos. Los endpoints de cripto (/features,
# /resolve, /edge) y /promote se quedan fuera a propósito: son lentos, caros en
# DeepSeek y de nicho estrecho para un marketplace generalista. Siguen
# vendiéndose por x402 sin cambio alguno.
fiat = APIRouter(prefix="/fiat",
                 dependencies=[Depends(_fiat_auth), Depends(_fiat_guard)])
for _path, _fn in [("/repair", repair), ("/validate", validate), ("/csv", to_csv),
                   ("/markdown", to_markdown), ("/extract", extract),
                   ("/ask", ask)]:
    fiat.post(_path)(_fn)
app.include_router(fiat)


# ----------------------------------------------------------------- escaparate (gratis)
# Autopromoción agéntica: DeepSeek regenera el kit publicitario del propio
# servicio como máximo una vez cada PROMO_INTERVAL_HOURS (dogfooding de /promote,
# ~1 llamada corta al día — coste despreciable). Se sirve en GET / y GET /ads.
PROMO_INTERVAL_HOURS = float(os.getenv("PROMO_INTERVAL_HOURS", "24"))
_PROMO_RETRY_SECONDS = 3600  # tras un intento fallido, esperar antes de reintentar

BASE_URL = os.getenv("PUBLIC_URL", "https://agent-data-toolkit.onrender.com")

_SELF_SERVICE = {
    "name": "agent-data-toolkit",
    "url": BASE_URL,
    "what_it_does": "; ".join(f"POST {p} ({PRICES[p]}): {DESCRIPTIONS[p]}"
                              for p in PRICES),
    "pricing": "pay-per-call in USDC on Base via the x402 protocol, from $0.001",
    "audience": "AI agents with x402 wallets and the developers who run them",
    "related_services": [
        {"name": "x402-trust-guard",
         "url": "https://nico222222222-x402-trust-guard.hf.space"}
    ],
}

_FALLBACK_KIT = {
    "tagline": "Clean data for AI agents, one cent at a time.",
    "bazaar_description": "Pay-per-call data utilities for AI agents: repair "
        "JSON, validate schemas, JSON-to-CSV, HTML-to-Markdown, LLM extraction "
        "and promo-kit generation. USDC on Base via x402, from $0.001.",
    "tweet": "agent-data-toolkit: six pay-per-call data endpoints for AI agents "
        "(JSON repair, schema validation, CSV, Markdown, extraction, promo kits). "
        f"USDC on Base, from $0.001. {BASE_URL} #x402",
    "readme_blurb": "Pay-per-call data utilities for AI agents, billed in USDC "
        "on Base via x402. Six endpoints from $0.001: JSON repair, schema "
        "validation, CSV, Markdown, LLM extraction and promo-kit generation.",
    "one_liner": "An x402 API that fixes, validates, converts and extracts "
        "data for about a tenth of a cent per call.",
}

_promo = {"kit": None, "generated_at": None, "worker": "static"}
_promo_lock = threading.Lock()
_promo_last_attempt = 0.0


def _refresh_promo() -> None:
    """Regenera la autopromoción con DeepSeek."""
    global _promo_last_attempt
    _promo_last_attempt = time.time()
    brain = get_brain()
    if brain is None:
        return
    try:
        kit = brain.run("promote", json.dumps(_SELF_SERVICE))
        if all(k in kit for k in _FALLBACK_KIT):
            _promo.update(kit=kit, generated_at=time.time(), worker="deepseek")
    except Exception:
        pass  # el escaparate nunca debe caerse por un fallo del LLM


def _promo_kit() -> dict:
    """Kit vigente; si caducó, dispara un refresco en segundo plano."""
    now = time.time()
    stale = (_promo["generated_at"] is None
             or now - _promo["generated_at"] > PROMO_INTERVAL_HOURS * 3600)
    if (stale and now - _promo_last_attempt > _PROMO_RETRY_SECONDS
            and _promo_lock.acquire(blocking=False)):
        def _job():
            try:
                _refresh_promo()
            finally:
                _promo_lock.release()
        threading.Thread(target=_job, daemon=True).start()
    return _promo["kit"] or _FALLBACK_KIT


@app.get("/")
def home():
    kit = _promo_kit()
    return {
        "service": "agent-data-toolkit",
        "tagline": kit.get("tagline"),
        "endpoints": {p: {"price": PRICES[p], "description": DESCRIPTIONS[p]}
                      for p in PRICES},
        "free": {"GET /": "this page", "GET /ads": "full promo kit",
                 "GET /catalog": "what POST /route can route to",
                 "GET /health": "service status", "GET /docs": "Swagger UI"},
        "payment": {"protocol": "x402", "currency": "USDC", "network": NETWORK},
        "fiat_gateway": "same endpoints under /fiat/* via RapidAPI subscription "
                        "(credit card, no crypto wallet needed)",
        "more_services": _SELF_SERVICE["related_services"],
        "note": "The promo copy on this page is regenerated periodically by "
                "POST /promote — the product advertising itself.",
    }


@app.get("/ads")
def ads():
    kit = _promo_kit()
    return {
        "ok": True,
        "kit": kit,
        "worker": _promo["worker"],
        "generated_at": _promo["generated_at"],
        "refresh_interval_hours": PROMO_INTERVAL_HOURS,
        "made_with": "POST /promote — buy this same copywriting for your own "
                     f"service ({PRICES['/promote']})",
    }


# ---------------------------------------------------- router x402 (peaje /route)
_ROUTE_SEED = [
    {"resource": BASE_URL + p, "method": "POST",
     "price_atomic": int(_atomic(PRICES[p])), "description": DESCRIPTIONS[p]}
    for p in PRICES if p != "/route"
]
ROUTE_CATALOG = Catalog(seed=_ROUTE_SEED)


class RouteRequest(BaseModel):
    query: str | None = None       # necesidad en lenguaje natural, o...
    resource: str | None = None    # ...URL exacta del catálogo
    payload: dict | list | None = None   # body JSON para el downstream (POST)
    params: dict | None = None     # query params para el downstream
    method: str | None = None      # override GET/POST (default: el del catálogo)
    dry_run: bool = False          # true = solo decide, no compra


@app.post("/route")
async def route(req: RouteRequest):
    if not ROUTER_ENABLED:
        raise HTTPException(503, "router dormido: falta PAYER_PK (wallet pagadora)")
    await ROUTE_CATALOG.refresh()
    if not req.query and not req.resource:
        raise HTTPException(400, "send 'query' (natural language) or "
                                 "'resource' (a URL from GET /catalog)")

    if req.resource:
        entry = ROUTE_CATALOG.get(req.resource)
        if entry is None:
            raise HTTPException(400, "resource not in the routable catalog "
                                     "(see GET /catalog)")
        chooser = {"resource": req.resource, "reason": "resource given by caller",
                   "worker": "direct"}
    else:
        candidates = ROUTE_CATALOG.routable()
        if not candidates:
            raise HTTPException(503, "catalog is empty right now, retry shortly")
        chooser = router_choose(req.query, candidates)
        entry = ROUTE_CATALOG.get(chooser["resource"])

    price = entry["price_atomic"]
    if price > MAX_ROUTE_SPEND_ATOMIC:
        raise HTTPException(400, f"target costs {price} atomic USDC, above the "
                                 f"router per-call cap {MAX_ROUTE_SPEND_ATOMIC}")
    method = (req.method or entry["method"]).upper()
    if method not in ("GET", "POST"):
        raise HTTPException(400, "method must be GET or POST")

    if req.dry_run:
        return {"ok": True, "dry_run": True,
                "would_route_to": entry["resource"], "method": method,
                "downstream_price_usd": price / 1_000_000, "chooser": chooser}

    if not BUDGET.can_spend(price):
        raise HTTPException(503, "router daily downstream budget exhausted; "
                                 "try again tomorrow")

    result = await router_buy(entry["resource"], PAYER_PK, price, method,
                              req.payload, req.params)
    if not result.get("performed"):
        raise HTTPException(502, result.get("reason", "payer refused the call"))
    if result.get("ok") or result.get("tx_hash"):
        BUDGET.add(price)  # x402 solo liquida con 2xx; sin 2xx/tx no hubo gasto

    body, parsed = result.get("body"), None
    if body:
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            parsed = body[:RESPONSE_MAX_CHARS]

    return {
        "ok": result["ok"],
        "routed_to": entry["resource"],
        "chooser": chooser,
        "downstream_status": result["status"],
        "downstream_price_usd": result["spent_usd"],
        "tx_hash": result["tx_hash"],
        "explorer": (f"https://basescan.org/tx/{result['tx_hash']}"
                     if result["tx_hash"] else None),
        "latency_ms": result["latency_ms"],
        "response": parsed,
        "error": result["error"],
    }


@app.get("/catalog")
async def catalog_view():
    await ROUTE_CATALOG.refresh()

    def fmt(e: dict) -> dict:
        return {"resource": e["resource"], "method": e["method"],
                "price_usd": e["price_atomic"] / 1_000_000,
                "description": e["description"], "source": e["source"]}

    return {
        "ok": True,
        "router_enabled": ROUTER_ENABLED,
        "per_call_cap_usd": MAX_ROUTE_SPEND_ATOMIC / 1_000_000,
        "routable": [fmt(e) for e in sorted(ROUTE_CATALOG.routable(),
                                            key=lambda e: e["price_atomic"])],
        "not_routable_price_above_cap": [fmt(e) for e in ROUTE_CATALOG.skipped()],
        "sources": {"seed": "operated by us", "trust-guard":
                    f"real-purchase verified by {TRUST_GUARD_URL}/verified"},
    }


# ------------------------------------------------------- discovery (/.well-known/x402)
# Manifiesto de discovery x402 (convención well-known, RFC 8615): los crawlers
# lo piden para indexar TODOS los recursos de pago de una vez, sin sondear
# endpoint por endpoint. Mismo formato "accepts" que emiten nuestros 402.
_USDC_BY_NETWORK = {
    "base": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "base-sepolia": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
}


@app.get("/.well-known/x402")
def wellknown_x402():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    resources = []
    if PAY_TO:
        for path in PRICES:
            resources.append({
                "resource": BASE_URL + path,
                "type": "http",
                "x402Version": 1,
                "accepts": [{
                    "scheme": "exact",
                    "network": NETWORK,
                    "maxAmountRequired": _atomic(PRICES[path]),
                    "resource": BASE_URL + path,
                    "description": DESCRIPTIONS[path],
                    "mimeType": "application/json",
                    "payTo": PAY_TO,
                    "maxTimeoutSeconds": 60,
                    "asset": _USDC_BY_NETWORK.get(NETWORK, ""),
                    "extra": {"name": "USD Coin", "version": "2"},
                    # Misma forma que emite el middleware en el 402 real: el
                    # crawler debe ver aquí lo mismo que verá al sondear.
                    "outputSchema": {
                        "input": {"type": "http", "method": "POST",
                                  "bodyType": "json",
                                  "bodyFields": input_body_fields(path)},
                        "output": output_schema_for(path),
                    },
                }],
                "lastUpdated": now,
                "metadata": {"input": {"type": "http", "method": "POST",
                                       "discoverable": True}},
            })
    return {
        "x402Version": 1,
        "serviceName": "agent-data-toolkit",
        "resources": resources,
    }


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Los descubridores de agentes avisan si el origen no tiene favicon: es
    parte de cómo presentan el servicio en su catálogo."""
    from fastapi.responses import FileResponse
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "favicon.png")
    if not os.path.exists(ruta):
        raise HTTPException(404, "sin favicon")
    return FileResponse(ruta, media_type="image/png")


# --------------------------------------------------- discovery (/openapi.json)
# Segundo canal de descubrimiento, para AgentCash: la capa de pago x402 que
# corre DENTRO de Claude Code, Cursor, Windsurf, Codex y Claude Desktop. Su
# formato canónico es el propio /openapi.json, pero exige campos que FastAPI no
# pone solo (info.x-guidance, x-payment-info y el 402 declarado por operación).
# El well-known de arriba sirve a los crawlers del Bazaar; esto sirve al agente
# que ya está sentado en el editor de un desarrollador.
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "nr039020@gmail.com")

app.openapi = lambda: build_openapi(
    app,
    prices=PRICES,
    descriptions=DESCRIPTIONS,
    pay_to=PAY_TO,
    network=NETWORK,
    base_url=BASE_URL,
    contact_email=CONTACT_EMAIL,
)


# ------------------------------------------------------------------------ self-ping
# Render free duerme tras ~15 min sin tráfico ENTRANTE. Este hilo pide /health
# por la URL pública cada SELF_PING_MINUTES para que siempre haya tráfico
# (las 750 h/mes del free tier alcanzan para estar 24/7 despierto). Solo se
# activa en Render (env RENDER) o forzando SELF_PING=1; en dev local no corre.
SELF_PING_MINUTES = float(os.getenv("SELF_PING_MINUTES", "14"))

if os.getenv("RENDER") or os.getenv("SELF_PING") == "1":
    def _self_ping_loop():
        import urllib.request
        while True:
            time.sleep(SELF_PING_MINUTES * 60)
            try:
                urllib.request.urlopen(BASE_URL + "/health", timeout=90)
            except Exception:
                pass  # el siguiente intento llega en unos minutos

    threading.Thread(target=_self_ping_loop, daemon=True).start()


# --------------------------------------------------------------------------- health
@app.get("/health")
def health():
    return {
        "status": "ok",
        "paid": bool(PAY_TO),
        "network": NETWORK,
        "worker": DEEPSEEK_MODEL if DEEPSEEK_API_KEY else None,
        "fiat_gateway": bool(RAPIDAPI_PROXY_SECRET),
        "fiat_guard": FIAT_GUARD.snapshot(),
        "router": {"enabled": ROUTER_ENABLED,
                   "per_call_cap_atomic": MAX_ROUTE_SPEND_ATOMIC,
                   "daily_spent_usd": BUDGET.spent_usd,
                   "catalog_entries": len(ROUTE_CATALOG.entries)},
        "prices": PRICES,
    }
