"""Prepara un primer commit seguro sin revelar valores del archivo .env."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GITIGNORE_PATH = ROOT / ".gitignore"
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE_PATH = ROOT / ".env.example"
README_PATH = ROOT / "README.md"

REQUIRED_IGNORE_RULES = (".env", "venv/", ".venv/", "env/")

# Solo estos valores de configuración conocidos como no secretos se conservan.
# Cualquier otra variable se conserva por nombre, pero su valor se reemplaza.
SAFE_VALUE_PATTERNS = (
    re.compile(r"^OLLAMA_(BASE_URL|MODEL)$"),
    re.compile(r"^OLLAMA_TIMEOUT_SECONDS$"),
    re.compile(r"^GROQ_MODEL$"),
    re.compile(r"^GROQ_TIMEOUT_SECONDS$"),
    re.compile(r"^BRAIN_HISTORY_SIZE$"),
    re.compile(r"^BRAIN_MAX_TOKENS$"),
    re.compile(r"^WAKE_WORD$"),
    re.compile(r"^WAKE_WORD_ALTERNATIVES$"),
    re.compile(r"^WAKE_WORD_SAMPLE_RATE$"),
    re.compile(r"^WAKE_WORD_CHUNK_SIZE$"),
    re.compile(r"^WAKE_WORD_SENSITIVITY$"),
    re.compile(r"^VOSK_MODEL_PATH$"),
    re.compile(r"^XAMPP_(APACHE|MYSQL)_(START|STOP)$"),
)
SENSITIVE_NAME = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|PRIVATE|AUTH)",
    re.IGNORECASE,
)
EMBEDDED_SECRET = re.compile(
    r"(://[^/\s:@]+:[^@\s]+@|\b(?:sk-|gsk_|gh[pousr]_|xox[baprs]-)"
    r"[A-Za-z0-9_-]{8,})",
    re.IGNORECASE,
)

DEFAULT_ENV_EXAMPLE = """\
# Copia este archivo como .env y configura solo valores locales.
GROQ_API_KEY=tu_api_key_aqui
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:3b
OLLAMA_TIMEOUT_SECONDS=4
GROQ_MODEL=llama-3.1-8b-instant
GROQ_TIMEOUT_SECONDS=8
BRAIN_HISTORY_SIZE=5
WAKE_WORD=che gige
WAKE_WORD_ALTERNATIVES=che gige,che yige,che jige
WAKE_WORD_SAMPLE_RATE=16000
WAKE_WORD_CHUNK_SIZE=4000
WAKE_WORD_SENSITIVITY=0.65
VOSK_MODEL_PATH=models/vosk-model-small-es-0.42
"""

README_CONTENT = """\
# JARVIS - Asistente Virtual "Che Gige"

Asistente modular para Windows y XAMPP, con procesamiento asíncrono de voz,
activación local por palabra clave, enrutamiento de acciones y dashboard web.

## Arquitectura

- `main.py`: coordina las tareas asyncio, la señal de cierre y el dashboard.
- `core/wakeword.py`: detección local de "Che Gige" con Vosk.
- `core/listener.py`: captura y transcripción de comandos tras la activación.
- `core/router.py` y `modules/actions.py`: ejecutan acciones locales permitidas
  o derivan consultas al cerebro.
- `core/brain.py`: prioriza Ollama y usa Groq como fallback configurable.
- `core/speaker.py`: salida de voz del sistema.
- `modules/xampp_control.py` y `modules/mysql_db.py`: integración con XAMPP y
  consultas MySQL.
- `web/server.py`: API FastAPI y dashboard en `http://127.0.0.1:8000`.
- `asyncio.Queue` conecta la captura, el enrutamiento y la respuesta de voz.

## Requisitos

- Python 3.10 o posterior.
- Git.
- XAMPP en Windows para controlar Apache/MySQL.
- Un dispositivo de entrada de audio.
- Modelo acústico español Vosk descargado localmente para Wake Word/STT.

## Instalación

En PowerShell desde la raíz del proyecto:

```powershell
python -m venv venv
.\\venv\\Scripts\\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

PyAudio puede requerir una wheel compatible con la versión de Python y Windows.
Descarga un modelo español de Vosk y extráelo en `models/`; por defecto se
espera `models/vosk-model-small-es-0.42`. También puedes cambiar `VOSK_MODEL_PATH`.

Copia `.env.example` a `.env` y agrega localmente `GROQ_API_KEY` si usarás el
fallback remoto. No compartas ni commits `.env`; `.gitignore` lo excluye.

## Uso

```powershell
python main.py
```

Di "Che Gige" para habilitar la captura del comando. El dashboard está disponible
en `http://127.0.0.1:8000`. Usa Ctrl+C para apagar y liberar los recursos.

El control de servicios utiliza scripts `.bat` de XAMPP configurados por defecto
en `C:\\xampp`; se pueden cambiar con `XAMPP_APACHE_START`, `XAMPP_APACHE_STOP`,
`XAMPP_MYSQL_START` y `XAMPP_MYSQL_STOP`.

## Preparación del primer commit

```powershell
python setup_first_commit.py
git init
git add .
git status --short
```

