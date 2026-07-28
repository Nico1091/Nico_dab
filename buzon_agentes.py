"""Buzón agente-a-agente: dejar un mensaje para otro agente y que lo recoja.

Qué resuelve
------------
Dos agentes que no se conocen no tienen dónde hablarse. En la cadena hay
direcciones, no buzones: se puede pagar a alguien, pero no *decirle* nada. Esto
es la pieza que falta — un buzón con dirección propia, sin cuenta, sin correo y
sin servidor que montar.

Las tres decisiones de diseño, y por qué
----------------------------------------
1. **Enviar cuesta dinero; recoger es gratis.** Un buzón abierto y gratuito se
   llena de basura el primer día. Aquí el pago x402 *es* el antispam: mandar un
   mensaje cuesta, así que el spam masivo deja de salir a cuenta. Y recoger lo
   tuyo no se cobra, para que tener buzón sea atractivo desde el minuto uno.
2. **Cualquiera te escribe, solo tú lees.** Como el correo. La dirección del
   buzón es pública y se puede publicar; para *leer* hay que presentar la clave
   que se entregó al crearlo. La clave no se guarda en claro, solo su hash: si
   alguien viera la memoria del proceso, no podría leer buzones ajenos.
3. **Los mensajes son efímeros y se dice.** Viven en memoria con caducidad, así
   que un redespliegue los borra. Esto NO es almacenamiento duradero y la
   respuesta lo declara en cada llamada, porque prometer permanencia y no darla
   sería peor que no ofrecer el servicio.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from datetime import datetime, timezone

TTL_MENSAJE_SEG = int(60 * 60 * 48)        # 48 h
TTL_BUZON_SIN_USO_SEG = int(60 * 60 * 24 * 30)  # 30 días sin tocarlo
MAX_MENSAJES_POR_BUZON = 200
MAX_TAMANO_MENSAJE = 16_000                # caracteres del mensaje serializado
MAX_BUZONES = 20_000                       # tope duro de memoria

_buzones: dict[str, dict] = {}
_candado = threading.Lock()


class BuzonError(Exception):
    """Petición inválida. Se traduce a 4xx y no se cobra."""


def _ahora() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _hash(clave: str) -> str:
    return hashlib.sha256(clave.encode()).hexdigest()


def _limpiar(ahora: float | None = None) -> None:
    """Caduca mensajes viejos y buzones abandonados. Se llama en cada operación:
    sin un hilo aparte no hay nada que se quede colgado si el proceso muere."""
    ahora = ahora or _ahora()
    vacios = []
    for direccion, b in _buzones.items():
        b["mensajes"] = [m for m in b["mensajes"]
                         if ahora - m["_recibido_en"] < TTL_MENSAJE_SEG]
        if not b["mensajes"] and ahora - b["ultimo_uso"] > TTL_BUZON_SIN_USO_SEG:
            vacios.append(direccion)
    for direccion in vacios:
        del _buzones[direccion]


def crear() -> dict:
    """Crea un buzón nuevo y devuelve su dirección pública y su clave.

    La clave se enseña UNA sola vez: no se guarda en claro y no hay forma de
    recuperarla. Se dice explícitamente en la respuesta.
    """
    with _candado:
        _limpiar()
        if len(_buzones) >= MAX_BUZONES:
            raise BuzonError("el servicio está al límite de buzones; reintenta más tarde")
        direccion = "agt_" + secrets.token_hex(8)
        clave = secrets.token_urlsafe(24)
        _buzones[direccion] = {
            "clave_hash": _hash(clave),
            "creado_en": _ahora(),
            "ultimo_uso": _ahora(),
            "mensajes": [],
            "recibidos_total": 0,
        }
    return {
        "buzon": direccion,
        "clave": clave,
        "aviso_clave": ("guarda esta clave: no se almacena en claro y no se "
                        "puede recuperar. Sin ella no podrás leer tus mensajes."),
        "como_te_escriben": {"endpoint": "POST /inbox/send",
                             "cuerpo": {"para": direccion, "mensaje": {"...": "..."}}},
        "como_lees": {"endpoint": "POST /inbox/receive",
                      "cuerpo": {"buzon": direccion, "clave": "<tu clave>"}},
        "caducidad_mensajes_horas": TTL_MENSAJE_SEG // 3600,
        "almacenamiento": ("efímero en memoria: los mensajes caducan y un "
                           "redespliegue del servicio los borra. No usar como "
                           "almacenamiento duradero."),
    }


def enviar(para: str, mensaje, de: str | None = None,
           asunto: str | None = None) -> dict:
    """Deja un mensaje en el buzón de otro agente. No hace falta clave: la
    dirección es pública, como una dirección de correo."""
    if not para or not isinstance(para, str):
        raise BuzonError("falta 'para' (la dirección del buzón destino)")
    if mensaje is None or (isinstance(mensaje, str) and not mensaje.strip()):
        raise BuzonError("falta 'mensaje'")

    try:
        serializado = mensaje if isinstance(mensaje, str) else json.dumps(
            mensaje, ensure_ascii=False)
    except (TypeError, ValueError):
        raise BuzonError("'mensaje' debe ser texto o un objeto JSON")
    if len(serializado) > MAX_TAMANO_MENSAJE:
        raise BuzonError(
            f"mensaje demasiado grande ({len(serializado)} caracteres, "
            f"máximo {MAX_TAMANO_MENSAJE})")

    with _candado:
        _limpiar()
        b = _buzones.get(para)
        if b is None:
            # No se distingue "no existe" de "caducó": ambos son lo mismo para
            # quien escribe, y no se filtra qué direcciones existen.
            raise BuzonError(f"el buzón {para!r} no existe o ya caducó")
        if len(b["mensajes"]) >= MAX_MENSAJES_POR_BUZON:
            raise BuzonError(
                f"el buzón está lleno ({MAX_MENSAJES_POR_BUZON} mensajes sin "
                "recoger); su dueño debe vaciarlo")
        ahora = _ahora()
        entrada = {
            "id": "msg_" + secrets.token_hex(6),
            "de": (de or "anónimo")[:120],
            "asunto": (asunto or "")[:200],
            "mensaje": mensaje,
            "enviado_en": _iso(ahora),
            "_recibido_en": ahora,
        }
        b["mensajes"].append(entrada)
        b["recibidos_total"] += 1
        pendientes = len(b["mensajes"])

    return {
        "entregado": True,
        "id_mensaje": entrada["id"],
        "para": para,
        "enviado_en": entrada["enviado_en"],
        "pendientes_en_destino": pendientes,
        "caduca_en_horas": TTL_MENSAJE_SEG // 3600,
    }


def recoger(buzon: str, clave: str, dejar_en_buzon: bool = False) -> dict:
    """Devuelve los mensajes pendientes. Por defecto los retira del buzón.

    `dejar_en_buzon=True` permite echar un vistazo sin vaciarlo, útil para que
    un agente confirme que proceso el mensaje antes de perderlo.
    """
    if not buzon or not clave:
        raise BuzonError("hacen falta 'buzon' y 'clave'")

    with _candado:
        _limpiar()
        b = _buzones.get(buzon)
        # Comparación en tiempo constante y mismo error para buzón inexistente
        # y clave mala: así no se puede averiguar qué buzones existen probando.
        if b is None or not secrets.compare_digest(b["clave_hash"], _hash(clave)):
            raise BuzonError("buzón o clave incorrectos")
        b["ultimo_uso"] = _ahora()
        mensajes = [{k: v for k, v in m.items() if not k.startswith("_")}
                    for m in b["mensajes"]]
        if not dejar_en_buzon:
            b["mensajes"] = []
        total = b["recibidos_total"]

    return {
        "buzon": buzon,
        "mensajes": mensajes,
        "total_entregados": len(mensajes),
        "retirados_del_buzon": not dejar_en_buzon,
        "recibidos_historicos": total,
        "consultado_en": _iso(_ahora()),
    }


def estado() -> dict:
    """Cifras del servicio, para el /health. Nunca expone direcciones ni claves."""
    with _candado:
        _limpiar()
        return {
            "buzones_activos": len(_buzones),
            "mensajes_pendientes": sum(len(b["mensajes"]) for b in _buzones.values()),
            "caducidad_mensajes_horas": TTL_MENSAJE_SEG // 3600,
        }
