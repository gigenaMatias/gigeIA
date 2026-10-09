"""Adaptadores de demostración sustituibles por micrófono y STT reales."""

from core.listener import AudioSource, Transcriber


class DemoAudioSource:
    """Emite un comando de texto como bloque; no captura audio real."""

    def __init__(self, sample_text: str = "ayuda") -> None:
        self._sample = sample_text.encode("utf-8")
        self._sent = False

    def read_block(self) -> bytes | None:
        if self._sent:
            return None
        self._sent = True
        return self._sample

    def close(self) -> None:
        pass


class Utf8DemoTranscriber:
    """Transcriptor de demostración: espera bloques UTF-8, no audio PCM."""

    def transcribe(self, audio: bytes) -> str:
        return audio.decode("utf-8", errors="replace")


def demo_adapters() -> tuple[AudioSource, Transcriber]:
    return DemoAudioSource(), Utf8DemoTranscriber()
