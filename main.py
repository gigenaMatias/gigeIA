"""Orquestador asíncrono del asistente JARVIS con activación por voz."""

import asyncio
import json
import logging
import signal
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from core.brain import brain_worker, close_brain_client
from core.events import event_hub, set_core_running
from core.listener import (
    AudioSource,
    Transcriber,
    capture_worker,
    listener_worker,
)
from core.messages import AudioFrame, Command, Response
from core.router import QuickAction, router_worker
from core.speaker import Speaker, default_speaker, speaker_worker
from core.wakeword import WakeWordDetector
from modules.actions import quick_action

logger = logging.getLogger("jarvis")


@dataclass(frozen=True)
class UserCommand(Command):
    """Instrucción reconocida que circula por la cola persistente."""


SourceFactory = Callable[[], AudioSource]


class MicrophoneCommandSource:
    """Captura frases desde el micrófono con timeouts y audio PCM mono."""

    def __init__(
        self,
        sample_rate: int,
        timeout: float = 2.0,
        phrase_time_limit: float = 8.0,
    ) -> None:
        try:
            import speech_recognition as sr
        except ImportError as error:
            raise RuntimeError(
                "Falta SpeechRecognition/PyAudio. Ejecuta "
                "'pip install -r requirements.txt'."
            ) from error
        self._sr = sr
        self._recognizer = sr.Recognizer()
        self._recognizer.pause_threshold = 0.8
        self._microphone = sr.Microphone(sample_rate=sample_rate)
        self._sample_rate = sample_rate
        self._timeout = timeout
        self._phrase_time_limit = phrase_time_limit
        self._calibrated = False
        self._closed = False

    def read_block(self) -> bytes | None:
        """Retorna una frase PCM; None termina la sesión tras silencio."""
        if self._closed:
            return None
        try:
            with self._microphone as microphone:
                if not self._calibrated:
                    self._recognizer.adjust_for_ambient_noise(
                        microphone,
                        duration=0.2,
                    )
                    self._calibrated = True
                audio = self._recognizer.listen(
                    microphone,
                    timeout=self._timeout,
                    phrase_time_limit=self._phrase_time_limit,
                )
        except self._sr.WaitTimeoutError:
            return None
        return audio.get_raw_data(convert_rate=self._sample_rate, convert_width=2)

    def close(self) -> None:
        self._closed = True


class VoskCommandTranscriber:
    """Transcribe frases localmente y reutiliza el modelo español ya cargado."""

    def __init__(self, model: Any, sample_rate: int) -> None:
        import vosk

        self._vosk = vosk
        self._model = model
        self._sample_rate = sample_rate

    def transcribe(self, audio: bytes) -> str:
        recognizer = self._vosk.KaldiRecognizer(self._model, self._sample_rate)
        recognizer.AcceptWaveform(audio)
        result = json.loads(recognizer.FinalResult())
        return str(result.get("text", ""))


async def _logged_quick_action(text: str) -> str | None:
    logger.info("[ROUTER] Evaluando comando: %s", text)
    result = await quick_action(text)
    destination = "acción local" if result is not None else "cerebro LLM"
    logger.info("[ROUTER] Destino: %s.", destination)
    return result


def _log_signal(signum: signal.Signals, stop_event: asyncio.Event) -> None:
    logger.info("[SISTEMA] Señal %s recibida; iniciando cierre limpio.", signum.name)
    stop_event.set()


def install_signal_handlers(stop_event: asyncio.Event) -> None:
    """Registra SIGINT/SIGTERM donde el loop de la plataforma lo admite."""
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, _log_signal, signum, stop_event)
        except (NotImplementedError, RuntimeError):
            if signum == signal.SIGINT:
                signal.signal(
                    signum,
                    lambda _signal_number, _frame: loop.call_soon_threadsafe(
                        stop_event.set
                    ),
                )


async def wakeword_worker(
    detector: WakeWordDetector,
    source_factory: SourceFactory,
    transcriber: Transcriber,
    command_queue: asyncio.Queue[Command | None],
    response_spoken: asyncio.Event,
) -> None:
    """Espera la wake word, transcribe una sesión y espera la respuesta TTS."""
    while True:
        logger.info("[WAKE WORD] Esperando «Che Gige»...")
        if not await detector.wait_for_keyword():
            continue

        logger.info("[WAKE WORD] Activación detectada; escuchando comando.")
        source = source_factory()
        audio_queue: asyncio.Queue[AudioFrame | None] = asyncio.Queue(maxsize=8)
        session_commands: asyncio.Queue[Command | None] = asyncio.Queue()
        capture_task = asyncio.create_task(
            capture_worker(audio_queue, source),
            name="active-command-capture",
        )
        listener_task = asyncio.create_task(
            listener_worker(audio_queue, session_commands, transcriber),
            name="active-command-listener",
        )
        try:
            await asyncio.gather(capture_task, listener_task)
            recognized = False
            while not session_commands.empty():
                command = session_commands.get_nowait()
                if command is None:
                    continue
                recognized = True
                logger.info("[WAKE WORD] Comando reconocido: %s", command.text)
                response_spoken.clear()
                await command_queue.put(
                    UserCommand(text=command.text, created_at=monotonic())
                )
                await response_spoken.wait()
            if not recognized:
                logger.info("[WAKE WORD] No se reconoció ningún comando.")
        finally:
            source.close()
            for task in (capture_task, listener_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                capture_task,
                listener_task,
                return_exceptions=True,
            )


