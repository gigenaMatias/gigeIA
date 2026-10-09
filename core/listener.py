"""Captura de audio y worker de transcripción."""

import asyncio
from typing import Protocol

from core.events import event_hub
from core.messages import AudioFrame, Command


class AudioSource(Protocol):
    """Adaptador de micrófono: read_block debe devolver pronto o tener timeout."""

    def read_block(self) -> bytes | None:
        ...

    def close(self) -> None:
        ...


class Transcriber(Protocol):
    def transcribe(self, audio: bytes) -> str:
        ...


async def capture_worker(
    audio_queue: asyncio.Queue[AudioFrame | None],
    source: AudioSource,
) -> None:
    """Lee el dispositivo fuera del event loop y publica bloques de audio."""
    try:
        while True:
            block = await asyncio.to_thread(source.read_block)
            if block is None:
                break
            await audio_queue.put(AudioFrame.now(block))
    finally:
        await audio_queue.put(None)


async def listener_worker(
    audio_queue: asyncio.Queue[AudioFrame | None],
    command_queue: asyncio.Queue[Command | None],
    transcriber: Transcriber,
) -> None:
    """Consume audio_queue, transcribe cada bloque y publica texto."""
    try:
        while True:
            frame = await audio_queue.get()
            if frame is None:
                break
            text = (await asyncio.to_thread(transcriber.transcribe, frame.data)).strip()
            if text:
                await event_hub.publish("command", text)
                await command_queue.put(Command.now(text))
    finally:
        await command_queue.put(None)
