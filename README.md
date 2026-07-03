# agent-data-toolkit (x402 + DeepSeek)

**Autor y creador original: Nicolás Ángel Rojas Yáñez © 2026.**
Distribuido bajo licencia MIT: todo uso o copia de este software debe conservar
el aviso de copyright del autor (ver [LICENSE](LICENSE)). Eliminar la atribución
constituye una violación de la licencia.

API de pago por llamada para agentes de IA. Cobra USDC en Base vía protocolo x402.
Categoría "data": la de mayor demanda del ecosistema x402 (~31% de la actividad).

## Endpoints
- `POST /repair` ($0.001) — repara JSON malformado. Body: `{"broken": "...", "schema_": {...}?}`
- `POST /validate` ($0.001) — valida datos contra JSON Schema. Body: `{"data": ..., "schema_": {...}}`
- `POST /csv` ($0.001) — lista de registros JSON → CSV. Body: `{"records": [{...}], "delimiter": ","?}`
- `POST /markdown` ($0.002) — HTML → Markdown limpio (sin scripts). Body: `{"html": "..."}`
- `POST /extract` ($0.005) — extrae JSON estructurado de texto libre usando DeepSeek. Body: `{"text": "...", "schema_": {...}, "instructions": "..."?}`
- `POST /promote` ($0.005) — publicidad agéntica: DeepSeek redacta el kit publicitario de TU servicio x402 (tagline, descripción optimizada para índices de discovery, tweet con #x402, blurb de README). Body: `{"name": "...", "url": "...", "what_it_does": "...", "pricing": "..."?, "audience": "..."?, "language": "es"?}`
- `GET /` y `GET /ads` — gratis, escaparate del servicio con autopromoción regenerada por DeepSeek cada `PROMO_INTERVAL_HOURS` horas (24 por defecto): el propio `/promote` aplicado a este servicio, como demo de su calidad.
- `GET /health` — gratis, estado del servicio.

Límite de entrada: 200k caracteres (HTTP 413 si se supera). Llamadas a DeepSeek con timeout 60s y 2 reintentos.

## Configuración (variables de entorno)
| Variable | Valor |
|---|---|
| `PAY_TO` | tu wallet en Base (0x...) — sin ella corre en modo gratis |
| `DEEPSEEK_API_KEY` | clave de platform.deepseek.com |
| `NETWORK` | `base` (producción) o `base-sepolia` (pruebas) |
| `PRICE_REPAIR` / `PRICE_EXTRACT` / `PRICE_PROMOTE` | precios, ej. `$0.001` |
| `PROMO_INTERVAL_HOURS` | cada cuántas horas DeepSeek regenera la autopromoción (24 por defecto; ~1 llamada corta al día, coste despreciable) |
| `PUBLIC_URL` | URL pública del servicio para la autopromoción |

## Cómo crear la wallet en Base
1. Instala **Coinbase Wallet** (o MetaMask y añade la red Base, chainid 8453).
2. Copia tu dirección `0x...` → esa es `PAY_TO`. Recibir USDC no cuesta gas.
3. Los pagos se liquidan solos vía el facilitador de Coinbase CDP; solo pagarás gas cuando muevas fondos fuera.

## Ejecutar local
```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
set PAY_TO=0xTU_WALLET
set DEEPSEEK_API_KEY=sk-...
.venv\Scripts\uvicorn main:app --host 0.0.0.0 --port 8402
```

## Desplegar (Render/Fly.io/VPS de 1 vCPU y 512MB basta)
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Define las variables de entorno de arriba con `NETWORK=base`.
- Prueba primero en `base-sepolia` con USDC de faucet.

## Publicar en el x402 Bazaar (Coinbase)
Al usar el facilitador por defecto de Coinbase, los recursos con `discoverable: true`
(ya activado) quedan indexados en el catálogo del Bazaar automáticamente cuando
reciben su primer pago en mainnet. Docs: https://docs.cdp.coinbase.com/x402/welcome
