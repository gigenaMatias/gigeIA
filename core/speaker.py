"""Worker de salida de voz."""

import asyncio
import logging
import os
import subprocess
from typing import Protocol

from core.events import event_hub
from core.messages import Response

logger = logging.getLogger(__name__)


class Speaker(Protocol):
    def speak(self, text: str) -> None:
        ...


class WindowsSystemSpeaker:
    """Usa System.Speech de Windows; el texto nunca se interpola en el script."""

    _SCRIPT = (
        "Add-Type -AssemblyName System.Speech; "
        "$text = [Console]::In.ReadToEnd(); "
        "$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$voice.Speak($text); $voice.Dispose()"
    )

    def speak(self, text: str) -> None:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                self._SCRIPT,
            ],
            input=text,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or "PowerShell terminó con error."
            raise RuntimeError(f"No se pudo sintetizar la respuesta: {detail}")


class ConsoleSpeaker:
    """Salida alternativa para sistemas sin el adaptador de voz de Windows."""

    def speak(self, text: str) -> None:
        print(f"Asistente: {text}", flush=True)


def default_speaker() -> Speaker:
    if os.name == "nt":
        return WindowsSystemSpeaker()
    return ConsoleSpeaker()


async def speaker_worker(
    response_queue: asyncio.Queue[Response | None],
    speaker: Speaker,
    response_spoken: asyncio.Event | None = None,
) -> None:
    """Consume respuestas y delega la síntesis para no detener asyncio."""
    while True:
        response = await response_queue.get()
        if response is None:
            return
        try:
            logger.info("[JARVIS] %s", response.text)
            await asyncio.to_thread(speaker.speak, response.text)
            await event_hub.publish("response", response.text)
        except Exception as error:
            logger.exception("Error durante la síntesis de voz.")
            await event_hub.publish("error", f"Error de TTS: {error}")
        finally:
            if response_spoken is not None:
                response_spoken.set()
