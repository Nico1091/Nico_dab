"""Documento de discovery /openapi.json conforme a AgentCash.

Por qué existe
--------------
AgentCash es la capa de pago x402 integrada en Claude Code, Cursor, Windsurf,
Codex y Claude Desktop: el agente hace `agentcash discover <origen>` y recibe el
catálogo de endpoints pagables sin que intervenga una persona. Su formato
canónico de descubrimiento es el propio `GET /openapi.json`, pero con campos que
FastAPI no genera solo:

  info.x-guidance        instrucciones de alto nivel para el agente
  info.contact.email     habilita la verificación de propiedad del origen
  responses.402          la operación debe declarar el reto de pago
  x-payment-info         protocolos aceptados + precio de ESA operación

Regla de conversión que hay que respetar (documentada por AgentCash): en el
OpenAPI el precio va en **USD decimal**; en tiempo de ejecución el `accepts[].
amount` del 402 va en **unidades atómicas** del token (USDC: 0.01 -> "10000").
Las dos cifras salen aquí del mismo PRICES, así que no pueden divergir.

Este módulo no toca el cobro: solo describe lo que el middleware x402 ya hace.
"""
from fastapi.openapi.utils import get_openapi

# Rutas que NO se anuncian en el discovery aunque existan en la app:
# el gateway fiat vive detrás del secreto de RapidAPI y responde 401/503 a un
# agente x402. Anunciarlo sería mandar al comprador a una puerta cerrada.
_HIDDEN_PREFIXES = ("/fiat",)

_GUIDANCE = (
    "Pay-per-call data utilities for AI agents. No account, no API key, no "
    "subscription: every paid route answers HTTP 402 with x402 payment "
    "instructions (USDC on Base), you pay and retry. Prices below are in USD "
    "per call. Start with POST /repair to fix malformed JSON from an LLM, or "
    "GET /catalog and GET /.well-known/x402 (both free) to see everything this "
    "origin sells. Payment is only charged when the response is 2xx: invalid "
    "input returns 4xx and costs nothing."
)

_402_RESPONSE = {
    "description": (
        "Payment Required. The body carries the x402 challenge: pay the amount "
        "in `accepts[].maxAmountRequired` (atomic USDC units) to `accepts[]."
        "payTo` on the given network and retry with the X-PAYMENT header."
    ),
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "x402Version": {"type": "integer", "example": 1},
                    "error": {"type": "string"},
                    "accepts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "scheme": {"type": "string", "example": "exact"},
                                "network": {"type": "string", "example": "base"},
                                "maxAmountRequired": {
                                    "type": "string",
                                    "description": "atomic USDC units (6 decimals)",
                                    "example": "1000",
                                },
                                "resource": {"type": "string"},
                                "description": {"type": "string"},
                                "payTo": {"type": "string"},
                                "asset": {"type": "string"},
                                "maxTimeoutSeconds": {"type": "integer"},
                            },
                        },
                    },
                },
            }
        }
    },
}


# ---------------------------------------------------------------- esquemas E/S
# El validador de AgentCash marca SCHEMA_OUTPUT_MISSING si el 402 no dice qué
# devuelve el endpoint: un agente no compra a ciegas algo cuya forma de
# respuesta desconoce. x402 1.x acepta input_schema/output_schema en
# require_payment, así que se declaran aquí y viajan dentro de accepts[].
#
# Se escriben a mano y no se derivan de los modelos Pydantic a propósito: el
# middleware se registra antes de que los modelos existan, y además la salida
# de estos endpoints es un dict construido a mano, no un response_model.

def _obj(props: dict, required: list[str] | None = None) -> dict:
    s = {"type": "object", "properties": props}
    if required:
        s["required"] = required
    return s


_STR = {"type": "string"}
_BOOL = {"type": "boolean"}
_NUM = {"type": "number"}
_INT = {"type": "integer"}
_ANY: dict = {}
_OK = {"ok": _BOOL}

