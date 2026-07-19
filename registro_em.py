# =============================================================================
# REGISTRO EN EXECUTION MARKET — identidad ERC-8004 + executor + service listings
# =============================================================================
# Registra el agent-data-toolkit como AGENTE VENDEDOR en Base:
#   1. Identidad ERC-8004 on-chain (gasless, la mintea el Facilitator).
#   2. Registro como executor (para poder vender).
#   3. Dos service listings (POST /api/v1/services) — pura vitrina, sin escrow:
#      solo se mueve dinero si un comprador ORDENA (y entonces el toolkit cobra 87%).
#
# Auth: ERC-8128 (RFC 9421 + EIP-191) firmando con la Cuenta 3.
# La clave se lee de Downloads\API KEYS.txt al correr; NUNCA está en este archivo.
#
# Uso:  python registro_em.py            (idempotente: verifica antes de registrar)
# =============================================================================
import base64
import hashlib
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct

API = "https://api.execution.market"
CHAIN_ID = 8453  # Base
KEYS_FILE = Path.home() / "Downloads" / "API KEYS.txt"
TOOLKIT_URL = "https://agent-data-toolkit.onrender.com"


def clave_cuenta3() -> str:
    for linea in KEYS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        if linea.startswith("CUENTA 3 BASE:"):
            return linea.split(":", 1)[1].strip()
    sys.exit("No encontré 'CUENTA 3 BASE:' en API KEYS.txt")


class ClienteEM8128:
    """Cliente ERC-8128 (spec exacta de docs.execution.market/identity/erc-8128)."""

    def __init__(self, pk: str):
        self.cuenta = Account.from_key(pk)
        self.wallet = self.cuenta.address

    def _firmar_eip191(self, mensaje: str) -> bytes:
        firmado = self.cuenta.sign_message(encode_defunct(text=mensaje))
        sig = firmado.signature  # 65 bytes r||s||v (v=27/28)
        if len(sig) != 65:
            sys.exit(f"firma de {len(sig)} bytes; esperaba 65")
        return bytes(sig)

    def _sig_params(self, covered, params):
        partes = ["(" + " ".join(f'"{c}"' for c in covered) + ")"]
        for k in ["created", "expires", "nonce", "keyid", "alg"]:
            v = params[k]
            partes.append(f"{k}={v}" if isinstance(v, int) else f'{k}="{v}"')
        return ";".join(partes)

    def _headers_firmados(self, metodo: str, url: str, body: str | None):
        nonce = httpx.get(f"{API}/api/v1/auth/erc8128/nonce", timeout=20).json()["nonce"]
        p = urlparse(url)
        created = int(time.time())
        covered = ["@method", "@authority", "@path"]
        digest = None
        if p.query:
            covered.append("@query")
        if body is not None:
            b64 = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
            digest = f"sha-256=:{b64}:"
            covered.append("content-digest")
        params = {
            "created": created,
            "expires": created + 300,
            "nonce": nonce,
            "keyid": f"erc8128:{CHAIN_ID}:{self.wallet.lower()}",
            "alg": "eip191",
        }
        sp = self._sig_params(covered, params)
        lineas = []
        for c in covered:
            if c == "@method":
                lineas.append(f'"@method": {metodo.upper()}')
            elif c == "@authority":
                lineas.append(f'"@authority": {p.netloc}')
            elif c == "@path":
                lineas.append(f'"@path": {p.path}')
            elif c == "@query":
                lineas.append(f'"@query": ?{p.query}')
            elif c == "content-digest":
                lineas.append(f'"content-digest": {digest}')
        lineas.append(f'"@signature-params": {sp}')
        sig_b64 = base64.b64encode(self._firmar_eip191("\n".join(lineas))).decode()
        h = {"Signature": f"eth=:{sig_b64}:", "Signature-Input": f"eth={sp}"}
        if digest:
            h["Content-Digest"] = digest
        return h

    def post(self, path: str, data: dict):
        url = f"{API}{path}"
        body = json.dumps(data)
        h = {"Content-Type": "application/json", **self._headers_firmados("POST", url, body)}
        r = httpx.post(url, content=body, headers=h, timeout=120)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {"raw": r.text[:300]}

    def get(self, path: str, firmado: bool = False):
        url = f"{API}{path}"
        h = self._headers_firmados("GET", url, None) if firmado else {}
        r = httpx.get(url, headers=h, timeout=60)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {"raw": r.text[:300]}


