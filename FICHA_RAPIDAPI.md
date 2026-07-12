# El Cambista — publicar el agent-data-toolkit en RapidAPI

Los 7 servicios del toolkit ya existen bajo `/fiat/*`, autenticados con el header
`X-RapidAPI-Proxy-Secret`. RapidAPI pone la tarjeta de crédito, las cuotas y los
clientes; nosotros solo servimos las llamadas. **Doble caja sobre el mismo código:**
los agentes pagan USDC vía x402, los humanos pagan suscripción vía RapidAPI.

## Estado

- Código: DESPLEGADO (rutas `/fiat/repair|validate|csv|markdown|extract|promote|ask`).
- Gateway: **DORMIDO** (responde 503) hasta pegar `RAPIDAPI_PROXY_SECRET` en Render.
- Chivato: `GET /health` → `"fiat_gateway": true|false`.

## Pasos de publicación (una sola vez, ~20 min)

1. Crear cuenta en https://rapidapi.com y entrar al **Provider Dashboard**
   (rapidapi.com/provider) → **Add New API**.
   - Name: `Agent Data Toolkit — JSON repair, extract & LLM ask`
   - Category: `Data` · Base URL: `https://agent-data-toolkit.onrender.com/fiat`
2. En **Settings → Configure** copiar el **X-RapidAPI-Proxy-Secret** que RapidAPI
   genera para esta API.
3. En Render (dashboard.render.com → agent-data-toolkit → Environment) crear
   `RAPIDAPI_PROXY_SECRET` = ese secreto. Al guardar, Render redespliega y el
   gateway despierta solo. Verificar: `/health` → `"fiat_gateway": true`.
4. Definir los 7 endpoints (POST, JSON) copiando los ejemplos de abajo.
5. Crear los planes (pestaña **Plans & Pricing**):
   | Plan | Precio | Cuota mensual | Extra |
   |------|--------|---------------|-------|
   | BASIC | $0 | 25 req (hard limit) | anzuelo |
   | PRO | $9/mes | 5.000 req | $0.005/req extra |
   | ULTRA | $29/mes | 50.000 req | $0.002/req extra |
   | MEGA | $99/mes | 250.000 req | $0.001/req extra |
6. Publicar como **Public** y pegar los textos de marketing de abajo.

## Endpoints (payload de ejemplo para la consola de RapidAPI)

| Ruta | Ejemplo de body |
|------|-----------------|
| `POST /repair` | `{"broken": "{name: \"Ana\", tags: [a, b,]}"}` |
| `POST /validate` | `{"data": {"a": 1}, "schema": {"type": "object", "properties": {"a": {"type": "integer"}}}}` |
| `POST /csv` | `{"records": [{"a": 1, "b": 2}, {"a": 3}]}` |
| `POST /markdown` | `{"html": "<h1>Hello</h1><p>World</p>"}` |
| `POST /extract` | `{"text": "Pedro sells 3 laptops at 250 USD in Bogota", "schema": {"type": "object", "properties": {"seller": {"type": "string"}, "qty": {"type": "integer"}}}}` |
| `POST /promote` | `{"name": "my-api", "url": "https://example.com", "what_it_does": "converts JSON to CSV"}` |
| `POST /ask` | `{"prompt": "Summarize: ..."}` |

## Textos de marketing (copiar y pegar)

**Short description:**
> Fix broken JSON, validate schemas, convert JSON↔CSV, HTML→Markdown, extract
> structured data from free text and ask an LLM worker — 7 data endpoints built
> for AI pipelines. No setup, no GPU, answers in JSON.

**Long description:**
> **Agent Data Toolkit** is the data-janitor API your pipeline is missing:
> - **/repair** — fixes malformed JSON (LLM output artifacts, trailing commas, single quotes)
> - **/validate** — JSON Schema validation with a detailed violation list
> - **/csv** — JSON records → CSV with automatic header detection
> - **/markdown** — raw HTML → clean Markdown (scripts stripped)
> - **/extract** — structured JSON from free text, guided by your schema (LLM worker)
> - **/promote** — truthful marketing kit for your own API (LLM copywriter)
> - **/ask** — general-purpose LLM answers in strict JSON
>
> Built for LLM pipelines, scrapers, ETL jobs and AI agents. Also sold to
> autonomous agents via the x402 protocol (USDC on Base) — same battle-tested
> backend, live since July 2026.

**Tags:** `json`, `json-repair`, `llm`, `data-cleaning`, `csv`, `markdown`,
`extraction`, `ai-agents`, `etl`

## Números (por si preguntan los planes)

- Coste variable: solo `/extract`, `/promote` y `/ask` llaman a DeepSeek
  (~$0.0005–0.002/llamada); el resto es cómputo puro (~$0).
- Peor caso PRO: 5.000 llamadas × $0.002 = $10 de DeepSeek vs $9 de ingreso →
  en la práctica el mix real (repair/validate/csv dominan) deja margen ≥70%.
  Si algún cliente abusara solo de /ask, subir el precio del plan o bajar
  `ASK_MAX_CHARS` (env var, default 16000).
- Render free tier aguanta este tráfico; si un plan MEGA se vende, pasar a
  Render Starter ($7/mes) con parte del ingreso.
