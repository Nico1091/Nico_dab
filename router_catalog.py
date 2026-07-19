"""Catálogo de servicios enrutables del /route (x402-router integrado).

Fuentes, en orden de confianza:
  1. Semilla inyectada por main.py: los propios endpoints de este toolkit
     (precio, método y descripción conocidos — siempre disponibles).
  2. La vitrina del x402 Trust Guard (GET /verified): servicios de terceros con
     compra real probada on-chain. A cada candidato se le sondea su 402 para
     conocer precio, descripción y versión ANTES de admitirlo.

Solo se admite lo que el pagador puede pagar de verdad: ofertas x402 v1 en
Base con precio ≤ MAX_ROUTE_SPEND_ATOMIC. El resto queda listado como
"not_routable" por transparencia.
"""
import asyncio
import base64
import ipaddress
import json
import os
import socket
import time
from urllib.parse import urlsplit

import httpx

TRUST_GUARD_URL = os.getenv(
    "TRUST_GUARD_URL", "https://nico222222222-x402-trust-guard.hf.space")
CATALOG_TTL_HOURS = float(os.getenv("CATALOG_TTL_HOURS", "6"))
CATALOG_MAX_EXTERNAL = int(os.getenv("CATALOG_MAX_EXTERNAL", "40"))
MAX_ROUTE_SPEND_ATOMIC = int(os.getenv("MAX_ROUTE_SPEND_ATOMIC", "5000"))
USDC_DECIMALS = 6


def url_is_safe(url: str) -> bool:
    """Solo http(s) hacia hosts públicos: bloquea loopback/red privada (SSRF)."""
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    host = p.hostname
    if host == "localhost" or host.endswith(".local"):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True  # no resuelve: el probe lo descartará como unreachable
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    return True


def _is_base_network(network) -> bool:
    n = str(network or "")
    return n.startswith("base") or n in ("eip155:8453", "eip155:84532")


def _pick_accept(accepts: list) -> dict | None:
    if not accepts:
        return None
    for a in accepts:
        if _is_base_network(a.get("network")):
            return a
    return accepts[0]


def _extract_offer(r) -> tuple[dict | None, int | None]:
    """(accept_elegido, x402Version) del 402: v1 en el cuerpo, v2 en el header."""
    try:
        body = r.json()
    except Exception:
        body = {}
    if isinstance(body, dict) and body.get("accepts"):
        chosen = _pick_accept(body["accepts"])
        if chosen:
            return chosen, int(body.get("x402Version") or 1)
    hdr = r.headers.get("payment-required")
    if hdr:
        try:
            v2 = json.loads(base64.b64decode(hdr))
            chosen = _pick_accept(v2.get("accepts") or [])
            if chosen:
                return chosen, int(v2.get("x402Version") or 2)
        except Exception:
            pass
    return None, None


async def _probe(client: httpx.AsyncClient, resource: str) -> dict | None:
    """Sondea el 402 de un candidato externo; entry si su oferta es pagable."""
    method = "POST"
    try:
        r = await client.request(method, resource, json={})
        if r.status_code != 402:
            r2 = await client.get(resource)
            if r2.status_code == 402:
                r, method = r2, "GET"
            else:
                return None
    except Exception:
        return None
    offer, version = _extract_offer(r)
    if not offer or version not in (None, 1):
        return None  # el pagador solo habla x402 v1 por ahora
    try:
        price = int(offer.get("maxAmountRequired") or offer.get("amount"))
    except (TypeError, ValueError):
        return None
    if (offer.get("scheme") != "exact" or not _is_base_network(offer.get("network"))
            or price <= 0):
        return None
    return {
        "resource": resource,
        "method": method,
        "price_atomic": price,
        "description": (offer.get("description") or "")[:200],
    }


class Catalog:
    def __init__(self, seed: list[dict]):
        self.seed = seed
        self.entries: dict[str, dict] = {}
        self.last_refresh: float | None = None
        self._lock = asyncio.Lock()

    @property
    def stale(self) -> bool:
        return (self.last_refresh is None
                or time.time() - self.last_refresh > CATALOG_TTL_HOURS * 3600)

    async def refresh(self, force: bool = False) -> None:
        if not (force or self.stale):
            return
        async with self._lock:
            if not (force or self.stale):  # otro caller ya refrescó
                return
            entries = {}
            for e in self.seed:
                entries[e["resource"]] = {**e, "source": "seed",
                                          "verified_by": "operator"}
            try:
                async with httpx.AsyncClient(timeout=20) as c:
                    r = await c.get(f"{TRUST_GUARD_URL}/verified")
                    services = (r.json().get("services") or [])[:CATALOG_MAX_EXTERNAL]
                    for s in services:
                        url = s.get("resource") or ""
                        if url in entries or not url_is_safe(url):
                            continue
                        entry = await _probe(c, url)
                        if entry:
                            entry["source"] = "trust-guard"
                            entry["verified_by"] = s.get("proof_tx")
                            entries[url] = entry
            except Exception:
                pass  # sin trust-guard seguimos con la semilla: nunca vacío
            self.entries = entries
            self.last_refresh = time.time()

    def get(self, resource: str) -> dict | None:
        return self.entries.get(resource)

    def routable(self) -> list[dict]:
        return [e for e in self.entries.values()
                if e["price_atomic"] <= MAX_ROUTE_SPEND_ATOMIC]

    def skipped(self) -> list[dict]:
        return [e for e in self.entries.values()
                if e["price_atomic"] > MAX_ROUTE_SPEND_ATOMIC]
