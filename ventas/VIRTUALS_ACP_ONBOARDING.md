# Runbook — listar el agent-data-toolkit como Seller en Virtuals ACP (sandbox)

Virtuals ACP es la tienda x402 con **volumen real entre agentes** (~$1M/mes). A diferencia
del Bazaar de Coinbase (donde ya estás y llevas $0 externos), aquí los agentes compran de
verdad, pero exige **registro + whitelist + un agente vendedor corriendo** (no es un simple
POST). Estos son los pasos reales, sacados del SDK oficial `@virtuals-protocol/acp-node-v2`
(README de `Virtual-Protocol/acp-node`).

> ⚠️ Honestidad: esto necesita TU wallet y aprobaciones en el navegador. Yo no puedo tocar
> tu clave (bien que así sea). Te dejo los pasos para que los corras tú; yo preparo el código
> del agente vendedor cuando digas.

## Qué necesitas antes
- Node.js (ya lo tienes).
- Una wallet EVM en Base para el agente (puede ser una nueva, dedicada — recomendado, NO
  reuses la del bot bittensor ni la de cobro).
- La API ya viva: `https://agent-data-toolkit.onrender.com` (lista).

## Pasos

1. **Registrar el agente en el Service Registry** (web, con tu wallet):
   → https://app.virtuals.io/acp/join
   Sin este registro, ningún agente puede descubrirte. Elige rol **Provider/Seller** (o
   hybrid). Define el "offering": envuelve tus endpoints x402 existentes (repair, validate,
   extract…) como un servicio vendible.

2. **Crear smart wallet + whitelist de tu wallet de desarrollo + fondear el agente.**
   La guía paso a paso está en:
   → https://whitepaper.virtuals.io/acp-product-resources/acp-dev-onboarding-guide
   (sección "testing flow": register agent → create smart wallet → whitelist dev wallet →
   fund agent → run buyer/seller).

3. **Instalar el SDK y correr el agente vendedor** (esto lo programo yo):
   ```
   npm install @virtuals-protocol/acp-node-v2
   ```
   El agente vendedor usa `new AcpClient({ acpContractClient: await AcpContractClientV2.build(...) })`,
   escucha solicitudes de compra y responde con `job.accept()` / `job.deliver()` llamando por
   detrás a tu API del toolkit. Patrón: igual que un webhook, pero de la red ACP.

4. **Sandbox → graduación:** al onboardear, el agente aparece en la pestaña **Sandbox**; un
   buyer de prueba le manda trabajos. Tras **10 transacciones exitosas** el equipo lo marca
   **graduated** y ya es visible/comprable por agentes reales. Todo el sandbox es sin dinero
   real.

## Realidad honesta (para calibrar el esfuerzo)
- Virtuals ACP **sí** tiene compradores reales (a diferencia del Bazaar), así que vale la
  pena probar — es la mejor apuesta de distribución que encontramos.
- PERO exige mantener un **agente vendedor corriendo 24/7** (como el bot bittensor) y pasar
  la graduación. Es más trabajo que un listing pasivo.
- Mi recomendación: hacerlo **después** de decidir si el producto que vendemos es el toolkit
  (datos genéricos, mucha competencia) o algo más diferenciado (Fase B con tu dato único de
  subnets bittensor). Registrar en ACP algo indiferenciado repetiría el resultado del Bazaar.

## Estado
- [ ] Paso 1: registro en app.virtuals.io/acp/join (TÚ)
- [ ] Paso 2: smart wallet + whitelist + fondeo (TÚ)
- [ ] Paso 3: agente vendedor Node (YO lo programo cuando confirmes)
- [ ] Paso 4: 10 tx de sandbox → graduar (TÚ + YO)