Antes del commit, confirma que `.env` y los entornos virtuales no aparecen en
`git status` y que el ejemplo no contiene credenciales reales.
"""


def run_git(*arguments: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    """Ejecuta Git en la raíz del proyecto sin imprimir datos de configuración."""
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as error:
        raise RuntimeError("No se encontró Git en PATH.") from error
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Falló un comando de Git.")
    return result


def _is_git_repository() -> bool:
    return run_git("rev-parse", "--is-inside-work-tree").returncode == 0


def ensure_gitignore() -> None:
    """Crea .gitignore o añade las reglas esenciales sin borrar las existentes."""
    existing = GITIGNORE_PATH.read_text(encoding="utf-8") if GITIGNORE_PATH.exists() else ""
    present = {line.strip() for line in existing.splitlines()}
    missing = [rule for rule in REQUIRED_IGNORE_RULES if rule not in present]

    if not GITIGNORE_PATH.exists():
        GITIGNORE_PATH.write_text(
            "# Credentials and virtual environments\n"
            + "\n".join(REQUIRED_IGNORE_RULES)
            + "\n",
            encoding="utf-8",
        )
    elif missing:
        with GITIGNORE_PATH.open("a", encoding="utf-8", newline="\n") as gitignore:
            if existing and not existing.endswith(("\n", "\r")):
                gitignore.write("\n")
            gitignore.write("\n# Credentials and virtual environments\n")
            gitignore.write("\n".join(missing) + "\n")

    verified = {
        line.strip() for line in GITIGNORE_PATH.read_text(encoding="utf-8").splitlines()
    }
    absent = [rule for rule in REQUIRED_IGNORE_RULES if rule not in verified]
    if absent:
        raise RuntimeError(f".gitignore no protege estas rutas: {', '.join(absent)}")


def _safe_value(name: str, value: str) -> str:
    if SENSITIVE_NAME.search(name):
        return "tu_api_key_aqui" if "KEY" in name.upper() else "REEMPLAZAR_LOCALMENTE"
    if any(pattern.fullmatch(name) for pattern in SAFE_VALUE_PATTERNS):
        if EMBEDDED_SECRET.search(value):
            return "REEMPLAZAR_LOCALMENTE"
        return value
    return "REEMPLAZAR_LOCALMENTE"


def generate_env_example() -> None:
    """Crea .env.example sin copiar comentarios ni valores no autorizados."""
    if not ENV_PATH.is_file():
        ENV_EXAMPLE_PATH.write_text(DEFAULT_ENV_EXAMPLE, encoding="utf-8")
        return

    sanitized: list[str] = []
    seen_names: set[str] = set()
    for raw_line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            continue
        if name in seen_names:
            continue
        seen_names.add(name)
        sanitized.append(f"{name}={_safe_value(name, value.strip())}")

    if not sanitized:
        sanitized = DEFAULT_ENV_EXAMPLE.rstrip().splitlines()
    ENV_EXAMPLE_PATH.write_text("\n".join(sanitized) + "\n", encoding="utf-8")


def ensure_readme() -> None:
    """Crea documentación inicial solo si README.md todavía no existe."""
    if not README_PATH.exists():
        README_PATH.write_text(README_CONTENT, encoding="utf-8")


def verify_git_safety() -> None:
    """Falla antes de git add si .env está rastreado, staged o no está ignorado."""
    if not _is_git_repository():
        print(
            "[INFO] Git aún no está inicializado; verificación por patrones "
            "de .gitignore completada."
        )
        return

    tracked = run_git("ls-files", "--error-unmatch", "--", ".env")
    if tracked.returncode == 0:
        raise RuntimeError(
            ".env ya está rastreado por Git. No continúo: quitar el archivo del "
            "índice no elimina secretos del historial. Revoca credenciales y "
            "limpia el historial antes de publicar."
        )

    staged = run_git("diff", "--cached", "--name-only", "--", ".env")
    if staged.stdout.strip():
        raise RuntimeError(
            ".env está preparado en el índice de Git. Desprepáralo y revisa "
            "el historial antes de continuar."
        )

    for path in (".env", "venv/", ".venv/"):
        result = run_git("check-ignore", "--no-index", "-q", "--", path)
        if result.returncode != 0:
            raise RuntimeError(
                f"Git no confirma que {path} esté ignorado. "
                "Revisa .gitignore antes de crear el commit."
            )

    example = run_git("check-ignore", "--no-index", "-q", "--", ".env.example")
    if example.returncode == 0:
        raise RuntimeError(
            ".env.example está ignorado por Git y no se incluiría en el commit."
        )

    status = run_git("status", "--short", "--", ".env", "venv/", ".venv/")
    if status.stdout.strip():
        raise RuntimeError(
            "Git muestra .env o un entorno virtual como cambio: "
            f"{status.stdout.strip()}"
        )


def main() -> int:
    print("[SETUP] Revisando reglas de exclusión...")
    ensure_gitignore()
    print("[SETUP] Generando .env.example con valores sanitizados...")
    generate_env_example()
    print("[SETUP] Comprobando el estado de Git...")
    verify_git_safety()
    print("[SETUP] Creando README.md si no existe...")
    ensure_readme()
    print("[OK] Preparación finalizada. Revisa .env.example antes del primer commit.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as error:
        print(f"[ERROR] Preparación detenida: {error}", file=sys.stderr)
        raise SystemExit(1) from error
