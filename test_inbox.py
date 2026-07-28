"""Pruebas del buzon agente-a-agente, incluidos los casos de abuso.

Un buzon abierto es una superficie de ataque: lo que mas se prueba aqui no es el
camino feliz, es que nadie pueda leer el correo de otro ni averiguar que buzones
existen.
"""
import os
import sys

os.environ.setdefault("PAY_TO", "")  # sin cobro: se prueba la logica
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import buzon_agentes  # noqa: E402
import main  # noqa: E402

cliente = TestClient(main.app)
fallos = []


def check(nombre, cond, detalle=""):
    if cond:
        print(f"  ok    {nombre}")
    else:
        print(f"  FALLA {nombre} {detalle}")
        fallos.append(nombre)


print("== crear buzon ==")
r = cliente.post("/inbox/create")
check("responde 200", r.status_code == 200, f"({r.status_code})")
b = r.json()
direccion, clave = b.get("buzon"), b.get("clave")
check("da direccion", bool(direccion) and direccion.startswith("agt_"))
check("da clave", bool(clave))
check("avisa de que la clave no se recupera", "no se" in b.get("aviso_clave", ""))
check("declara que el almacenamiento es efimero",
      "efímero" in b.get("almacenamiento", "") or "efimero" in b.get("almacenamiento", ""))
check("explica como te escriben", bool(b.get("como_te_escriben")))

print()
print("== la clave NO se guarda en claro ==")
interno = buzon_agentes._buzones.get(direccion, {})
check("solo se guarda el hash", "clave_hash" in interno and "clave" not in interno)
check("el hash no es la clave", interno.get("clave_hash") != clave)

print()
print("== enviar y recoger ==")
r = cliente.post("/inbox/send", json={
    "para": direccion, "mensaje": {"tarea": "revisar precio", "sku": 42},
    "de": "agente-comprador", "asunto": "consulta"})
check("envio responde 200", r.status_code == 200, f"({r.status_code})")
env = r.json()
check("confirma entrega", env.get("entregado") is True)
check("devuelve id del mensaje", bool(env.get("id_mensaje")))
check("dice cuantos hay pendientes", env.get("pendientes_en_destino") == 1)

r = cliente.post("/inbox/receive", json={"buzon": direccion, "clave": clave,
                                         "dejar_en_buzon": True})
check("recoger responde 200", r.status_code == 200, f"({r.status_code})")
rec = r.json()
check("llega 1 mensaje", rec.get("total_entregados") == 1)
if rec.get("mensajes"):
    m = rec["mensajes"][0]
    check("el contenido llega intacto", m["mensaje"] == {"tarea": "revisar precio", "sku": 42})
    check("se sabe quien escribio", m.get("de") == "agente-comprador")
    check("no se filtran campos internos",
          not any(k.startswith("_") for k in m))

print()
print("== 'dejar_en_buzon' de verdad no vacia ==")
r = cliente.post("/inbox/receive", json={"buzon": direccion, "clave": clave})
check("sigue estando al releer", r.json().get("total_entregados") == 1)
r = cliente.post("/inbox/receive", json={"buzon": direccion, "clave": clave})
check("y ahora si se vacio", r.json().get("total_entregados") == 0)

print()
print("== SEGURIDAD: nadie lee el correo de otro ==")
r = cliente.post("/inbox/receive", json={"buzon": direccion, "clave": "clave-mala"})
check("clave incorrecta -> 4xx", 400 <= r.status_code < 500, f"({r.status_code})")
error_clave_mala = r.text

r = cliente.post("/inbox/receive", json={"buzon": "agt_noexiste", "clave": "x"})
check("buzon inexistente -> 4xx", 400 <= r.status_code < 500, f"({r.status_code})")
check("MISMO error para clave mala y buzon inexistente (no se puede sondear "
      "que buzones existen)", r.text == error_clave_mala,
      f"\n        {error_clave_mala}\n        vs {r.text}")

print()
print("== limites y entradas malas ==")
r = cliente.post("/inbox/send", json={"para": "agt_noexiste", "mensaje": "hola"})
check("enviar a buzon inexistente -> 4xx", 400 <= r.status_code < 500)
r = cliente.post("/inbox/send", json={"para": direccion, "mensaje": ""})
check("mensaje vacio -> 4xx", 400 <= r.status_code < 500)
r = cliente.post("/inbox/send", json={"mensaje": "sin destino"})
check("sin destinatario -> 4xx", 400 <= r.status_code < 500)
r = cliente.post("/inbox/send", json={
    "para": direccion, "mensaje": "x" * (buzon_agentes.MAX_TAMANO_MENSAJE + 100)})
check("mensaje gigante -> 4xx", 400 <= r.status_code < 500, f"({r.status_code})")

print()
print("== el buzon se llena y lo dice, no revienta ==")
b2 = cliente.post("/inbox/create").json()
for i in range(buzon_agentes.MAX_MENSAJES_POR_BUZON):
    cliente.post("/inbox/send", json={"para": b2["buzon"], "mensaje": f"n{i}"})
r = cliente.post("/inbox/send", json={"para": b2["buzon"], "mensaje": "uno mas"})
check("al llenarse responde 4xx claro", 400 <= r.status_code < 500, f"({r.status_code})")
check("el error explica por que", "lleno" in r.text.lower())

print()
print("== caducidad ==")
b3 = cliente.post("/inbox/create").json()
cliente.post("/inbox/send", json={"para": b3["buzon"], "mensaje": "viejo"})
with buzon_agentes._candado:
    for m in buzon_agentes._buzones[b3["buzon"]]["mensajes"]:
        m["_recibido_en"] -= buzon_agentes.TTL_MENSAJE_SEG + 10
r = cliente.post("/inbox/receive", json={"buzon": b3["buzon"], "clave": b3["clave"]})
check("los mensajes caducados desaparecen", r.json().get("total_entregados") == 0)

print()
print("== estado en /health, sin filtrar nada ==")
h = cliente.get("/health").json()
check("health trae el estado del buzon", "inbox" in h)
if "inbox" in h:
    texto = str(h["inbox"])
    check("no expone direcciones", "agt_" not in texto)
    check("no expone claves", clave not in texto)
    check("cuenta buzones activos", isinstance(h["inbox"].get("buzones_activos"), int))

print()
print("== tarifas: solo se cobra enviar ==")
check("/inbox/send tiene precio", "/inbox/send" in main.PRICES)
check("/inbox/create es gratis", "/inbox/create" not in main.PRICES)
check("/inbox/receive es gratis", "/inbox/receive" not in main.PRICES)

print()
if fallos:
    print(f"FALLARON {len(fallos)}: {fallos}")
    sys.exit(1)
print("TODO OK")
