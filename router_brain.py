"""Cerebro del x402-router: DeepSeek elige a qué servicio enrutar una petición
en lenguaje natural. Si no hay clave o el LLM falla, decide una heurística de
solape de palabras (con el precio como desempate) — el router nunca se queda
sin respuesta por culpa del cerebro.
"""
import json
import os

ROUTE_BEHAVIOR = (
    "You are the routing brain of a paid API gateway for AI agents.\n"
    "The user message contains a JSON with: \"query\" (what the caller needs) "
    "and \"candidates\" (list of services: resource URL, description, price_usd).\n"
    "TASK: pick the single best candidate for the query.\n"
    "RULES:\n"
    "1. Respond ONLY with a JSON object: {{\"resource\": \"<candidate URL>\", "
    "\"reason\": \"<one short sentence>\"}}.\n"
    "2. The resource MUST be copied verbatim from the candidates list.\n"
    "3. Prefer the cheapest candidate among those that truly match the need.\n"
    "4. If nothing matches well, pick the closest one and say so in reason."
)


class Brain:
    def __init__(self, api_key: str):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com",
                             timeout=30, max_retries=1)

    def choose(self, query: str, candidates: list[dict]) -> dict | None:
        compact = [{"resource": c["resource"],
                    "description": c["description"][:140],
                    "price_usd": c["price_atomic"] / 1_000_000}
                   for c in candidates[:30]]
        try:
            resp = self.client.chat.completions.create(
                model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
                messages=[{"role": "system", "content": ROUTE_BEHAVIOR.format()},
                          {"role": "user", "content": json.dumps(
                              {"query": query, "candidates": compact})}],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=200,
            )
            out = json.loads(resp.choices[0].message.content or "{}")
            urls = {c["resource"] for c in candidates}
            if out.get("resource") in urls:
                return {"resource": out["resource"],
                        "reason": str(out.get("reason", ""))[:200],
                        "worker": "deepseek"}
        except Exception:
            pass
        return None


def heuristic_choice(query: str, candidates: list[dict]) -> dict:
    """Solape de palabras query↔descripción+URL; a igualdad, el más barato."""
    words = {w for w in query.lower().split() if len(w) > 2}
    best, best_key = None, None
    for c in candidates:
        text = (c["description"] + " " + c["resource"]).lower()
        overlap = sum(1 for w in words if w in text)
        key = (-overlap, c["price_atomic"])
        if best_key is None or key < best_key:
            best, best_key = c, key
    return {"resource": best["resource"],
            "reason": "keyword-overlap heuristic", "worker": "heuristic"}


def get_brain() -> "Brain | None":
    key = os.getenv("DEEPSEEK_API_KEY", "")
    return Brain(key) if key else None


def choose(query: str, candidates: list[dict]) -> dict:
    brain = get_brain()
    if brain:
        out = brain.choose(query, candidates)
        if out:
            return out
    return heuristic_choice(query, candidates)
