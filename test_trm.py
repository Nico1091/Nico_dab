"""Pruebas de los endpoints de la TRM contra la fuente oficial real.

Toca red a proposito: el valor de este servicio es que el dato sea el de verdad.
Una prueba con datos simulados no diria nada sobre lo unico que importa aqui.
"""
import os
import sys

os.environ.setdefault("PAY_TO", "")  # sin cobro: se prueba la logica
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main  # noqa: E402

cliente = TestClient(main.app)
fallos = []


def check(nombre, cond, detalle=""):
    if cond:
        print(f"  ok    {nombre}")
    else:
        print(f"  FALLA {nombre} {detalle}")
        fallos.append(nombre)


print("== /trm de hoy ==")
r = cliente.post("/trm", json={})
check("responde 200", r.status_code == 200, f"({r.status_code})")
if r.status_code == 200:
    d = r.json()
    tasa = d.get("trm_cop_por_usd")
    check("trae un valor numerico", isinstance(tasa, (int, float)))
    # Rango de cordura: la TRM historica nunca ha estado fuera de esto.
    check("valor en rango plausible", 500 < (tasa or 0) < 20000, f"({tasa})")
    check("dice su fecha de vigencia", bool(d.get("vigencia_desde")))
    check("cita la fuente oficial", "Superintendencia" in d.get("fuente", ""))
    check("marca cuando se consulto", bool(d.get("consultado_en")))

print()
print("== /trm de una fecha pasada ==")
r = cliente.post("/trm", json={"date": "2024-01-15"})
check("responde 200", r.status_code == 200, f"({r.status_code})")
if r.status_code == 200:
    d = r.json()
    check("devuelve la fecha pedida", d.get("fecha") == "2024-01-15")
    check("valor historico plausible", 3000 < d.get("trm_cop_por_usd", 0) < 5500,
          f"({d.get('trm_cop_por_usd')})")

print()
print("== /trm/convert ==")
r = cliente.post("/trm/convert", json={"amount": 100, "from": "USDC"})
check("responde 200", r.status_code == 200, f"({r.status_code})")
if r.status_code == 200:
    d = r.json()
    esperado = round(100 * d["trm_aplicada"], 2)
    check("la conversion cuadra con la tasa aplicada",
          abs(d["monto_convertido"] - esperado) < 0.01)
    check("destino es COP", d.get("moneda_destino") == "COP")
    check("declara que USDC va a la par con USD",
          d.get("usdc_tratado_como_usd_a_la_par") is True)

r = cliente.post("/trm/convert", json={"amount": 1000000, "from": "COP"})
check("COP -> USD responde 200", r.status_code == 200)
if r.status_code == 200:
    d = r.json()
    check("COP -> USD invierte la tasa", d.get("moneda_destino") == "USD")
    check("resultado coherente",
          abs(d["monto_convertido"] * d["trm_aplicada"] - 1000000) < 1)

print()
print("== /trm/series ==")
r = cliente.post("/trm/series", json={"from": "2026-01-01", "limit": 50})
check("responde 200", r.status_code == 200, f"({r.status_code})")
if r.status_code == 200:
    d = r.json()
    check("devuelve puntos", len(d.get("puntos", [])) > 0)
    check("respeta el limite", len(d.get("puntos", [])) <= 50)
    check("puntos ordenados por fecha",
          [p["fecha"] for p in d["puntos"]] == sorted(p["fecha"] for p in d["puntos"]))
    res = d.get("resumen", {})
    check("resumen con variacion", "variacion_cop" in res and "variacion_pct" in res)
    check("minima <= promedio <= maxima",
          res.get("minima", 0) <= res.get("promedio", 0) <= res.get("maxima", 0))

print()
print("== entradas malas: 4xx y sin cobro ==")
casos = [
    ("fecha con formato invalido", "/trm", {"date": "15-01-2024"}),
    ("moneda no soportada", "/trm/convert", {"amount": 1, "from": "EUR"}),
    ("serie sin fecha inicial", "/trm/series", {}),
    ("serie con rango invertido", "/trm/series",
     {"from": "2026-06-01", "to": "2026-01-01"}),
]
for nombre, ruta, cuerpo in casos:
    r = cliente.post(ruta, json=cuerpo)
    check(f"{nombre} -> 4xx", 400 <= r.status_code < 500, f"({r.status_code})")

print()
print("== los tres endpoints estan tarifados y descritos ==")
for ruta in ("/trm", "/trm/convert", "/trm/series"):
    check(f"{ruta} tiene precio", ruta in main.PRICES)
    check(f"{ruta} tiene descripcion", bool(main.DESCRIPTIONS.get(ruta)))

print()
if fallos:
    print(f"FALLARON {len(fallos)}: {fallos}")
    sys.exit(1)
print("TODO OK")