IO_SCHEMAS: dict[str, dict] = {
    "/repair": {
        "in": {"broken": "string, the malformed JSON text (required)",
               "schema": "object, optional JSON Schema to validate the result against"},
        "out": _obj({**_OK, "repaired": _ANY, "worker": _STR,
                     "schema_valid": _BOOL, "schema_error": _STR,
                     "error": _STR}, ["ok"]),
    },
    "/validate": {
        "in": {"data": "object or array to validate (required)",
               "schema": "object, the JSON Schema to validate against (required)"},
        "out": _obj({**_OK, "valid": _BOOL,
                     "errors": {"type": "array", "items": _obj(
                         {"path": _STR, "message": _STR})}}, ["ok", "valid"]),
    },
    "/csv": {
        "in": {"records": "array of objects (required, min 1)",
               "delimiter": "string, defaults to ','"},
        "out": _obj({**_OK, "csv": _STR, "rows": _INT}, ["ok", "csv"]),
    },
    "/markdown": {
        "in": {"html": "string, raw HTML (required)"},
        "out": _obj({**_OK, "markdown": _STR}, ["ok", "markdown"]),
    },
    "/extract": {
        "in": {"text": "string, free-form text (required)",
               "schema": "object, JSON Schema describing what to extract (required)",
               "instructions": "string, optional extra guidance"},
        "out": _obj({**_OK, "data": _ANY, "schema_valid": _BOOL,
                     "schema_error": _STR}, ["ok", "data"]),
    },
    "/promote": {
        "in": {"name": "string (required)", "url": "string (required)",
               "what_it_does": "string (required)", "pricing": "string, optional",
               "audience": "string, optional", "language": "string, defaults to 'en'"},
        "out": _obj({**_OK, "kit": _ANY}, ["ok", "kit"]),
    },
    "/ask": {
        "in": {"prompt": "string, the question or instruction (required)",
               "schema": "object, optional JSON Schema for the answer"},
        "out": _obj({**_OK, "answer": _ANY, "worker": _STR}, ["ok", "answer"]),
    },
    "/features": {
        "in": {"symbol": "string, e.g. 'BTCUSDT' or an alias like 'bittensor'",
               "interval": "string, candle interval (1h, 1d, ...)"},
        "out": _obj({**_OK, "symbol": _STR, "interval": _STR, "rsi": _NUM,
                     "sma": _ANY, "momentum": _ANY, "volatility": _NUM,
                     "drawdown": _NUM, "range_position": _NUM,
                     "volume_trend": _NUM, "as_of": _STR}, ["ok", "symbol"]),
    },
    "/resolve": {
        "in": {"question": "string, the price question to settle (required)",
               "symbol": "string, optional explicit symbol",
               "date": "string, UTC date YYYY-MM-DD"},
        "out": _obj({**_OK, "question": _STR, "answer": _STR, "close": _NUM,
                     "threshold": _NUM, "date": _STR, "source_url": _STR},
                    ["ok", "question"]),
    },
    "/edge": {
        "in": {"question": "string, the betting question (required)",
               "horizon_hours": "integer, optional forecast horizon"},
        "out": _obj({**_OK, "question": _STR, "symbol": _STR,
                     "horizon_hours": _INT, "probability": _NUM,
                     "confidence": _NUM, "reasons": {"type": "array",
                                                     "items": _STR},
                     "features": _ANY, "worker": _STR, "latency_ms": _INT,
                     "disclaimer": _STR}, ["ok", "question"]),
    },
    "/x402series": {
        "in": {"seller": "string, optional seller domain; omit for the whole market",
               "limit": "integer, optional number of points"},
        "out": _obj({**_OK, "seller": _STR, "points": {"type": "array",
                                                       "items": _ANY},
                     "as_of": _STR}, ["ok"]),
    },
    "/x402report": {
        "in": {},
        "out": _obj({**_OK, "totals": _ANY, "trend": _ANY,
                     "concentration": _ANY, "top_sellers": {"type": "array",
                                                            "items": _ANY},
                     "as_of": _STR}, ["ok"]),
    },
    "/x402ask": {
        "in": {"question": "string, a question about the x402 market (required)"},
        "out": _obj({**_OK, "question": _STR, "answer": _STR,
                     "figures": {"type": "array", "items": _ANY},
                     "grounded_in_dossier": _BOOL, "as_of": _STR,
                     "analysis_author": _STR, "worker": _STR},
                    ["ok", "answer", "grounded_in_dossier"]),
    },
}


def input_schema_for(path: str):
    """HTTPInputSchema para require_payment, o None si la ruta no está descrita."""
    spec = IO_SCHEMAS.get(path)
    if spec is None:
        return None
    from x402.types import HTTPInputSchema

    return HTTPInputSchema(bodyType="json", bodyFields=spec["in"])


def output_schema_for(path: str):
    spec = IO_SCHEMAS.get(path)
    return spec["out"] if spec else None


def input_body_fields(path: str):
    """Los campos de entrada en crudo, para construir el well-known a mano."""
    spec = IO_SCHEMAS.get(path)
    return spec["in"] if spec else None


_TIPOS_TEXTO = {
    "string": "string", "object": "object", "array": "array",
    "integer": "integer", "number": "number", "boolean": "boolean",
}


