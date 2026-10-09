"""Mensajes tipados que circulan entre los workers."""

from dataclasses import dataclass
from time import monotonic


@dataclass(frozen=True)
class AudioFrame:
    data: bytes
    captured_at: float

    @classmethod
    def now(cls, data: bytes) -> "AudioFrame":
        return cls(data=data, captured_at=monotonic())


@dataclass(frozen=True)
class Command:
    text: str
    created_at: float

    @classmethod
    def now(cls, text: str) -> "Command":
        return cls(text=text, created_at=monotonic())


@dataclass(frozen=True)
class Response:
    text: str
    created_at: float

    @classmethod
    def now(cls, text: str) -> "Response":
        return cls(text=text, created_at=monotonic())
