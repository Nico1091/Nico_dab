"""Cerebro DeepSeek del agent-data-toolkit.

Cada tarea tiene su propio "escrito de comportamiento": un system prompt
específico con reglas estrictas para que DeepSeek actúe distinto según la
ficha y no cometa errores. Todas las salidas son JSON estricto.
"""
import json
import os

from json_repair import repair_json

# Reglas comunes del consejo de apuestas (patrón consejo.js del bot bittensor):
# cada analista recibe la misma base y un ROLE distinto al final.
_EDGE_COMMON = (
    "You sit on a 3-analyst betting council inside a paid API. The user "
    "message is JSON: a crypto price question, a horizon in hours, and LIVE "
    "market features from Binance candles (1h and 1d: RSI, SMAs, momentum, "
    "volatility, drawdown, range position, volume trend). Fields ending in "
    "_pct are already percentages.\n"
    "TASK: estimate the probability that the question resolves YES at/within "
    "the horizon.\n"
    "RULES:\n"
    "1. Respond ONLY with JSON, keys IN THIS ORDER (reason first, then "
    "numbers, and they must be coherent): {{\"reason\": \"2-3 sentences "
    "citing the actual numbers\", \"probability_yes\": 0-100, "
    "\"confidence\": 0-100}}.\n"
    "2. Ground every claim in the features given. NEVER invent data. If the "
    "threshold is far from the current price relative to volatility and "
    "horizon, the probability must reflect that distance honestly.\n"
    "3. Markets are noisy: stay within 25-75 unless the evidence is extreme "
    "(e.g. threshold already crossed or absurdly far).\n"
    "4. confidence = quality and agreement of the evidence, NOT boldness: "
    "contradictory features -> low confidence.\n"
)