def main():
    c = ClienteEM8128(clave_cuenta3())
    print(f"wallet agente: {c.wallet}")

    # ---- 1. Identidad ERC-8004 (NUNCA registrar a ciegas: verificar primero)
    code, ident = c.get(f"/api/v1/reputation/identity/wallet/{c.wallet}?network=base")
    agent_id = ident.get("agent_id") if code == 200 else None
    if agent_id:
        print(f"1. identidad ya existia: Agent #{agent_id}")
    else:
        code, reg = c.post(
            "/api/v1/reputation/register",
            {"network": "base", "recipient": c.wallet, "agent_uri": TOOLKIT_URL},
        )
        print(f"1. register -> HTTP {code}: {json.dumps(reg)[:200]}")
        if code == 202:  # mint lento: SOLO poll, jamas re-POST (duplicaria identidad)
            rid = reg["registration_id"]
            for _ in range(30):
                time.sleep(5)
                code, reg = c.get(f"/api/v1/reputation/register/{rid}")
                if reg.get("status") != "pending":
                    break
            print(f"   poll final: {json.dumps(reg)[:200]}")
        agent_id = reg.get("agent_id")
    print(f"   Agent ID: {agent_id}")

    # ---- 2. Registro como executor (necesario para poder vender listings)
    code, ex = c.post(
        "/api/v1/executors/register",
        {"wallet_address": c.wallet, "display_name": "Agent Data Toolkit"},
    )
    print(f"2. executor -> HTTP {code}: {json.dumps(ex)[:250]}")

    # ---- 3. Service listings (vitrina; sin escrow hasta que alguien ordene)
    listings = [
        {
            "title": "Batch JSON repair + validate + CSV + HTML-to-Markdown (up to 50 docs)",
            "description": (
                "I repair broken/LLM-mangled JSON, validate against your schema, convert "
                "JSON to CSV and HTML to clean Markdown. Deliverable: a JSON artifact with "
                "all repaired/converted docs + per-doc status. Managed batch of up to 50 documents. "
                f"Need instant self-serve instead? Same engine, pay-per-call from $0.001 via x402 "
                f"at {TOOLKIT_URL} (discovery: /.well-known/x402), listed on the Coinbase x402 Bazaar."
            ),
            "category": "data_processing",
            "unit_price_usd": 2.0,
            "skills": ["json", "data-cleaning", "csv", "markdown", "x402"],
            "evidence_schema": ["text_response"],
            "payment_network": "base",
        },
        {
            "title": "LLM structured extraction from raw text (up to 20 docs)",
            "description": (
                "Turn messy raw text (emails, listings, logs, scraped pages) into clean JSON "
                "with the exact fields you specify. Powered by the agent-data-toolkit extraction "
                "engine (DeepSeek). Deliverable: JSON artifact with extracted records. "
                f"Self-serve x402 endpoint: POST {TOOLKIT_URL}/extract ($0.005/call, USDC on Base)."
            ),
            "category": "data_processing",
            "unit_price_usd": 5.0,
            "skills": ["extraction", "llm", "json", "nlp", "x402"],
            "evidence_schema": ["text_response"],
            "payment_network": "base",
        },
    ]
    code, existentes = c.get("/api/v1/services?limit=100")
    titulos = {l.get("title") for l in existentes.get("listings", [])} if code == 200 else set()
    for l in listings:
        if l["title"] in titulos:
            print(f"3. listing ya existia: {l['title'][:50]}")
            continue
        code, resp = c.post("/api/v1/services", l)
        print(f"3. listing -> HTTP {code}: {json.dumps(resp)[:250]}")

    print("\nLISTO. Verifica en https://execution.market (busca 'Agent Data Toolkit').")


if __name__ == "__main__":
    main()
