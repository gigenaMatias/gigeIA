"""LLM asíncrono con Ollama prioritario, fallback Groq e historial acotado."""

import asyncio
import logging
import os
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from core.messages import Command, Response

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Eres un asistente virtual personal, sofisticado, atento y profesional. "
    "Responde siempre en español, de forma concisa, educada y natural. "
    "Limita cada respuesta a un máximo de tres oraciones, adecuado para voz. "
    "No uses emojis, Markdown ni afirmes haber realizado acciones que no ejecutaste."
)
FAILURE_RESPONSE = (
    "Lo siento, tengo problemas para conectar con mi núcleo de procesamiento."
)


def _load_dotenv() -> None:
    """Carga pares KEY=VALUE opcionales sin reemplazar el entorno del proceso."""
    dotenv_path = Path(__file__).resolve().parents[1] / ".env"
    if not dotenv_path.is_file():
        return
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        if entry.startswith("export "):
            entry = entry[7:].lstrip()
        name, separator, value = entry.partition("=")
        if not separator or not name.strip():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(name.strip(), value)


@dataclass(frozen=True)
class BrainConfig:
    """Endpoints, límites y comportamiento configurable de los proveedores."""

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:3b"
    ollama_timeout: float = 4.0
    groq_api_key: str = ""
    groq_model: str = "llama-3.1-8b-instant"
    groq_timeout: float = 8.0
    history_size: int = 5
    system_prompt: str = SYSTEM_PROMPT
    max_tokens: int = 120

    @classmethod
    def from_environment(cls) -> "BrainConfig":
        """Lee configuración del entorno, con valores locales seguros por defecto."""
        return cls(
            ollama_base_url=os.getenv(
                "OLLAMA_BASE_URL", "http://127.0.0.1:11434"
            ).rstrip("/"),
            ollama_model=os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
            ollama_timeout=_positive_float("OLLAMA_TIMEOUT_SECONDS", 4.0),
            groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
            groq_model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
            groq_timeout=_positive_float("GROQ_TIMEOUT_SECONDS", 8.0),
            history_size=_positive_int("BRAIN_HISTORY_SIZE", 5, allow_zero=True),
            system_prompt=os.getenv("BRAIN_SYSTEM_PROMPT", SYSTEM_PROMPT),
            max_tokens=_positive_int("BRAIN_MAX_TOKENS", 120),
        )


def _positive_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(f"{name} debe ser un número positivo.") from error
    if parsed <= 0:
        raise ValueError(f"{name} debe ser un número positivo.")
    return parsed


def _positive_int(name: str, default: int, *, allow_zero: bool = False) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{name} debe ser un entero válido.") from error
    is_invalid = parsed < 0 if allow_zero else parsed < 1
    if is_invalid:
        qualifier = "no negativo" if allow_zero else "positivo"
        raise ValueError(f"{name} debe ser {qualifier}.")
    return parsed


