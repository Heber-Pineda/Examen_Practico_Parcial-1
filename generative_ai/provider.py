"""Proveedor OpenAI real por HTTPS, con salida estructurada y sin SDK adicional."""

from dataclasses import dataclass, field
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backend.repository import ApiProblem


@dataclass(frozen=True)
class ProviderSettings:
    provider: str = "openai"
    model: str = ""
    api_key: str = field(default="", repr=False)
    timeout: int = 15
    max_output_tokens: int = 900

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        return cls(env.get("GENAI_PROVIDER", "openai").strip().lower(), env.get("GENAI_MODEL", "").strip(),
                   env.get("OPENAI_API_KEY", "").strip(), int(env.get("GENAI_TIMEOUT", "15")),
                   int(env.get("GENAI_MAX_OUTPUT_TOKENS", "900")))

    def __post_init__(self):
        if not 1 <= self.timeout <= 30 or not 200 <= self.max_output_tokens <= 1600:
            raise ValueError("Límites del proveedor fuera de rango.")
        if self.model and not re.fullmatch(r"[A-Za-z0-9._:-]{1,120}", self.model):
            raise ValueError("Identificador de modelo inválido.")

    @property
    def configured(self):
        return self.provider == "openai" and bool(self.model) and bool(self.api_key)


class OpenAIProvider:
    def __init__(self, settings=None, transport=None):
        self.settings = settings or ProviderSettings.from_env()
        self.transport = transport or urlopen

    def generate(self, instructions, payload, schema, name):
        if not self.settings.configured:
            raise ApiProblem(503, "AI_NOT_CONFIGURED", "Falta configurar proveedor, modelo o clave de IA.")
        body = {"model": self.settings.model, "store": False, "instructions": instructions,
                "input": json.dumps(payload, ensure_ascii=False), "max_output_tokens": self.settings.max_output_tokens,
                "text": {"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}}}
        request = Request("https://api.openai.com/v1/responses", data=json.dumps(body).encode(),
                          headers={"Authorization": "Bearer " + self.settings.api_key, "Content-Type": "application/json"}, method="POST")
        try:
            with self.transport(request, timeout=self.settings.timeout) as response:
                raw = response.read(131073)
            if len(raw) > 131072:
                raise ValueError("Respuesta demasiado grande.")
            result = json.loads(raw)
            if result.get("status") != "completed":
                raise ValueError("Respuesta incompleta.")
            pieces = [part["text"] for item in result.get("output", []) if item.get("type") == "message"
                      for part in item.get("content", []) if part.get("type") == "output_text"]
            parsed = json.loads("".join(pieces))
            if not isinstance(parsed, dict):
                raise ValueError("Formato inesperado.")
            return parsed
        except HTTPError as exc:
            code = "AI_PROVIDER_AUTH" if exc.code in (401, 403) else "AI_PROVIDER_LIMIT" if exc.code == 429 else "AI_PROVIDER_UNAVAILABLE"
            raise ApiProblem(503, code, "El proveedor de IA no pudo completar la solicitud. Revisa configuración, cuota o disponibilidad.") from None
        except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError):
            raise ApiProblem(503, "AI_PROVIDER_UNAVAILABLE", "El proveedor no devolvió una respuesta completa y verificable. Intenta nuevamente.") from None
