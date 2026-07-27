"""Pruebas del documento de discovery para AgentCash (sin red, sin pagos).

Contrastan el /openapi.json contra la especificación publicada por AgentCash:
campos obligatorios, x-payment-info por operación, y la regla de conversión
USD decimal (documento) <-> unidades atómicas (runtime del 402).
"""
import os
import sys

os.environ.setdefault("PAY_TO", "0x92Cb873c2d114d6Aa847EB110F721DA22B5220F7")
os.environ.setdefault("NETWORK", "base")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

client = TestClient(main.app)
DOC = main.app.openapi()

fallos = []


def check(nombre, condicion, detalle=""):
    if condicion:
        print(f"  ok   {nombre}")
    else:
        print(f"  FALLA {nombre} {detalle}")
        fallos.append(nombre)


print("== campos obligatorios de info ==")
info = DOC.get("info", {})
check("info.title", bool(info.get("title")))
check("info.version", bool(info.get("version")))
check("info.x-guidance", bool(info.get("x-guidance")))
check("info.contact.email", bool(info.get("contact", {}).get("email")))
check("openapi", bool(DOC.get("openapi")))
check("paths", bool(DOC.get("paths")))
check("servers apunta al origen", DOC.get("servers", [{}])[0].get("url", "").startswith("http"))
check("x-discovery.ownershipProofs",
      bool(DOC.get("x-discovery", {}).get("ownershipProofs")))

print()
print("== toda ruta de pago declara x-payment-info y 402 ==")
for path, price in main.PRICES.items():
    item = DOC["paths"].get(path)
    if item is None:
        check(f"{path} presente", False, "(no aparece en el documento)")
        continue
    op = item.get("post") or item.get("get")
    pi = (op or {}).get("x-payment-info", {})
    check(f"{path} x-payment-info", bool(pi))
    check(f"{path} protocolo x402",
          any("x402" in p for p in pi.get("protocols", [])))
    esperado = price.lstrip("$")
    check(f"{path} precio USD decimal {esperado}",
          pi.get("price", {}).get("amount") == esperado,
          f"(dice {pi.get('price', {}).get('amount')})")
    check(f"{path} modo fixed", pi.get("price", {}).get("mode") == "fixed")
    check(f"{path} moneda USD", pi.get("price", {}).get("currency") == "USD")
    check(f"{path} declara 402", "402" in (op or {}).get("responses", {}))
    if item.get("post"):
        cuerpo = op.get("requestBody", {}).get("content", {})
        check(f"{path} schema de entrada JSON",
              "schema" in cuerpo.get("application/json", {}))

print()
print("== el gateway fiat NO se anuncia ==")
check("sin rutas /fiat en el documento",
      not [p for p in DOC["paths"] if p.startswith("/fiat")])

print()
print("== coherencia documento <-> runtime (la regla de conversion) ==")
# El 402 real emite unidades atomicas; el documento, USD decimal. Deben ser
# el mismo dinero: 0.001 USD == 1000 unidades atomicas de USDC.
for path, price in main.PRICES.items():
    op = DOC["paths"][path].get("post") or DOC["paths"][path].get("get")
    doc_usd = op["x-payment-info"]["price"]["amount"]
    runtime_atomic = main._atomic(price)
    check(f"{path} {doc_usd} USD == {runtime_atomic} atomicas",
          int(round(float(doc_usd) * 1_000_000)) == int(runtime_atomic))

print()
print("== el well-known sigue vivo y coherente ==")
wk = client.get("/.well-known/x402").json()
check("well-known responde", wk.get("x402Version") == 1)
check("well-known lista los mismos recursos",
      len(wk.get("resources", [])) == len(main.PRICES),
      f"({len(wk.get('resources', []))} vs {len(main.PRICES)})")

print()
print("== /openapi.json se sirve por HTTP ==")
r = client.get("/openapi.json")
check("200 en /openapi.json", r.status_code == 200)
check("trae x-guidance por HTTP", bool(r.json().get("info", {}).get("x-guidance")))

print()
if fallos:
    print(f"FALLARON {len(fallos)}: {fallos}")
    sys.exit(1)
print("TODO OK")