class LLMBrain:
    """Orquesta proveedores y mantiene solo los últimos N pares de mensajes."""

    def __init__(self, config: BrainConfig) -> None:
        if config.history_size < 0:
            raise ValueError("history_size no puede ser negativo.")
        if config.ollama_timeout <= 0 or config.groq_timeout <= 0:
            raise ValueError("Los tiempos de espera deben ser positivos.")
        self.config = config
        self._history: deque[tuple[str, str]] = deque(
            maxlen=config.history_size or None
        )
        self._history_enabled = config.history_size > 0
        self._history_lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"Content-Type": "application/json"},
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                    keepalive_expiry=30.0,
                ),
            )
        return self._client

    async def _messages(self, prompt: str) -> list[dict[str, str]]:
        async with self._history_lock:
            history = list(self._history)
        messages = [{"role": "system", "content": self.config.system_prompt}]
        for user_text, assistant_text in history:
            messages.append({"role": "user", "content": user_text})
            messages.append({"role": "assistant", "content": assistant_text})
        messages.append({"role": "user", "content": prompt})
        return messages

    async def _remember(self, prompt: str, answer: str) -> None:
        if not self._history_enabled:
            return
        async with self._history_lock:
            self._history.append((prompt, answer))

    async def _ask_ollama(self, messages: list[dict[str, str]]) -> str:
        client = self._http_client()
        response = await client.post(
            f"{self.config.ollama_base_url}/api/chat",
            json={
                "model": self.config.ollama_model,
                "messages": messages,
                "stream": False,
                "keep_alive": "5m",
                "options": {"num_predict": self.config.max_tokens},
            },
            timeout=httpx.Timeout(self.config.ollama_timeout),
        )
        response.raise_for_status()
        payload: Any = response.json()
        content = payload.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Ollama devolvió una respuesta vacía o inválida.")
        return content.strip()

    async def _ask_groq(self, messages: list[dict[str, str]]) -> str:
        if not self.config.groq_api_key:
            raise RuntimeError("GROQ_API_KEY no está configurada.")
        client = self._http_client()
        response = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.config.groq_api_key}"},
            json={
                "model": self.config.groq_model,
                "messages": messages,
                "max_tokens": self.config.max_tokens,
                "temperature": 0.4,
                "stream": False,
            },
            timeout=httpx.Timeout(self.config.groq_timeout),
        )
        response.raise_for_status()
        payload: Any = response.json()
        choices = payload.get("choices")
        content = choices[0].get("message", {}).get("content") if choices else None
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Groq devolvió una respuesta vacía o inválida.")
        return content.strip()

    async def generate_response(self, prompt: str) -> str:
        """Genera respuesta usando primero Ollama y luego Groq como fallback."""
        cleaned_prompt = prompt.strip()
        if not cleaned_prompt:
            return "No he recibido ningún comando. ¿En qué puedo ayudarte?"

        messages = await self._messages(cleaned_prompt)
        try:
            answer = await self._ask_ollama(messages)
        except (httpx.HTTPError, ValueError, RuntimeError) as error:
            logger.warning("Ollama no disponible; se intentará Groq: %s", error)
        else:
            await self._remember(cleaned_prompt, answer)
            return answer

        try:
            answer = await self._ask_groq(messages)
        except (httpx.HTTPError, ValueError, RuntimeError) as error:
            logger.error("Fallaron Ollama y Groq: %s", error)
            return FAILURE_RESPONSE

        await self._remember(cleaned_prompt, answer)
        return answer

    async def aclose(self) -> None:
        """Cierra el pool HTTP persistente cuando finaliza el asistente."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


_load_dotenv()
_brain = LLMBrain(BrainConfig.from_environment())


def configure_brain(config: BrainConfig) -> None:
    """Reemplaza la configuración y el historial del cerebro global."""
    global _brain
    _brain = LLMBrain(config)


async def generate_response(prompt: str) -> str:
    """Interfaz principal para obtener una respuesta corta en español."""
    return await _brain.generate_response(prompt)


async def close_brain_client() -> None:
    """Libera el cliente HTTP compartido del cerebro global."""
    await _brain.aclose()


AnswerCommand = Callable[[str], Awaitable[str]]


async def brain_worker(
    llm_queue: asyncio.Queue[Command | None],
    response_queue: asyncio.Queue[Response | None],
    answer_command: AnswerCommand = generate_response,
) -> None:
    """Consume la cola del LLM y publica respuestas listas para el TTS."""
    while True:
        command = await llm_queue.get()
        if command is None:
            return
        answer = await answer_command(command.text)
        await response_queue.put(Response.now(answer))


async def _interactive_test() -> None:
    """Bucle de consola para probar el fallback de proveedores."""
    print("Prueba del cerebro JARVIS. Escribe 'salir' para terminar.")
    try:
        while True:
            prompt = await asyncio.to_thread(input, "Tú: ")
            if prompt.strip().lower() in {"salir", "exit", "quit"}:
                break
            print(f"Asistente: {await generate_response(prompt)}")
    finally:
        await close_brain_client()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(_interactive_test())
    except KeyboardInterrupt:
        print("\nPrueba interrumpida.")
