"""Acciones locales allowlist; nunca ejecuta texto arbitrario como shell."""

import re
from datetime import datetime

from modules.xampp_control import (
    XAMPPControlError,
    check_service_status,
    restart_apache,
    start_apache,
    start_mysql,
    stop_apache,
    stop_mysql,
)


async def quick_action(text: str) -> str | None:
    """Devuelve una respuesta si el texto coincide con una acción local."""
    normalized = re.sub(r"\s+", " ", text.strip().lower())

    if normalized in {"ayuda", "comandos disponibles"}:
        return (
            "Puedo decir la hora o consultar, iniciar y detener Apache y MySQL. "
            "También puedo reiniciar Apache. Los demás comandos se envían al cerebro."
        )
    if normalized in {"qué hora es", "que hora es", "dime la hora"}:
        return f"Son las {datetime.now().strftime('%H:%M:%S')}."

    match = re.fullmatch(
        r"(iniciar|detener|reiniciar|estado)\s+(apache|mysql)",
        normalized,
    )
    if match:
        action, service = match.groups()
        if action == "reiniciar" and service != "apache":
            return "La acción de reinicio solo está disponible para Apache."
        try:
            if action == "estado":
                running = await check_service_status(service)
                state = "activo" if running else "inactivo"
                return f"{service.capitalize()} está {state}."
            if action == "iniciar":
                if service == "apache":
                    return await start_apache()
                return await start_mysql()
            if action == "detener":
                if service == "apache":
                    return await stop_apache()
                return await stop_mysql()
            return await restart_apache()
        except XAMPPControlError as error:
            return f"No se pudo completar la acción sobre {service}: {error}"

    return None