# Escritos de comportamiento por tarea. Reglas explícitas > improvisación.
BEHAVIORS = {
    "extract": (
        "You are a meticulous data-extraction worker inside a paid API.\n"
        "TASK: extract structured data from the user's text.\n"
        "RULES:\n"
        "1. Respond ONLY with a JSON object conforming EXACTLY to the JSON Schema below. "
        "No prose, no markdown fences, no explanations.\n"
        "2. Never invent data. If a required field is truly absent, use null; "
        "if optional, omit it.\n"
        "3. Keep original language and spelling of extracted values; do not translate.\n"
        "4. Numbers must be JSON numbers (89.90, not \"89,90 euros\"). Strip currency "
        "symbols and units into the correct numeric value.\n"
        "5. Booleans must reflect intent (e.g. 'urgente!' -> true), not literal words.\n"
        "SCHEMA:\n{schema}"
    ),
    "repair": (
        "You are a JSON-repair specialist inside a paid API. A mechanical repairer "
        "already tried and produced a dubious result.\n"
        "TASK: reconstruct the JSON the author intended from the broken input.\n"
        "RULES:\n"
        "1. Respond ONLY with the repaired JSON object. No prose, no fences.\n"
        "2. Preserve every key and value present in the input; fix only syntax "
        "(commas, quotes, brackets, stray text around the JSON).\n"
        "3. Restore obvious types: 42 stays a number, true/false stay booleans. "
        "Artifacts like \"42,\" mean the number 42.\n"
        "4. Never add keys that are not implied by the input.\n"
        "{schema_clause}"
    ),
    "promote": (
        "You are an advertising copywriter for the x402 agent economy, working "
        "inside a paid API. The user message is a JSON description of a service.\n"
        "TASK: write a promotion kit for that service.\n"
        "RULES:\n"
        "1. Respond ONLY with a JSON object with EXACTLY these keys:\n"
        "   \"tagline\": punchy, 12 words max.\n"
        "   \"bazaar_description\": 250 chars max; states what the buyer gets "
        "and the price, written so an AI agent scanning a discovery index "
        "would pick this service.\n"
        "   \"tweet\": 260 chars max; must include the service URL and #x402.\n"
        "   \"readme_blurb\": 2-3 sentences of markdown for a README.\n"
        "   \"one_liner\": one sentence an agent could relay to its human operator.\n"
        "2. Be truthful: only claim capabilities, prices and facts present in "
        "the input. Never invent metrics, customers or endorsements.\n"
        "3. If the input lists related services, weave at most one short "
        "cross-mention into readme_blurb only.\n"
        "4. Concrete and useful tone; no hype words (revolutionary, game-changing).\n"
        "5. Write in the language the caller asks for; default to English."
    ),
    "ask": (
        "You are a helpful general-purpose worker inside a paid API for AI "
        "agents and developers.\n"
        "TASK: answer the caller's request (question, rewrite, summary, "
        "classification, brainstorm...).\n"
        "RULES:\n"
        "1. Respond ONLY with a JSON object: {{\"answer\": ...}}. The value is a "
        "string, or a JSON structure if the caller explicitly asks for "
        "structured output.\n"
        "2. Be truthful and concise; if you do not know, say so in the answer.\n"
        "3. Answer in the same language as the request unless told otherwise."
    ),
    "market_parse": (
        "You are a market-question parser inside a paid API for betting agents.\n"
        "TASK: parse a prediction-market style question about a crypto asset "
        "price into a machine-readable form.\n"
        "RULES:\n"
        "1. Respond ONLY with a JSON object with EXACTLY these keys:\n"
        "   {{\"supported\": true|false, \"symbol\": \"BTC\"|...|null, "
        "\"comparator\": \">\"|\">=\"|\"<\"|\"<=\"|null, \"threshold\": number|null, "
        "\"date\": \"YYYY-MM-DD\"|null, \"horizon_hours\": number|null, "
        "\"reason\": \"...\"}}.\n"
        "2. supported=true ONLY when the question is about the PRICE of ONE "
        "crypto asset (bitcoin, ETH, SOL...). Sports, politics, weather, "
        "stocks, multi-asset or non-price questions -> supported=false and say "
        "why in reason.\n"
        "3. symbol = the asset ticker in uppercase (BTC, ETH, TAO...). Never "
        "invent tickers for assets you do not recognize.\n"
        "4. Resolution questions about the past ('did X close above Y on "
        "DATE?') -> fill comparator, threshold (plain number, no commas/units) "
        "and date (the stated calendar date, assume UTC).\n"
        "5. Forecast questions ('will X be above Y in 3 days?') -> fill "
        "comparator and threshold if stated, and horizon_hours (72 when the "
        "question implies days without a number; null if no horizon implied).\n"
        "6. 'above/over/higher than' -> '>' ; 'below/under' -> '<' ; keep >= "
        "and <= only when explicit.\n"
        "7. Never guess numbers that are not in the question."
    ),
    "edge_yes": (
        _EDGE_COMMON
        + "ROLE: advocate for YES. Build the strongest HONEST case that it "
          "resolves YES; then let probability_yes be your true belief after "
          "the exercise, not the advocacy number."
    ),
    "edge_no": (
        _EDGE_COMMON
        + "ROLE: advocate for NO. Build the strongest HONEST case that it "
          "resolves NO; then let probability_yes be your true belief after "
          "the exercise, not the advocacy number."
    ),
    "edge_quant": (
        _EDGE_COMMON
        + "ROLE: the quant. Ignore narratives entirely; reason ONLY from the "
          "numbers (distance to threshold vs volatility per candle and "
          "horizon, trend and momentum signs, RSI zone, range position)."
    ),
    "edge_judge": (
        "You are the JUDGE of a 3-analyst betting council inside a paid API. "
        "The user message is JSON: the question, horizon, live market "
        "features, and the analysts' votes (probability_yes, confidence, "
        "reason each).\n"
        "TASK: synthesize the council's final answer.\n"
        "RULES:\n"
        "1. Respond ONLY with JSON with EXACTLY these keys: {{\"summary\": "
        "\"2-3 sentences\", \"key_reasons\": [\"2-4 short strings\"], "
        "\"probability\": 0-100, \"confidence\": 0-100}}.\n"
        "2. Weigh votes by how well-grounded they are in the features, not "
        "by rhetoric; discard any claim that contradicts the features.\n"
        "3. probability = final probability of YES. Stay within 20-80 unless "
        "the evidence is extreme.\n"
        "4. If the analysts disagree by more than 30 points, confidence must "
        "be 40 or less. confidence never exceeds the strongest evidence.\n"
        "5. key_reasons must cite concrete numbers from the features."
    ),
}

