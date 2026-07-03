"""json-repair-api — servicios de datos para agentes de IA, cobrados en USDC vía x402.

Endpoints de pago (categoría "data", la de mayor demanda del ecosistema x402):
  POST /repair    $0.001  repara JSON malformado (cómputo puro)
  POST /validate  $0.001  valida datos contra un JSON Schema y lista los errores
  POST /csv       $0.001  convierte una lista de registros JSON a CSV
  POST /markdown  $0.002  convierte HTML a Markdown limpio
  POST /extract   $0.005  extrae JSON estructurado de texto libre (trabajador: DeepSeek)
  POST /promote   $0.02   kit publicitario x402 para el servicio del cliente (DeepSeek)

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
"""
import csv
import io
import json
import os
import threading
import time

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from json_repair import repair_json
from markdownify import markdownify
import jsonschema

from brain import get_brain

PAY_TO = os.getenv("PAY_TO", "")
NETWORK = os.getenv("NETWORK", "base")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "200000"))

PRICES = {
    "/repair": os.getenv("PRICE_REPAIR", "$0.001"),
    "/validate": os.getenv("PRICE_VALIDATE", "$0.001"),
    "/csv": os.getenv("PRICE_CSV", "$0.001"),
    "/markdown": os.getenv("PRICE_MARKDOWN", "$0.002"),
    "/extract": os.getenv("PRICE_EXTRACT", "$0.005"),
    "/promote": os.getenv("PRICE_PROMOTE", "$0.02"),
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
}

app = FastAPI(
    title="agent-data-toolkit",
    version="2.0.0",
    description="Pay-per-call data utilities for AI agents (x402 / USDC on Base).",
)

# Middleware x402: responde HTTP 402 con instrucciones de pago; el agente paga en
# USDC y reintenta con el header X-PAYMENT. Sin PAY_TO corre gratis (solo dev).
if PAY_TO:
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
    broken: str
    schema_: dict | None = None


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
    data: dict | list
    schema_: dict


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
    text: str
    schema_: dict
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
                 "GET /health": "service status", "GET /docs": "Swagger UI"},
        "payment": {"protocol": "x402", "currency": "USDC", "network": NETWORK},
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


# --------------------------------------------------------------------------- health
@app.get("/health")
def health():
    return {
        "status": "ok",
        "paid": bool(PAY_TO),
        "network": NETWORK,
        "worker": "deepseek-chat" if DEEPSEEK_API_KEY else None,
        "prices": PRICES,
    }