def input_json_schema(path: str):
    """JSON Schema de ENTRADA, derivado de las descripciones de IO_SCHEMAS.

    Se deriva en vez de escribirse aparte para que no haya dos verdades: el
    texto que lee el agente y el esquema que valida son la misma fuente.
    """
    campos = input_body_fields(path)
    if campos is None:
        return None
    props, requeridos = {}, []
    for nombre, texto in campos.items():
        cabeza = texto.split(",")[0].strip().lower()
        tipo = _TIPOS_TEXTO.get(cabeza.split()[0] if cabeza else "", "string")
        props[nombre] = {"type": tipo, "description": texto}
        if "(required)" in texto:
            requeridos.append(nombre)
    esquema = {"type": "object", "properties": props}
    if requeridos:
        esquema["required"] = requeridos
    return esquema


def resource_schema_for(path: str):
    """El `schema` de la extensión bazaar.

    No es el esquema de la respuesta ni el de la petición: es el **meta-esquema
    del bloque `info`**. El validador lo recorre exactamente así:

        schema.properties.input.properties.body      -> esquema de la petición
        schema.properties.output.properties.example  -> esquema de la respuesta

    (`queryParams` sirve de alternativa a `body` para endpoints GET.)
    Cualquier otra anidación se lee como "falta el esquema", aunque los datos
    estén ahí. Costó dos intentos: se ve solo sondeando el 402 real, no en el
    documento OpenAPI.
    """
    entrada, salida = input_json_schema(path), output_schema_for(path)
    if entrada is None and salida is None:
        return None
    return {
        "type": "object",
        "properties": {
            "input": {
                "type": "object",
                "properties": {"body": entrada or {"type": "object"}},
            },
            "output": {
                "type": "object",
                "properties": {"example": salida or {"type": "object"}},
            },
        },
    }


_EJEMPLO_POR_TIPO = {
    "string": "", "number": 0, "integer": 0,
    "boolean": True, "array": [], "object": {},
}


def example_for(path: str):
    """Ejemplo mínimo de respuesta que SATISFACE el esquema de salida.

    x402 v2 valida `example` contra `schema` al registrar la extensión bazaar,
    así que un ejemplo inventado a mano se convierte en un aviso en el arranque
    y en un recurso mal formado para el Bazaar. Se genera desde el propio
    esquema —solo los campos requeridos— para que no puedan desincronizarse.
    """
    esquema = output_schema_for(path)
    if not esquema:
        return None
    props = esquema.get("properties", {})
    ejemplo = {}
    for campo in esquema.get("required", []):
        tipo = props.get(campo, {}).get("type")
        ejemplo[campo] = True if campo == "ok" else _EJEMPLO_POR_TIPO.get(tipo, None)
    return ejemplo


def _decimal_usd(price: str) -> str:
    """'$0.001' -> '0.001'. AgentCash quiere USD decimal, no atómico ni con $."""
    return price.lstrip("$").strip()


def build_openapi(app, *, prices, descriptions, pay_to, network,
                  base_url, contact_email):
    """Devuelve el esquema OpenAPI enriquecido para AgentCash (con caché)."""
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    info = schema.setdefault("info", {})
    info["x-guidance"] = _GUIDANCE
    if contact_email:
        info.setdefault("contact", {})["email"] = contact_email

    schema["servers"] = [{"url": base_url}]

    # Prueba de propiedad del origen. La especificación de x402scan pide aquí
    # una lista de DIRECCIONES ("0x..."), no objetos descriptivos: es la wallet
    # que cobra, que es lo que ata este dominio a un titular verificable.
    if pay_to:
        schema["x-discovery"] = {"ownershipProofs": [pay_to]}

    for path, item in list(schema.get("paths", {}).items()):
        if path.startswith(_HIDDEN_PREFIXES):
            del schema["paths"][path]
            continue
        price = prices.get(path)
        if not price:
            # Ruta gratis: se anuncia igual, pero hay que decirlo de forma
            # explícita. El validador infiere el modo de autenticación de
            # `security`, y una lista vacía significa "sin protección"; si se
            # omite, avisa con L2_AUTH_MODE_MISSING y el agente no sabe si
            # puede llamarla sin pagar.
            for method, operation in item.items():
                if method in ("get", "post", "put", "patch", "delete"):
                    operation["security"] = []
            continue
        for method, operation in item.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            operation["x-payment-info"] = {
                "protocols": [{"x402": {}}],
                "price": {
                    "mode": "fixed",
                    "currency": "USD",
                    "amount": _decimal_usd(price),
                },
            }
            if descriptions.get(path):
                operation["description"] = descriptions[path]
            # Solo hay reto de pago real si el servicio corre con PAY_TO; sin
            # él (modo dev) declarar un 402 que nunca llega sería mentir al
            # validador de AgentCash, que contrasta documento contra runtime.
            if pay_to:
                operation.setdefault("responses", {})["402"] = _402_RESPONSE

    app.openapi_schema = schema
    return schema
