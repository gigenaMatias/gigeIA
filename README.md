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
.\venv\Scripts\Activate.ps1
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
en `C:\xampp`; se pueden cambiar con `XAMPP_APACHE_START`, `XAMPP_APACHE_STOP`,
`XAMPP_MYSQL_START` y `XAMPP_MYSQL_STOP`.
