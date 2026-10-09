"""Control asíncrono de Apache y MySQL instalados con XAMPP en Windows."""

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path


class XAMPPControlError(RuntimeError):
    """Error ejecutable al controlar un servicio de XAMPP."""


@dataclass(frozen=True)
class XAMPPConfig:
    """Rutas y límites configurables de una instalación local de XAMPP."""

    apache_start: Path = Path(r"C:\xampp\apache_start.bat")
    apache_stop: Path = Path(r"C:\xampp\apache_stop.bat")
    mysql_start: Path = Path(r"C:\xampp\mysql_start.bat")
    mysql_stop: Path = Path(r"C:\xampp\mysql_stop.bat")
    command_timeout: float = 15.0
    port_timeout: float = 0.5


_config = XAMPPConfig(
    apache_start=Path(
        os.environ.get("XAMPP_APACHE_START", r"C:\xampp\apache_start.bat")
    ),
    apache_stop=Path(
        os.environ.get("XAMPP_APACHE_STOP", r"C:\xampp\apache_stop.bat")
    ),
    mysql_start=Path(
        os.environ.get("XAMPP_MYSQL_START", r"C:\xampp\mysql_start.bat")
    ),
    mysql_stop=Path(
        os.environ.get("XAMPP_MYSQL_STOP", r"C:\xampp\mysql_stop.bat")
    ),
)


def configure_xampp(config: XAMPPConfig) -> None:
    """Establece las rutas y tiempos límite para las siguientes operaciones."""
    global _config
    _config = config


async def _run_batch(batch_path: Path, *, detached: bool) -> str:
    if os.name != "nt":
        raise XAMPPControlError("El control por archivos .bat requiere Windows.")
    if not batch_path.is_file():
        raise XAMPPControlError(f"No se encontró el script de XAMPP: {batch_path}")

    if detached:
        # El comando start deja que Apache/MySQL continúe sin retener este worker.
        command = f'start "" /b "{batch_path}"'
        arguments = ["cmd.exe", "/d", "/c", command]
    else:
        arguments = ["cmd.exe", "/d", "/c", str(batch_path)]

    try:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=_config.command_timeout,
        )
    except asyncio.TimeoutError as error:
        if "process" in locals() and process.returncode is None:
            process.kill()
            await process.wait()
        raise XAMPPControlError(
            f"Tiempo de espera agotado ejecutando {batch_path}."
        ) from error
    except OSError as error:
        raise XAMPPControlError(
            f"No se pudo ejecutar {batch_path}: {error}"
        ) from error

    output = (stdout or stderr).decode(errors="replace").strip()
    if process.returncode != 0:
        raise XAMPPControlError(
            f"El script {batch_path.name} terminó con código "
            f"{process.returncode}: {output or 'sin detalles'}"
        )
    return output


async def start_apache() -> str:
    """Inicia Apache en segundo plano y devuelve el resultado del comando."""
    output = await _run_batch(_config.apache_start, detached=True)
    return output or "Solicitud de inicio de Apache enviada."


async def stop_apache() -> str:
    """Detiene Apache mediante el script de parada de XAMPP."""
    output = await _run_batch(_config.apache_stop, detached=False)
    return output or "Solicitud de detención de Apache completada."


async def restart_apache() -> str:
    """Detiene Apache y luego solicita su inicio."""
    await stop_apache()
    return await start_apache()


async def start_mysql() -> str:
    """Inicia MySQL en segundo plano y devuelve el resultado del comando."""
    output = await _run_batch(_config.mysql_start, detached=True)
    return output or "Solicitud de inicio de MySQL enviada."


async def stop_mysql() -> str:
    """Detiene MySQL mediante el script de parada de XAMPP."""
    output = await _run_batch(_config.mysql_stop, detached=False)
    return output or "Solicitud de detención de MySQL completada."


async def _port_is_open(port: int) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port),
            timeout=_config.port_timeout,
        )
    except (OSError, asyncio.TimeoutError):
        return False

    del reader
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


async def check_service_status(service_name: str) -> bool:
    """Comprueba Apache (80/443) o MySQL (3306) por sus puertos locales."""
    service = service_name.strip().lower()
    ports = {"apache": (80, 443), "mysql": (3306,)}.get(service)
    if ports is None:
        raise ValueError("service_name debe ser 'apache' o 'mysql'.")
    statuses = await asyncio.gather(*(_port_is_open(port) for port in ports))
    return any(statuses)


if __name__ == "__main__":
    async def _smoke_test() -> None:
        for service in ("apache", "mysql"):
            running = await check_service_status(service)
            print(f"{service}: {'activo' if running else 'inactivo'}")

    try:
        asyncio.run(_smoke_test())
    except KeyboardInterrupt:
        print("\nPrueba interrumpida.")
