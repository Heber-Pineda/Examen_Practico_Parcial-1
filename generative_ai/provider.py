"""OpenAI y Ollama local: salida estructurada, transportes limitados y sin SDK."""

from dataclasses import dataclass, field
import json
import ipaddress
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener, urlopen

from backend.repository import ApiProblem

MAX_RESPONSE_BYTES = 131072


def ollama_endpoint(base_url):
    """Solo direcciones loopback literales; localhost se convierte a 127.0.0.1."""
    if not isinstance(base_url, str) or any(c.isspace() or ord(c) < 32 for c in base_url) or any(c in base_url for c in "\\%"):
        raise ValueError("OLLAMA_BASE_URL debe ser una URL HTTP de loopback sin credenciales ni rutas.")
    parsed = urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.username is not None or parsed.password is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("OLLAMA_BASE_URL debe ser una URL HTTP de loopback sin credenciales ni rutas.")
    host = "127.0.0.1" if parsed.hostname == "localhost" else parsed.hostname
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("Ollama solo permite localhost o una dirección IP de loopback.") from None
    if not address.is_loopback or parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("Ollama requiere dirección y puerto de loopback válidos.")
    netloc = f"[{address}]" if address.version == 6 else str(address)
    return "http://" + netloc + (f":{parsed.port}" if parsed.port is not None else "") + "/api/chat"


