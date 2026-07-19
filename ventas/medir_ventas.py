#!/usr/bin/env python3
# =============================================================================
# MEDIR VENTAS — cuántas de las entradas de USDC a la wallet de cobro son de
# CLIENTES EXTERNOS y cuántas son tus propias auto-compras de prueba.
# =============================================================================
# Es el termómetro honesto del negocio x402: estar listado en las tiendas (aunque
# sean las de más confianza) NO garantiza ventas. Este script te lo dice con datos
# on-chain, sin claves ni dependencias (solo la librería estándar de Python).
#
# Uso:  python medir_ventas.py
#       python medir_ventas.py 0xTuWallet 0xTuWalletPagadora1 0xPagadora2 ...
#
# Por defecto mide la wallet de cobro de los servicios x402 y marca la Cuenta 3
# (tu pagadora de pruebas) como "propia".
# =============================================================================
import json
import sys
import urllib.request
from datetime import datetime, timezone

USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
BLOCKSCOUT = "https://base.blockscout.com/api"

# Defaults del proyecto (cámbialos por argv si quieres medir otra wallet).
WALLET_COBRO = "0x92Cb873c2d114d6Aa847EB110F721DA22B5220F7"
WALLETS_PROPIAS = {"0x77747a236038e6f2c2eaa3bd11f52945596d3972"}  # Cuenta 3 (auto-compras)


def entrantes_usdc(wallet: str) -> list[dict]:
    url = (f"{BLOCKSCOUT}?module=account&action=tokentx"
           f"&contractaddress={USDC_BASE}&address={wallet}&sort=asc")
    with urllib.request.urlopen(url, timeout=40) as r:
        data = json.loads(r.read())
    return [t for t in (data.get("result") or [])
            if t.get("to", "").lower() == wallet.lower()]


def main():
    args = [a.lower() for a in sys.argv[1:]]
    wallet = (args[0] if args else WALLET_COBRO).lower()
    propias = set(args[1:]) if len(args) > 1 else {w.lower() for w in WALLETS_PROPIAS}

    txs = entrantes_usdc(wallet)
    n_prop = n_ext = 0
    usd_prop = usd_ext = 0.0
    ext_senders: dict[str, float] = {}
    first = last = None
    for t in txs:
        usd = int(t["value"]) / 1e6
        frm = t["from"].lower()
        ts = int(t["timeStamp"])
        first = first or ts
        last = ts
        if frm in propias:
            n_prop += 1
            usd_prop += usd
        else:
            n_ext += 1
            usd_ext += usd
            ext_senders[frm] = ext_senders.get(frm, 0.0) + usd

    def fecha(ts):
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d") if ts else "-"

    print("=== USDC ENTRANTE A LA WALLET DE COBRO (Base) ===")
    print(f"wallet: {wallet}")
    print(f"transferencias entrantes: {len(txs)}")
    print(f"  propias (auto-compras de prueba): {n_prop}  (${usd_prop:.4f})")
    print(f"  EXTERNAS (clientes reales):       {n_ext}  (${usd_ext:.4f})")
    print(f"remitentes externos distintos: {len(ext_senders)}")
    for k, v in sorted(ext_senders.items(), key=lambda kv: -kv[1]):
        print(f"   - {k}  ${v:.4f}")
    print(f"rango: {fecha(first)} -> {fecha(last)}")
    if n_ext == 0:
        print("\nVEREDICTO: 0 ventas externas. El cuello de botella es la DEMANDA,")
        print("no dónde estás listado. Más tiendas ayuda poco si nadie compra.")


if __name__ == "__main__":
    main()
