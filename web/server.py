"""API asíncrona y dashboard local para monitorizar JARVIS y XAMPP."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from core.events import event_hub, is_core_running
from modules.xampp_control import (
    XAMPPControlError,
    check_service_status,
    restart_apache,
    start_apache,
    start_mysql,
    stop_apache,
    stop_mysql,
)

_TEMPLATE = Path(__file__).resolve().parent / "templates" / "index.html"

app = FastAPI(title="JARVIS Dashboard", version="1.0.0")


@app.get("/", response_class=FileResponse, include_in_schema=False)
async def dashboard() -> FileResponse:
    """Sirve la página principal del dashboard."""
    return FileResponse(_TEMPLATE, media_type="text/html")


@app.get("/api/status")
async def status() -> dict[str, object]:
    """Devuelve el estado actual del core y de los puertos locales de XAMPP."""
    started = perf_counter()
    apache_running, mysql_running = await asyncio.gather(
        check_service_status("apache"),
        check_service_status("mysql"),
    )
    return {
        "jarvis_core": {
            "running": is_core_running(),
            "status": "online" if is_core_running() else "offline",
        },
        "apache": {"running": apache_running},
        "mysql": {"running": mysql_running},
        "latency_ms": round((perf_counter() - started) * 1000, 2),
    }


@app.post("/api/service/{name}/{action}")
async def control_service(name: str, action: str) -> dict[str, str]:
    """Ejecuta una acción explícita sobre Apache o MySQL."""
    service = name.strip().lower()
    operation = action.strip().lower()
    if service not in {"apache", "mysql"}:
        raise HTTPException(status_code=404, detail="Servicio no reconocido.")
    if operation not in {"start", "stop", "restart"}:
        raise HTTPException(status_code=400, detail="Acción no válida.")

    try:
        if service == "apache":
            if operation == "start":
                message = await start_apache()
            elif operation == "stop":
                message = await stop_apache()
            else:
                message = await restart_apache()
        else:
            if operation == "start":
                message = await start_mysql()
            elif operation == "stop":
                message = await stop_mysql()
            else:
                await stop_mysql()
                message = await start_mysql()
    except XAMPPControlError as error:
        await event_hub.publish("error", f"{service}: {error}")
        raise HTTPException(status_code=503, detail=str(error)) from error

    await event_hub.publish("service", f"{service}: {operation} solicitado.")
    return {"service": service, "action": operation, "message": message}


@app.get("/api/stream-logs")
async def stream_logs() -> StreamingResponse:
    """Transmite historial y eventos nuevos por Server-Sent Events."""
    stream: AsyncIterator[str] = event_hub.stream()
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
