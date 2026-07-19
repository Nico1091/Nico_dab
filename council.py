"""Consejo de apuestas multihilo: 3 analistas DeepSeek en paralelo + un juez.

Porta el patrón consejo.js del bot bittensor (Promise.all → ThreadPoolExecutor):
el abogado del SÍ y el abogado del NO construyen el mejor caso honesto de cada
lado, el QUANT solo mira los números; un JUEZ pondera los tres votos contra las
features y sintetiza probabilidad + confianza. Fail-safe: analista caído tras
2 intentos queda fuera del quórum; con menos de 2 votos vivos no hay respuesta
(mejor 502 que una señal inventada).

HONESTIDAD: más analistas = mejor filtro, NO certeza de ganancia.
"""
import json
from concurrent.futures import ThreadPoolExecutor

ANALYSTS = ("edge_yes", "edge_no", "edge_quant")
MIN_VOTES = 2


def _clamp(value, lo=0.0, hi=100.0) -> float:
    return max(lo, min(hi, float(value)))


def _ask_analyst(brain, behavior: str, payload: str) -> dict:
    last_error = None
    for _ in range(2):
        try:
            out = brain.run(behavior, payload, max_tokens=400)
            return {
                "analyst": behavior.removeprefix("edge_"),
                "probability_yes": round(_clamp(out["probability_yes"]), 1),
                "confidence": round(_clamp(out["confidence"]), 1),
                "reason": str(out.get("reason", ""))[:400],
            }
        except Exception as e:
            last_error = e
    return {"analyst": behavior.removeprefix("edge_"), "failed": True,
            "reason": f"analyst down after 2 attempts: {last_error}"}


def run_council(brain, question: str, horizon_hours: float | None,
                features: dict) -> dict:
    """Devuelve {probability, confidence, direction, key_reasons, summary, votes}."""
    payload = json.dumps({"question": question, "horizon_hours": horizon_hours,
                          "market_features": features})

    # Multihilo real: las 3 consultas salen a la vez (patrón consejo.js).
    with ThreadPoolExecutor(max_workers=len(ANALYSTS)) as pool:
        votes = list(pool.map(lambda b: _ask_analyst(brain, b, payload), ANALYSTS))

    alive = [v for v in votes if not v.get("failed")]
    if len(alive) < MIN_VOTES:
        raise RuntimeError("council quorum lost: "
                           + " | ".join(v["reason"] for v in votes))

    verdict = brain.run("edge_judge", json.dumps({
        "question": question, "horizon_hours": horizon_hours,
        "market_features": features, "votes": alive,
    }), max_tokens=500)

    probability = round(_clamp(verdict["probability"]), 1)
    confidence = round(_clamp(verdict["confidence"]), 1)
    # La dirección se deriva determinista de la probabilidad, no del texto del LLM.
    direction = ("YES" if probability >= 55 else
                 "NO" if probability <= 45 else "TOSS_UP")
    key_reasons = [str(r)[:300] for r in verdict.get("key_reasons", [])][:4]

    return {
        "probability": probability,
        "confidence": confidence,
        "direction": direction,
        "key_reasons": key_reasons,
        "summary": str(verdict.get("summary", ""))[:600],
        "votes": votes,
    }