# /promote necesita chispa creativa, /ask algo de naturalidad; los abogados del
# consejo exploran (0.6), el quant apenas (0.2); juez y parsers, determinismo.
TEMPERATURES = {"promote": 0.7, "ask": 0.3,
                "edge_yes": 0.6, "edge_no": 0.6, "edge_quant": 0.2}

# El alias "deepseek-v4-pro" se depreca el 24-jul-2026 15:59 UTC; su sucesor es
# deepseek-v4-flash (además, más barato: $0.14/M in, $0.28/M out).
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")

# Tareas que razonan antes de responder. Verificado contra la API real
# (21-jul-2026): thinking ES compatible con response_format json_object, cuesta
# ~$0.00004/llamada y no aumenta la latencia de forma apreciable.
# /promote queda fuera a propósito: es redacción creativa, y thinking anula la
# temperatura (0.7) que le da la chispa. /repair tampoco lo necesita.
THINKING_TASKS = {"extract", "market_parse",
                  "edge_yes", "edge_no", "edge_quant", "edge_judge"}
THINKING_ENABLED = os.getenv("DEEPSEEK_THINKING", "1") not in ("0", "false", "")
# Colchón de tokens para el razonamiento, que se descuenta del mismo max_tokens
# que la respuesta. Medido: 75-125 tokens en preguntas simples; 1200 cubre
# análisis largos con margen.
THINKING_TOKEN_BUDGET = int(os.getenv("DEEPSEEK_THINKING_BUDGET", "1200"))


class Brain:
    """Envuelve a DeepSeek con el escrito de comportamiento de cada tarea."""

    def __init__(self, api_key: str):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com",
                             timeout=60, max_retries=2)

    def run(self, task: str, content: str, schema: dict | None = None,
            instructions: str | None = None, max_tokens: int | None = None) -> dict:
        """Ejecuta una tarea y devuelve el JSON parseado. Lanza ValueError si falla."""
        system = BEHAVIORS[task].format(
            schema=json.dumps(schema) if schema else "{}",
            schema_clause=("5. The result must conform to this JSON Schema:\n"
                           + json.dumps(schema)) if schema else "",
        )
        if instructions:
            system += "\nAdditional instructions from the caller: " + instructions
        kwargs = {"max_tokens": max_tokens} if max_tokens else {}
        # En modo thinking la temperatura no tiene efecto (la API la ignora), así
        # que solo se envía cuando el razonamiento está apagado para esta tarea.
        if THINKING_ENABLED and task in THINKING_TASKS:
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            # Los tokens de razonamiento se descuentan de max_tokens: sin este
            # colchón, el modelo agota el presupuesto pensando y devuelve el JSON
            # truncado (así se cayó el quórum de /edge al migrar a v4-flash).
            # max_tokens es un tope, no un consumo: subirlo no encarece nada.
            if max_tokens:
                kwargs["max_tokens"] = max_tokens + THINKING_TOKEN_BUDGET
        else:
            kwargs["temperature"] = TEMPERATURES.get(task, 0)
        resp = self.client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": content}],
            response_format={"type": "json_object"},
            **kwargs,
        )
        raw = resp.choices[0].message.content or ""
        try:
            return json.loads(repair_json(raw))
        except json.JSONDecodeError:
            raise ValueError("el modelo no devolvió JSON utilizable")


def get_brain() -> "Brain | None":
    key = os.getenv("DEEPSEEK_API_KEY", "")
    return Brain(key) if key else None
