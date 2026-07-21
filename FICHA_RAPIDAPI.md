# El Cambista — publicar el agent-data-toolkit en RapidAPI

Doble caja sobre el mismo backend: **los agentes pagan USDC por x402, las
empresas pagan suscripción en USD por RapidAPI**. El marketplace pone la
tarjeta, las cuotas y los clientes; nosotros solo servimos las llamadas.

## Estado

- Código: **listo y probado** — rutas `/fiat/repair|validate|csv|markdown|extract|ask`.
- Gateway: **DORMIDO** (503) hasta pegar `RAPIDAPI_PROXY_SECRET` en Render.
- Chivato: `GET /health` → `"fiat_gateway": true|false` y bloque `"fiat_guard"`.

## Qué se publica y qué no

**Se publican los 6 endpoints del motor de datos**, elegidos por demanda real y
coste marginal bajo:

| Ruta | Qué hace | Coste variable |
|------|----------|----------------|
| `POST /repair` | Repara JSON roto (comas colgantes, comillas simples, artefactos de LLM). Motor `json-repair`, escala a DeepSeek solo si la reparación mecánica queda dudosa. **Es el producto estrella.** | ~$0 |
| `POST /validate` | Valida contra JSON Schema y devuelve la lista de violaciones con su ruta. | $0 |
| `POST /csv` | Registros JSON → CSV con cabecera automática. | $0 |
| `POST /markdown` | HTML → Markdown limpio (elimina `script`/`style`/`noscript`). | $0 |
| `POST /extract` | Texto libre → JSON estructurado guiado por tu esquema. | ~$0.0005–0.002 |
| `POST /ask` | Trabajador LLM de propósito general, salida JSON estricta. | ~$0.0005–0.002 |

**Quedan fuera a propósito** (siguen vendiéndose por x402, sin cambio):
`/edge`, `/resolve`, `/features` y `/promote`. Son de nicho cripto o de
marketing, lentos (hasta ~12 s) y caros en DeepSeek: un cliente que solo usara
`/edge` en un plan de $9/mes daría pérdidas. Son la base de una segunda API
premium ("Crypto Edge") cuando haya datos de consumo real.

Si se llaman por fiat responden **404**, no 402: no existen en ese router.

## Blindaje (ya implementado, `fiat_guard.py`)

El secreto de RapidAPI es la única barrera de `/fiat/*` — los middlewares x402
filtran por ruta exacta, así que estas rutas no pasan por cobro on-chain. Si el
secreto se filtrara, un tercero gastaría DeepSeek a nuestra costa. Por eso:

| Env var | Default | Efecto |
|---------|---------|--------|
| `FIAT_DAILY_LLM_CAP_USD` | `2.00` | Tope diario de coste del modelo (solo cuentan `/extract` y `/ask`). Al superarlo → **429**. |
| `FIAT_RATE_PER_MIN` | `120` | Peticiones fiat por minuto (ventana deslizante). Al superarlo → **429**. |
| `FIAT_LLM_COST_PER_CALL` | `0.002` | Coste estimado por llamada LLM, deliberadamente pesimista. |

Un 429 no se le cobra al cliente en RapidAPI. El estado vive en memoria y se
reinicia con cada despliegue: es un cortafuegos contra abuso, **no
contabilidad** — la facturación real la lleva RapidAPI.

## Pasos de publicación (una sola vez, ~20 min)

1. Crear cuenta en <https://rapidapi.com> y entrar al **Provider Dashboard**
   (rapidapi.com/provider) → **Add New API**.
   - Name: `Agent Data Toolkit — JSON repair, validate & extract`
   - Category: `Data`
   - **Base URL: `https://agent-data-toolkit.onrender.com/fiat`**
2. En **Settings → Configure**, copiar el `X-RapidAPI-Proxy-Secret` que RapidAPI
   genera para esta API.