def strict_json(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("Propiedad JSON duplicada.")
            result[key] = item
        return result
    def constant(value):
        raise ValueError("Constante JSON no válida.")
    return json.loads(value, object_pairs_hook=pairs, parse_constant=constant)


def validate_generated(value, schema):
    """Valida el subconjunto de JSON Schema empleado por PLAN_SCHEMA y SELECT_SCHEMA."""
    types = {"object": dict, "array": list, "string": str, "null": type(None),
             "boolean": bool, "integer": int, "number": (int, float)}
    declared = schema.get("type")
    allowed = declared if isinstance(declared, list) else [declared]
    if not any(kind in types and type(value) in (types[kind] if isinstance(types[kind], tuple) else (types[kind],)) for kind in allowed):
        raise ValueError("Tipo de respuesta incompatible con el contrato.")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("Valor fuera del catálogo autorizado.")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if not set(schema.get("required", [])).issubset(value):
            raise ValueError("Faltan propiedades obligatorias.")
        if schema.get("additionalProperties") is False and not set(value).issubset(properties):
            raise ValueError("Propiedades no autorizadas.")
        for key, item in value.items():
            if key in properties:
                validate_generated(item, properties[key])
    elif isinstance(value, list):
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", MAX_RESPONSE_BYTES):
            raise ValueError("Cantidad de elementos fuera del contrato.")
        for item in value:
            validate_generated(item, schema["items"])


@dataclass(frozen=True)
class ProviderSettings:
    provider: str = "openai"
    model: str = ""
    api_key: str = field(default="", repr=False)
    timeout: int = 15
    max_output_tokens: int = 900
    ollama_base_url: str = "http://127.0.0.1:11434"

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        provider = env.get("GENAI_PROVIDER", "openai").strip().lower()
        return cls(provider, env.get("GENAI_MODEL", "").strip(),
                   env.get("OPENAI_API_KEY", "").strip(), int(env.get("GENAI_TIMEOUT", "60" if provider == "ollama" else "15")),
                   int(env.get("GENAI_MAX_OUTPUT_TOKENS", "900")),
                   env.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip())

    def __post_init__(self):
        max_timeout = 120 if self.provider == "ollama" else 30
        if (type(self.timeout) is not int or not 1 <= self.timeout <= max_timeout
                or type(self.max_output_tokens) is not int or not 200 <= self.max_output_tokens <= 1600):
            raise ValueError("Límites del proveedor fuera de rango.")
        if self.model and not re.fullmatch(r"[A-Za-z0-9._:-]{1,120}", self.model):
            raise ValueError("Identificador de modelo inválido.")
        if self.provider == "ollama":
            ollama_endpoint(self.ollama_base_url)

    @property
    def configured(self):
        return bool(self.model) and (self.provider == "ollama" or self.provider == "openai" and bool(self.api_key))


class OpenAIProvider:
    def __init__(self, settings=None, transport=None):
        self.settings = settings or ProviderSettings.from_env()
        self.transport = transport or urlopen

    def generate(self, instructions, payload, schema, name):
        if self.settings.provider != "openai" or not self.settings.configured:
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
            result = strict_json(raw)
            if not isinstance(result, dict) or result.get("status") != "completed":
                raise ValueError("Respuesta incompleta.")
            pieces = [part["text"] for item in result.get("output", []) if item.get("type") == "message"
                      for part in item.get("content", []) if part.get("type") == "output_text"]
            parsed = strict_json("".join(pieces))
            validate_generated(parsed, schema)
            return parsed
        except HTTPError as exc:
            code = "AI_PROVIDER_AUTH" if exc.code in (401, 403) else "AI_PROVIDER_LIMIT" if exc.code == 429 else "AI_PROVIDER_UNAVAILABLE"
            raise ApiProblem(503, code, "El proveedor de IA no pudo completar la solicitud. Revisa configuración, cuota o disponibilidad.") from None
        except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
            raise ApiProblem(503, "AI_PROVIDER_UNAVAILABLE", "El proveedor no devolvió una respuesta completa y verificable. Intenta nuevamente.") from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


class OllamaProvider:
    def __init__(self, settings=None, transport=None):
        self.settings = settings or ProviderSettings.from_env()
        # Evita que HTTP_PROXY o una redirección envíen evidencias fuera de loopback.
        self.transport = transport if transport is not None else build_opener(ProxyHandler({}), NoRedirect()).open

    def generate(self, instructions, payload, schema, name):
        if self.settings.provider != "ollama" or not self.settings.configured:
            raise ApiProblem(503, "AI_NOT_CONFIGURED", "Configura GENAI_PROVIDER=ollama y GENAI_MODEL en el backend.")
        endpoint = ollama_endpoint(self.settings.ollama_base_url)
        system = instructions + "\nDevuelve un único objeto JSON, sin Markdown ni comentarios, conforme a este esquema:\n" + json.dumps(schema, ensure_ascii=False)
        body = {"model": self.settings.model, "stream": False, "format": schema,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                "options": {"temperature": 0, "num_predict": self.settings.max_output_tokens}}
        request = Request(endpoint, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                          headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
        try:
            with self.transport(request, timeout=self.settings.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("Respuesta demasiado grande.")
            result = strict_json(raw)
            if not isinstance(result, dict) or result.get("done") is not True or result.get("error"):
                raise ValueError("Respuesta incompleta o fallida.")
            if result.get("model") != self.settings.model:
                raise ValueError("El modelo de respuesta no coincide con el solicitado.")
            if result.get("done_reason") not in (None, "stop"):
                raise ValueError("Generación truncada.")
            message = result["message"]
            if not isinstance(message, dict) or message.get("role") != "assistant" or message.get("tool_calls"):
                raise ValueError("Mensaje incompatible.")
            if not isinstance(message.get("content"), str):
                raise ValueError("No hay contenido JSON.")
            parsed = strict_json(message["content"])
            validate_generated(parsed, schema)
            return parsed
        except HTTPError as exc:
            code = "AI_MODEL_NOT_AVAILABLE" if exc.code == 404 else "AI_PROVIDER_TIMEOUT" if exc.code in (408, 504) else "AI_PROVIDER_REDIRECT" if 300 <= exc.code < 400 else "AI_PROVIDER_UNAVAILABLE"
            message = "Ollama no encuentra el modelo. Revisa GENAI_MODEL y los modelos descargados." if exc.code == 404 else "Ollama no pudo completar la solicitud. Verifica el servicio local y su dirección."
            raise ApiProblem(503, code, message) from None
        except TimeoutError:
            raise ApiProblem(503, "AI_PROVIDER_TIMEOUT", "Ollama excedió el tiempo de espera. Revisa GENAI_TIMEOUT y la disponibilidad del modelo.") from None
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ApiProblem(503, "AI_PROVIDER_TIMEOUT", "Ollama excedió el tiempo de espera configurado.") from None
            raise ApiProblem(503, "AI_PROVIDER_UNAVAILABLE", "No fue posible contactar con Ollama local. Comprueba que esté iniciado.") from None
        except OSError:
            raise ApiProblem(503, "AI_PROVIDER_UNAVAILABLE", "No fue posible contactar con Ollama local.") from None
        except (ValueError, KeyError, TypeError, RecursionError):
            raise ApiProblem(503, "AI_PROVIDER_INVALID_RESPONSE", "Ollama no devolvió JSON completo conforme al contrato. No se publicaron cifras ni respuestas no verificadas.") from None


def create_provider(settings=None, transport=None):
    settings = settings if settings is not None else ProviderSettings.from_env()
    if settings.provider == "ollama":
        return OllamaProvider(settings, transport)
    if settings.provider in ("", "openai"):
        return OpenAIProvider(settings, transport)
    raise ValueError("GENAI_PROVIDER debe ser openai u ollama.")
