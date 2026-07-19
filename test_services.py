"""Tester de los servicios del agent-data-toolkit. Uso: python test_services.py [base_url]"""
import json
import os
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
    ("/ask", "feliz (DeepSeek)", {"prompt": "¿Cuál es la capital de Colombia? Responde en una sola palabra."}, 200,
     lambda r: r["ok"] and "bogot" in json.dumps(r["answer"], ensure_ascii=False).lower()),
    ("/ask", "payload gigante", {"prompt": "x" * 20000}, 413, None),
    ("/features", "BTC 1h (red)", {"symbol": "btc"}, 200,
     lambda r: r["ok"] and r["symbol"] == "BTCUSDT" and r["price"] > 0
     and 0 <= r["rsi14"] <= 100 and r["source"]),
    ("/features", "alias bittensor 1d", {"symbol": "bittensor", "interval": "1d"}, 200,
     lambda r: r["ok"] and r["symbol"] == "TAOUSDT" and r["interval"] == "1d"),
    ("/features", "simbolo basura", {"symbol": "NOEXISTEXYZ"}, 422, None),
    ("/features", "interval invalido", {"symbol": "BTC", "interval": "3m"}, 422, None),
    ("/resolve", "resuelta conocida (DeepSeek+red)",
     {"question": "Did Bitcoin close above $1,000 on 2026-07-01 (UTC)?"}, 200,
     lambda r: r["ok"] and r["outcome"] == "YES" and r["observed_close"] > 1000
     and r["symbol"] == "BTCUSDT" and r["source"] and r["rule"]),
    ("/resolve", "fecha futura", {"question": "Did BTC close above $1 on 2099-01-01?"}, 422, None),
    ("/resolve", "no soportada (deporte)", {"question": "Will the Lakers win tonight?"}, 422, None),
    ("/edge", "consejo BTC (DeepSeek+red, lento)",
     {"question": "Will Bitcoin be above $50,000 in 3 days?"}, 200,
     lambda r: r["ok"] and 0 <= r["probability"] <= 100 and 0 <= r["confidence"] <= 100
     and len(r["votes"]) == 3 and r["direction"] in ("YES", "NO", "TOSS_UP")
     and r["key_reasons"] and r["disclaimer"] and r["features"]["interval_1d"]["price"] > 0),
    ("/edge", "pregunta no cripto", {"question": "Will it rain in Bogotá tomorrow?"}, 422, None),
    ("/edge", "pregunta vacia", {"question": "   "}, 422, None),
    ("FIAT /fiat/repair", "fiat feliz", {"broken": "{a: 1,}"}, 200,
     lambda r: r["ok"] and r["repaired"]["a"] == 1),
    ("FIATBAD /fiat/repair", "fiat secreto malo", {"broken": "{}"}, 401, None),
    ("GET /catalog", "catalogo enrutable", None, 200,
     lambda r: len(r["routable"]) >= 6 and r["not_routable_price_above_cap"]),
    ("/route", "dry query json (router)", {"query": "repair my broken json string",
     "dry_run": True}, 200,
     lambda r: r["dry_run"] and r["would_route_to"].endswith("/repair")),
    ("/route", "resource desconocido", {"resource": "https://example.com/x",
     "dry_run": True}, 400, None),
    ("/route", "sin query ni resource", {}, 400, None),
    ("GET /", "escaparate", None, 200,
     lambda r: "/promote" in r["endpoints"] and r["tagline"]),
    ("GET /ads", "kit propio", None, 200,
     lambda r: r["ok"] and "#x402" in r["kit"]["tweet"]),
    ("GET /.well-known/x402", "manifiesto discovery", None, 200,
     lambda r: r["serviceName"] == "agent-data-toolkit" and isinstance(r["resources"], list)),
]

results = []
with httpx.Client(timeout=120) as c:
    for path, name, payload, want_status, check in CASES:
        t0 = time.perf_counter()
        try:
            if path.startswith("GET "):
                resp = c.get(BASE + path[4:])
            elif path.startswith("FIATBAD "):
                resp = c.post(BASE + path[8:], json=payload,
                              headers={"X-RapidAPI-Proxy-Secret": "incorrecto"})
            elif path.startswith("FIAT "):
                resp = c.post(BASE + path[5:], json=payload,
                              headers={"X-RapidAPI-Proxy-Secret":
                                       os.environ.get("TEST_FIAT_SECRET", "testsecret")})
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
