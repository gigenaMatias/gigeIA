"""Detección local y asíncrona de la palabra de activación «Che Gige»."""

import asyncio
import json
import logging
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

from config import (
    WAKE_WORD,
    WAKE_WORD_ALTERNATIVES,
    WAKE_WORD_CHUNK_SIZE,
    WAKE_WORD_MODEL_PATH,
    WAKE_WORD_SAMPLE_RATE,
    WAKE_WORD_SENSITIVITY,
)
from core.events import event_hub

logger = logging.getLogger(__name__)


class WakeWordError(RuntimeError):
    """Error de configuración o inicialización del detector."""


class WakeWordDetector:
    """Escucha una gramática Vosk local y libera el micrófono al activarse.

    Vosk usa un modelo acústico español descargado localmente y no necesita
    conexión de red. PyAudio entrega bloques PCM mediante un thread de trabajo
    para que las demás tareas asyncio continúen ejecutándose.
    """

    def __init__(
        self,
        model_path: Path = WAKE_WORD_MODEL_PATH,
        sample_rate: int = WAKE_WORD_SAMPLE_RATE,
        chunk_size: int = WAKE_WORD_CHUNK_SIZE,
        sensitivity: float = WAKE_WORD_SENSITIVITY,
        keyword: str = WAKE_WORD,
        alternatives: tuple[str, ...] = WAKE_WORD_ALTERNATIVES,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate debe ser positivo.")
        if chunk_size <= 0:
            raise ValueError("chunk_size debe ser positivo.")
        if not 0.0 <= sensitivity <= 1.0:
            raise ValueError("sensitivity debe estar entre 0.0 y 1.0.")
        if not model_path.is_dir():
            raise WakeWordError(
                f"No se encontró el modelo Vosk español en {model_path}. "
                "Descarga e instala el modelo indicado en las instrucciones."
            )

        try:
            import pyaudio
            import vosk
        except ImportError as error:
            missing = error.name or "dependencia"
            raise WakeWordError(
                f"Falta {missing}. Instala las dependencias con "
                "'pip install -r requirements.txt'."
            ) from error

        vosk.SetLogLevel(-1)
        try:
            self._model = vosk.Model(str(model_path))
            self._pyaudio_module = pyaudio
            self._audio = pyaudio.PyAudio()
        except Exception as error:
            raise WakeWordError(f"No se pudo inicializar el audio/Vosk: {error}") from error

        phrases = {keyword.strip().lower(), *(phrase.lower() for phrase in alternatives)}
        self._phrases = {_normalize(phrase) for phrase in phrases if phrase.strip()}
        if not self._phrases:
            self._audio.terminate()
            raise ValueError("Se requiere al menos una frase de activación.")

        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.sensitivity = sensitivity
        self._grammar = json.dumps(
            sorted(phrases | {"[unk]"}),
            ensure_ascii=False,
        )
        self._stream: Any | None = None
        self._closed = False
        self._listening = False

    @property
    def model(self) -> Any:
        """Modelo Vosk cargado, reutilizable por el STT local."""
        return self._model

    def _open_stream(self) -> None:
        if self._closed:
            raise WakeWordError("El detector ya fue cerrado.")
        if self._stream is not None:
            return
        try:
            self._stream = self._audio.open(
                format=self._pyaudio_module.paInt16,
                channels=1,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.chunk_size,
            )
            self._stream.start_stream()
        except Exception as error:
            self._close_stream()
            raise WakeWordError(f"No se pudo abrir el micrófono: {error}") from error

    def _close_stream(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            if stream.is_active():
                stream.stop_stream()
        finally:
            stream.close()

    def _make_recognizer(self) -> Any:
        import vosk

        recognizer = vosk.KaldiRecognizer(
            self._model,
            self.sample_rate,
            self._grammar,
        )
        recognizer.SetWords(True)
        return recognizer

    def _read_and_detect(self, recognizer: Any) -> bool:
        if self._stream is None:
            raise WakeWordError("El stream de audio no está abierto.")
        audio = self._stream.read(
            self.chunk_size,
            exception_on_overflow=False,
        )
        if not recognizer.AcceptWaveform(audio):
            return False

        result = json.loads(recognizer.Result())
        phrase = _normalize(str(result.get("text", "")))
        if phrase not in self._phrases:
            return False

        words = result.get("result", [])
        confidence = [float(word["conf"]) for word in words if "conf" in word]
        # Vosk no expone siempre confidencias; en ese caso la gramática exacta
        # basta. Sensibilidad alta reduce el umbral de confianza exigido.
        if confidence:
            confidence_threshold = 0.15 + (1.0 - self.sensitivity) * 0.7
            if sum(confidence) / len(confidence) < confidence_threshold:
                return False
        return True

    async def wait_for_keyword(self) -> bool:
        """Espera la frase de activación sin bloquear el event loop."""
        if self._closed:
            raise WakeWordError("El detector ya fue cerrado.")
        if self._listening:
            raise WakeWordError("wait_for_keyword no admite llamadas simultáneas.")

        self._listening = True
        recognizer: Any | None = None
        try:
            await asyncio.to_thread(self._open_stream)
            recognizer = await asyncio.to_thread(self._make_recognizer)
            while True:
                detected = await asyncio.to_thread(
                    self._read_and_detect,
                    recognizer,
                )
                if detected:
                    await event_hub.publish("wakeword", "Palabra de activación detectada.")
                    await asyncio.to_thread(self._play_confirmation_beep)
                    return True
        finally:
            self._listening = False
            await asyncio.to_thread(self._close_stream)

    @staticmethod
    def _play_confirmation_beep() -> None:
        """Reproduce una confirmación corta con utilidades del sistema."""
        if sys.platform == "win32":
            import winsound

            winsound.Beep(880, 120)
        else:
            print("\a", end="", flush=True)

    def cleanup(self) -> None:
        """Cierra el stream y libera el dispositivo de audio de forma segura."""
        if self._closed:
            return
        self._closed = True
        try:
            self._close_stream()
        finally:
            self._audio.terminate()
            logger.debug("Detector de palabra de activación cerrado.")


def _normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    unaccented = "".join(
        character for character in folded if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", unaccented)).strip()


async def _interactive_test() -> None:
    detector = WakeWordDetector()
    print("Escuchando... Di 'Che Gige' para activar")
    try:
        while await detector.wait_for_keyword():
            print("¡Activado!")
            print("Escuchando... Di 'Che Gige' para activar")
    finally:
        detector.cleanup()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(_interactive_test())
    except KeyboardInterrupt:
        print("\nPrueba interrumpida.")