3. En Render (dashboard.render.com → agent-data-toolkit → Environment) crear
   `RAPIDAPI_PROXY_SECRET` = ese secreto. Al guardar, Render redespliega y el
   gateway despierta solo.
   **Verificar:** `GET /health` → `"fiat_gateway": true`.
4. Definir los 6 endpoints (POST, JSON) con los ejemplos de abajo.
5. Crear los planes (pestaña **Plans & Pricing**).
6. Publicar como **Public** y pegar los textos de marketing.

## Endpoints (payload de ejemplo para la consola de RapidAPI)

| Ruta | Body de ejemplo |
|------|-----------------|
| `POST /repair` | `{"broken": "{name: \"Ana\", tags: [a, b,]}"}` |
| `POST /validate` | `{"data": {"a": 1}, "schema": {"type": "object", "properties": {"a": {"type": "integer"}}}}` |
| `POST /csv` | `{"records": [{"a": 1, "b": 2}, {"a": 3}]}` |
| `POST /markdown` | `{"html": "<h1>Hello</h1><p>World</p>"}` |
| `POST /extract` | `{"text": "Pedro sells 3 laptops at 250 USD in Bogota", "schema": {"type": "object", "properties": {"seller": {"type": "string"}, "qty": {"type": "integer"}}}}` |
| `POST /ask` | `{"prompt": "Summarize in JSON: ..."}` |

Todos aceptan `schema` como alias de `schema_`.

## Análisis de costos (hecho antes de fijar precios)

Suscripción fiat ≠ micropago x402. Aquí hay dos costes que en x402 no existen y
que se comen el margen si se ignoran:

**1. Comisión del marketplace.** RapidAPI (hoy Nokia API Hub) se queda el
**25%** de cada pago, **más 2.9% + $0.30** de procesamiento por transacción.
De un plan de $9 no llegan $9: llegan **$6.19**.

**2. Coste real del modelo.** Medido contra la API el 21-jul-2026 con
`deepseek-v4-flash` ($0.14/M input cache-miss, $0.28/M output):

| Escenario | Coste medido |
|---|---|
| Llamada típica a `/ask` o `/extract` | **$0.00004** |
| Misma llamada con thinking activo | **$0.00004** (razona en 74-125 tokens) |
| Peor caso: entrada al tope de `ASK_MAX_CHARS` (~4.000 tok) + 1.500 salida | **~$0.001** |

Es ~20× más barato de lo que sugería la documentación. El default de
`FIAT_LLM_COST_PER_CALL` se deja en `$0.001` (el peor caso) a propósito: el
cortafuegos debe frenar de más, no de menos.

Con eso, la tabla de precios anterior de esta ficha **perdía dinero**:
PRO a $9 con 10.000 req dejaba $6.19 netos contra hasta $10 de coste →
**−$3.81 por cliente**. Corregido abajo.

Un tercer criterio: el precio efectivo por llamada en fiat **no debe bajar del
precio x402** ($0.001–$0.005), o el canal de suscripción canibaliza el de
agentes.

## Planes

| Plan | Precio | Cuota | $/req efectivo | Neto tras comisión | Peor caso LLM | Margen mínimo |
|------|--------|-------|----------------|--------------------|---------------|---------------|
| BASIC | $0 | 25 req/mes (hard limit) | — | $0 | ~$0.03 | anzuelo |
| STARTER | $29/mes | 2.500 req | $0.0116 | $20.61 | $2.50 | **88%** |
| PRO | $79/mes | 15.000 req | $0.0053 | $56.66 | $15.00 | **74%** |
| ULTRA | $199/mes | 60.000 req | $0.0033 | $143.18 | $60.00 | **58%** |

Llamada extra fuera de cuota: **$0.006** (STARTER/PRO), **$0.004** (ULTRA).

El "peor caso" supone que el cliente gasta el 100% de su cuota en `/ask` con la
entrada al máximo — escenario extremo que además está topado por
`FIAT_DAILY_LLM_CAP_USD`. El mix real es mucho mejor: cuatro de los seis
endpoints (`repair`, `validate`, `csv`, `markdown`) tienen coste marginal
**cero**, así que un cliente típico deja margen por encima del 90%.

