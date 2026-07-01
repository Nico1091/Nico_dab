"""json-repair-api — servicios de datos para agentes de IA, cobrados en USDC vía x402.

Endpoints de pago (categoría "data", la de mayor demanda del ecosistema x402):
  POST /repair    $0.001  repara JSON malformado (cómputo puro)
  POST /validate  $0.001  valida datos contra un JSON Schema y lista los errores
  POST /csv       $0.001  convierte una lista de registros JSON a CSV
  POST /markdown  $0.002  convierte HTML a Markdown limpio
  POST /extract   $0.005  extrae JSON estructurado de texto libre (trabajador: DeepSeek)

Config por variables de entorno (.env soportado):
  PAY_TO            — dirección pública de tu wallet en Base (0x...), obligatoria en producción
  NETWORK           — "base" (mainnet) o "base-sepolia" (pruebas)
  DEEPSEEK_API_KEY  — clave de https://platform.deepseek.com (para /extract)
"""
import csv
import io
import json
import os

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

    for path, price in PRICES.items():
        app.middleware("http")(
            require_payment(path=path, price=price,
                            pay_to_address=PAY_TO, network=NETWORK)
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