async def run_assistant(
    detector: WakeWordDetector,
    source_factory: SourceFactory,
    transcriber: Transcriber,
    speaker: Speaker,
    action_handler: QuickAction = _logged_quick_action,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Ejecuta los workers del asistente y drena las colas al apagarse."""
    stop = stop_event or asyncio.Event()
    command_queue: asyncio.Queue[Command | None] = asyncio.Queue()
    llm_queue: asyncio.Queue[Command | None] = asyncio.Queue()
    response_queue: asyncio.Queue[Response | None] = asyncio.Queue()
    response_spoken = asyncio.Event()

    set_core_running(True)
    await event_hub.publish("system", "JARVIS Core iniciado.")
    logger.info("[SISTEMA] JARVIS Core iniciado.")

    wake_task = asyncio.create_task(
        wakeword_worker(
            detector,
            source_factory,
            transcriber,
            command_queue,
            response_spoken,
        ),
        name="wakeword-worker",
    )
    router_task = asyncio.create_task(
        router_worker(command_queue, llm_queue, response_queue, action_handler),
        name="router-worker",
    )
    brain_task = asyncio.create_task(
        brain_worker(llm_queue, response_queue),
        name="brain-worker",
    )
    tts_task = asyncio.create_task(
        speaker_worker(response_queue, speaker, response_spoken),
        name="speaker-worker",
    )
    stop_task = asyncio.create_task(stop.wait(), name="shutdown-waiter")
    workers = (wake_task, router_task, brain_task, tts_task)

    try:
        done, _ = await asyncio.wait(
            (*workers, stop_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task not in done:
            for task in done:
                if task.cancelled():
                    continue
                error = task.exception()
                if error is not None:
                    logger.error(
                        "[SISTEMA] Worker %s terminó con error: %s",
                        task.get_name(),
                        error,
                    )
                else:
                    logger.error(
                        "[SISTEMA] Worker %s terminó inesperadamente.",
                        task.get_name(),
                    )
            stop.set()
    finally:
        wake_task.cancel()
        await asyncio.gather(wake_task, return_exceptions=True)
        detector.cleanup()

        # Los sentinelas ordenan el drenado router -> cerebro -> sintetizador.
        router_failed = (
            router_task.done()
            and not router_task.cancelled()
            and router_task.exception() is not None
        )
        if router_failed:
            brain_task.cancel()
            tts_task.cancel()
        else:
            await command_queue.put(None)
            await asyncio.gather(router_task, return_exceptions=True)

            brain_failed = (
                brain_task.done()
                and not brain_task.cancelled()
                and brain_task.exception() is not None
            )
            if brain_failed:
                tts_task.cancel()
            else:
                await asyncio.gather(brain_task, return_exceptions=True)
                await response_queue.put(None)

        await asyncio.gather(brain_task, tts_task, return_exceptions=True)
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)

        set_core_running(False)
        await event_hub.publish("system", "JARVIS Core detenido.")
        await close_brain_client()
        logger.info("[SISTEMA] Recursos liberados; JARVIS detenido.")


async def main() -> None:
    """Inicia el detector, el dashboard y el asistente."""
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )
    stop_event = asyncio.Event()
    install_signal_handlers(stop_event)

    detector = WakeWordDetector()
    transcriber = VoskCommandTranscriber(detector.model, detector.sample_rate)

    def source_factory() -> AudioSource:
        return MicrophoneCommandSource(detector.sample_rate)

    server = uvicorn.Server(
        uvicorn.Config(
            "web.server:app",
            host="127.0.0.1",
            port=8000,
            log_level="warning",
        )
    )
    dashboard_task = asyncio.create_task(server.serve(), name="dashboard-server")
    assistant_task = asyncio.create_task(
        run_assistant(
            detector=detector,
            source_factory=source_factory,
            transcriber=transcriber,
            speaker=default_speaker(),
            stop_event=stop_event,
        ),
        name="jarvis-assistant",
    )
    shutdown_task = asyncio.create_task(
        stop_event.wait(),
        name="dashboard-shutdown-waiter",
    )
    try:
        done, _ = await asyncio.wait(
            (assistant_task, dashboard_task, shutdown_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if dashboard_task in done and not stop_event.is_set():
            logger.error("[SISTEMA] El servidor del dashboard terminó inesperadamente.")
            stop_event.set()
        if assistant_task in done and not stop_event.is_set():
            logger.error("[SISTEMA] El asistente terminó inesperadamente.")
            stop_event.set()
    finally:
        stop_event.set()
        server.should_exit = True
        shutdown_task.cancel()
        results = await asyncio.gather(
            assistant_task,
            dashboard_task,
            shutdown_task,
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                raise result


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("[SISTEMA] Interrupción de teclado recibida.")