Infra: Render Starter ($7/mes) se cubre con el primer cliente STARTER.

Con el coste típico medido ($0.00004/llamada), un cliente PRO que agote sus
15.000 llamadas cuesta en realidad **$0.60**, no $15 — el margen efectivo ronda
el **99%**. La fila "peor caso" existe para fijar el suelo, no la expectativa.

## Modelo (migración 21-jul-2026)

El alias `deepseek-chat` **se deprecó el 24-jul-2026 15:59 UTC**. El servicio
usa ahora `deepseek-v4-flash` (env `DEEPSEEK_MODEL`), que además es más barato.

Razonamiento activado (`DEEPSEEK_THINKING=1`) en las tareas analíticas:
`extract`, `market_parse` y las cuatro del consejo de `/edge`. Verificado contra
la API real: **thinking es compatible con `response_format: json_object`**.
Queda fuera de `promote` (es redacción creativa y thinking anula la temperatura
0.7 que le da la chispa) y de `repair` (no lo necesita).

⚠️ Trampa aprendida: los tokens de razonamiento **se descuentan del mismo
`max_tokens` que la respuesta**. Sin colchón, el modelo agota el presupuesto
pensando y devuelve JSON truncado — así se cayó el quórum de `/edge` en la
primera prueba. Cubierto por `DEEPSEEK_THINKING_BUDGET` (default 1200) en
`brain.py`. Efecto secundario aceptado: `/edge` pasó de ~11 s a ~30 s; no
afecta a RapidAPI porque `/edge` no se publica ahí.

## Textos de marketing (copiar y pegar)

**Short description:**
> Fix broken JSON, validate against JSON Schema, convert JSON→CSV and
> HTML→Markdown, extract structured data from free text, and run an LLM worker
> — 6 data endpoints built for AI pipelines. No setup, no GPU, JSON in, JSON out.

**Long description:**
> **Agent Data Toolkit** is the data-janitor API your LLM pipeline is missing.
>
> - **/repair** — fixes malformed JSON: trailing commas, single quotes, unquoted
>   keys, markdown fences and the rest of the debris LLMs emit. Mechanical
>   repair first, model-assisted only when needed.
> - **/validate** — JSON Schema validation with a precise list of violations.
> - **/csv** — JSON records → CSV with automatic header detection.
> - **/markdown** — raw HTML → clean Markdown, scripts and styles stripped.
>   Ideal for RAG ingestion.
> - **/extract** — structured JSON from free text, guided by your own schema.
> - **/ask** — general-purpose LLM answers in strict JSON.
>
> Built for LLM pipelines, scrapers, ETL jobs and AI agents. The same backend is
> also sold to autonomous agents over the x402 protocol (USDC on Base), live in
> production since July 2026.

**Tags:** `json`, `json-repair`, `llm`, `data-cleaning`, `csv`, `markdown`,
`extraction`, `ai-agents`, `etl`, `json-schema`

## Notas de operación

- **Render free tier duerme a los 15 min.** Lo mantiene despierto el auto-ping
  interno (`SELF_PING_MINUTES=14`), pero una primera llamada tras inactividad
  puede tardar decenas de segundos y en RapidAPI eso se percibe como latencia
  alta. Si entran ventas, pasar a Render Starter ($7/mes) deja de ser opcional.
- **Probar antes de publicar:** `TEST_FIAT_SECRET=... python test_services.py <url>`
  cubre los 6 endpoints fiat, el 401 con secreto malo, los 404 de lo excluido y
  el 429 del rate limit.
- **Publicar no es vender.** El listado es condición necesaria, no suficiente —
  misma lección que dejó el Bazaar. La diferencia real es que aquí hay
  compradores buscando activamente, en vez de esperar a que un agente descubra
  el recurso.
