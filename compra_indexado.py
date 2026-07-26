"""Compra 1 vez cada endpoint nuevo del toolkit para disparar el indexado del Bazaar CDP.
La clave de Cuenta 3 se lee de Downloads\\API KEYS.txt al correr; NUNCA se imprime.
Gasto total esperado: /features $0.003 + /resolve $0.008 + /edge $0.02 = $0.031
"""
import asyncio, base64, json, sys
from pathlib import Path

from eth_account import Account
from x402.clients.httpx import x402HttpxClient

BASE = "https://agent-data-toolkit.onrender.com"

KEYS_FILE = Path.home() / "Downloads" / "API KEYS.txt"


def clave_cuenta3() -> str:
    for linea in KEYS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        if linea.startswith("CUENTA 3 BASE:"):
            return linea.split(":", 1)[1].strip()
    sys.exit("No encontré 'CUENTA 3 BASE:' en API KEYS.txt")


COMPRAS = [
    ("/features", {"symbol": "BTCUSDT", "interval": "1h"}, 120),
    ("/resolve", {"question": "¿BTC cerró por encima de 100000 USD el 1 de julio de 2026?",
                  "deadline": "2026-07-01"}, 180),
    ("/edge", {"question": "¿Conviene apostar a que BTC sube esta semana?"}, 300),
]


async def comprar(cuenta, path, payload, timeout):
    # TRAMPA conocida: el cliente solo paga la PRIMERA petición de su sesión → cliente NUEVO por compra
    async with x402HttpxClient(account=cuenta, base_url=BASE, timeout=timeout) as cli:
        resp = await cli.post(path, json=payload)
        body = await resp.aread()
        tx = "?"
        header = resp.headers.get("x-payment-response")
        if header:
            try:
                dec = json.loads(base64.b64decode(header))
                tx = dec.get("transaction") or dec.get("txHash") or str(dec)[:120]
            except Exception:
                tx = header[:120]
        print(f"{path}: status={resp.status_code} tx={tx}")
        if resp.status_code == 402:
            # pago rechazado: imprimir motivo (campo error) y cuerpo completo
            try:
                d = json.loads(body)
                print(f"  ERROR del servidor: {d.get('error')!r}")
            except Exception:
                pass
            print(f"  body completo: {body.decode('utf-8', 'replace')}")
        else:
            print(f"  body: {body[:200].decode('utf-8', 'replace')}")
        return resp.status_code, tx


async def main():
    cuenta = Account.from_key(clave_cuenta3())
    print(f"Pagador: {cuenta.address}")
    resultados = []
    for path, payload, timeout in COMPRAS:
        try:
            resultados.append((path, *await comprar(cuenta, path, payload, timeout)))
        except Exception as e:
            print(f"{path}: ERROR {type(e).__name__}: {e}")
            resultados.append((path, "ERR", str(e)[:100]))
    print("\nRESUMEN:")
    for r in resultados:
        print(" ", r)


asyncio.run(main())
