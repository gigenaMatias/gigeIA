"""Configuración local de audio y detección de palabra de activación."""

import os
from pathlib import Path


WAKE_WORD = os.getenv("WAKE_WORD", "che gige")
WAKE_WORD_MODEL_PATH = Path(
    os.getenv("VOSK_MODEL_PATH", "models/vosk-model-small-es-0.42")
)
WAKE_WORD_SAMPLE_RATE = int(os.getenv("WAKE_WORD_SAMPLE_RATE", "16000"))
WAKE_WORD_CHUNK_SIZE = int(os.getenv("WAKE_WORD_CHUNK_SIZE", "4000"))
WAKE_WORD_SENSITIVITY = float(os.getenv("WAKE_WORD_SENSITIVITY", "0.65"))
WAKE_WORD_ALTERNATIVES = tuple(
    phrase.strip().lower()
    for phrase in os.getenv(
        "WAKE_WORD_ALTERNATIVES",
        "che gige,che yige,che jige",
    ).split(",")
    if phrase.strip()
)
