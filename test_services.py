"""Tester de los servicios del agent-data-toolkit. Uso: python test_services.py [base_url]"""
import json
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8402"

CASES = [
    # (endpoint, nombre_caso, payload, status_esperado, check)
    ("/repair", "feliz", {"broken": '{name: "Ana", tags: [a, b,]}'}, 200,
     lambda r: r["ok"] and r["repaired"]["name"] == "Ana"),
    ("/repair", "irreparable", {"broken": ""}, 200, lambda r: "ok" in r),
    ("/repair", "con schema", {"broken": "{n: 1}", "schema_": {"type": "object",
     "properties": {"n": {"type": "string"}}}}, 200, lambda r: r["schema_valid"] is False),
    ("/validate", "valido", {"data": {"a": 1}, "schema_": {"type": "object",
     "properties": {"a": {"type": "integer"}}}}, 200, lambda r: r["valid"] is True),
    ("/validate", "alias schema", {"data": {"a": 1}, "schema": {"type": "object",
     "properties": {"a": {"type": "integer"}}}}, 200, lambda r: r["valid"] is True),
    ("/validate", "invalido", {"data": {"a": "x"}, "schema_": {"type": "object",
     "properties": {"a": {"type": "integer"}}}}, 200,
     lambda r: r["valid"] is False and r["errors"]),
    ("/csv", "feliz", {"records": [{"a": 1, "b": 2}, {"a": 3, "c": 4}]}, 200,
     lambda r: r["rows"] == 2 and "a,b,c" in r["csv"]),
    ("/csv", "vacio", {"records": []}, 422, None),
    ("/markdown", "feliz", {"html": "<h2>T</h2><p><b>x</b></p><script>evil()</script>"}, 200,
     lambda r: "## T" in r["markdown"] and "evil" not in r["markdown"]),
    ("/markdown", "html roto", {"html": "<p>sin cerrar <b>negrita"}, 200,
     lambda r: "negrita" in r["markdown"]),
    ("/extract", "feliz (DeepSeek)", {"text": "Pedro vende 3 laptops usadas a 250 USD cada una, entrega en Bogota",
     "schema_": {"type": "object", "properties": {"vendedor": {"type": "string"},
     "cantidad": {"type": "integer"}, "precio_unitario_usd": {"type": "number"},
     "ciudad": {"type": "string"}}, "required": ["vendedor", "cantidad",
     "precio_unitario_usd", "ciudad"]}}, 200,
     lambda r: r["ok"] and r["data"]["cantidad"] == 3 and r.get("schema_valid") is True),
    ("/repair", "payload gigante", {"broken": "{x: 1}" + " " * 250000}, 413, None),
    ("/promote", "feliz (DeepSeek)", {"name": "demo-csv-api", "url": "https://example.com",
     "what_it_does": "convierte JSON a CSV por $0.001 la llamada", "language": "es"}, 200,
     lambda r: r["ok"] and all(k in r["kit"] for k in
     ("tagline", "bazaar_description", "tweet", "readme_blurb", "one_liner"))),
    ("/promote", "faltan campos", {"name": "x"}, 422, None),
    ("GET /", "escaparate", None, 200,
     lambda r: "/promote" in r["endpoints"] and r["tagline"]),
    ("GET /ads", "kit propio", None, 200,
     lambda r: r["ok"] and "#x402" in r["kit"]["tweet"]),
    ("GET /.well-known/x402", "manifiesto discovery", None, 200,
     lambda r: r["serviceName"] == "agent-data-toolkit" and isinstance(r["resources"], list)),
]

results = []
with httpx.Client(timeout=60) as c:
    for path, name, payload, want_status, check in CASES:
        t0 = time.perf_counter()
        try:
            if path.startswith("GET "):
                resp = c.get(BASE + path[4:])
            else:
                resp = c.post(BASE + path, json=payload)
            ms = (time.perf_counter() - t0) * 1000
            ok = resp.status_code == want_status
            detail = ""
            if ok and check is not None:
                try:
                    ok = bool(check(resp.json()))
                except Exception as e:
                    ok, detail = False, f"check: {e}"
            if not ok and not detail:
                detail = f"status {resp.status_code}, body: {resp.text[:120]}"
        except Exception as e:
            ms, ok, detail = (time.perf_counter() - t0) * 1000, False, str(e)
        results.append((path, name, ok, ms, detail))

fallos = [r for r in results if not r[2]]
print(f"{'ENDPOINT':<12}{'CASO':<22}{'RESULTADO':<10}{'LATENCIA':>10}")
for path, name, ok, ms, detail in results:
    print(f"{path:<12}{name:<22}{'PASA' if ok else 'FALLA':<10}{ms:>8.0f}ms  {detail}")
print(f"\n{len(results) - len(fallos)}/{len(results)} pruebas pasaron")
sys.exit(1 if fallos else 0)
