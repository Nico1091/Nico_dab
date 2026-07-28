"""Cobro x402 v2 — el que AgentCash sí acepta.

Por qué existe
--------------
El servicio nació con el SDK `x402<2`, que habla el protocolo **v1**: la oferta
de pago viaja en el cuerpo JSON del 402 y la red se nombra "base". El validador
oficial de AgentCash rechaza eso con `X402_VERSION_V1_NOT_SUPPORTED`, y
AgentCash es la capa de pago que corre dentro de Claude Code, Cursor, Windsurf,
Codex y Claude Desktop. Sin v2 no hay canal ahí, por buena que sea la API.

En v2 cambia casi todo por fuera: la oferta va en el header `PAYMENT-REQUIRED`,
la red se nombra en CAIP-2 (`eip155:8453` para Base mainnet), y el middleware se
arma con un `x402ResourceServer` en vez de un decorador por ruta.

Compatibilidad
--------------
Este módulo NO sustituye al camino v1: convive con él. `main.py` detecta qué
SDK hay instalado y usa uno u otro. Si v2 diera problemas en producción, basta
con volver a fijar `x402<2` en requirements.txt: el código sigue funcionando sin
tocar una línea. El cobro es lo último que se puede permitir romper.
"""
import os

# Base mainnet y su testnet en CAIP-2, que es como v2 nombra las redes.
CAIP2 = {
    "base": "eip155:8453",
    "base-sepolia": "eip155:84532",
}


def disponible() -> bool:
    """True si el SDK instalado habla v2."""
    try:
        import x402.http.middleware.fastapi  # noqa: F401
        return True
    except ImportError:
        return False


def _extension_descubrimiento(path: str, descripcion: str,
                              campos_entrada, esquema_salida, ejemplo_salida,
                              esquema_recurso):
    """Bloque de discovery del Bazaar: qué recibe y qué devuelve el endpoint.

    Es el equivalente v2 de input_schema/output_schema. Sin él, el validador de
    AgentCash marca SCHEMA_OUTPUT_MISSING y el recurso no se lista.
    """
    from x402.extensions.bazaar.types import (
        BAZAAR, BodyDiscoveryExtension, BodyDiscoveryInfo, BodyInput, OutputInfo,
    )

    entrada = BodyInput(
        type="http",
        method="POST",
        body_type="json",
        body=campos_entrada or {},
    )
    # `example` es un EJEMPLO de respuesta, no el esquema: v2 lo valida contra
    # `schema` al registrar la ruta. Confundirlos deja el recurso mal formado.
    salida = OutputInfo(type="json", format=None, example=ejemplo_salida)
    ext = BodyDiscoveryExtension(
        info=BodyDiscoveryInfo(input=entrada, output=salida),
        # `schema` es obligatorio en v2 y NO es el esquema de la respuesta: el
        # validador lo sondea como `schema.properties.input` y `.output`, o
        # sea, el par entrada+salida completo. Declarar solo la salida da
        # SCHEMA_INPUT_MISSING contra el 402 real (en el openapi no se ve).
        schema=esquema_recurso or {"type": "object"},
    )
    # BAZAAR es un FacilitatorExtension, no una cadena: la clave del diccionario
    # de extensiones es su .key ("bazaar").
    return {BAZAAR.key: ext.model_dump(by_alias=True, exclude_none=True)}


def construir_middleware(*, prices, descriptions, pay_to, network,
                         base_url, input_body_fields, output_schema_for,
                         example_for, resource_schema_for):
    """Devuelve el middleware FastAPI de cobro v2, o None si no se puede montar.

    Se le pasan las mismas fuentes de verdad que usa el camino v1 (PRICES,
    DESCRIPTIONS y los esquemas de E/S), para que las dos versiones no puedan
    divergir en precios ni en la forma de los datos.
    """
    from x402 import x402ResourceServer
    from x402.http import HTTPFacilitatorClient
    from x402.http.middleware.fastapi import payment_middleware
    from x402.mechanisms.evm.exact import ExactEvmServerScheme

    red = CAIP2.get(network, network)

    # Facilitador de Coinbase (CDP) si hay claves: es el que además indexa el
    # recurso en el Bazaar. Sin claves, el facilitador público por defecto:
    # cobra igual, pero no lista.
    config_facilitador = None
    if os.getenv("CDP_API_KEY_ID") and os.getenv("CDP_API_KEY_SECRET"):
        from cdp.x402 import create_facilitator_config

        cdp = create_facilitator_config(
            os.getenv("CDP_API_KEY_ID"), os.getenv("CDP_API_KEY_SECRET")
        )
        # A diferencia de v1, aquí create_headers se espera SÍNCRONA, que es
        # justo lo que entrega cdp-sdk. El wrapper async que hacía falta en v1
        # sobra en v2: envolverlo rompería la autenticación.
        config_facilitador = {"url": cdp["url"],
                              "create_headers": cdp["create_headers"]}

    facilitador = HTTPFacilitatorClient(config_facilitador)
    server = x402ResourceServer(facilitador)
    server.register(red, ExactEvmServerScheme())

    rutas = {}
    for path, precio in prices.items():
        rutas[f"POST {path}"] = {
            # OJO: en v2 la descripción, el recurso y el mimeType van al nivel
            # de la RUTA, no dentro de accepts. Puestos en accepts se ignoran
            # en silencio y el 402 sale con la descripción vacía — que es
            # justamente el texto con el que el agente decide si compra.
            "description": descriptions.get(path, ""),
            "resource": base_url + path,
            "mimeType": "application/json",
            "serviceName": "agent-data-toolkit",
            "iconUrl": base_url + "/favicon.ico",
            "accepts": [{
                "scheme": "exact",
                "payTo": pay_to,
                "price": precio,
                "network": red,
                "maxTimeoutSeconds": 60,
            }],
            "extensions": _extension_descubrimiento(
                path, descriptions.get(path, ""),
                input_body_fields(path), output_schema_for(path),
                example_for(path), resource_schema_for(path),
            ),
        }

    return payment_middleware(rutas, server)
