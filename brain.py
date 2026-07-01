"""Cerebro DeepSeek del agent-data-toolkit.

Cada tarea tiene su propio "escrito de comportamiento": un system prompt
específico con reglas estrictas para que DeepSeek actúe distinto según la
ficha y no cometa errores. Todas las salidas son JSON estricto.
"""
import json
import os

from json_repair import repair_json

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
}


class Brain:
    """Envuelve a DeepSeek con el escrito de comportamiento de cada tarea."""

    def __init__(self, api_key: str):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com",
                             timeout=60, max_retries=2)

    def run(self, task: str, content: str, schema: dict | None = None,
            instructions: str | None = None) -> dict:
        """Ejecuta una tarea y devuelve el JSON parseado. Lanza ValueError si falla."""
        system = BEHAVIORS[task].format(
            schema=json.dumps(schema) if schema else "{}",
            schema_clause=("5. The result must conform to this JSON Schema:\n"
                           + json.dumps(schema)) if schema else "",
        )
        if instructions:
            system += "\nAdditional instructions from the caller: " + instructions
        resp = self.client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": content}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw = resp.choices[0].message.content or ""
        try:
            return json.loads(repair_json(raw))
        except json.JSONDecodeError:
            raise ValueError("el modelo no devolvió JSON utilizable")


def get_brain() -> "Brain | None":
    key = os.getenv("DEEPSEEK_API_KEY", "")
    return Brain(key) if key else None
