"""Acceso asíncrono a MySQL local mediante PyMySQL en threads de trabajo."""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_$-]+$")


class MySQLDatabaseError(RuntimeError):
    """Error de base de datos con código estable y mensaje legible."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class MySQLDependencyError(MySQLDatabaseError):
    """El driver PyMySQL no está instalado."""


@dataclass(frozen=True)
class MySQLConfig:
    """Configuración de conexión y límite de concurrencia del administrador."""

    host: str = "localhost"
    user: str = "root"
    password: str = ""
    port: int = 3306
    connect_timeout: int = 5
    read_timeout: int = 10
    write_timeout: int = 10
    max_connections: int = 5
    charset: str = "utf8mb4"


class MySQLDatabase:
    """Administrador con conexiones cortas y concurrencia limitada.

    Cada operación abre/cierra su propia conexión dentro del thread de trabajo;
    el semáforo limita el número de conexiones simultáneas sin compartir cursores
    ni conexiones entre tareas.
    """

    def __init__(self, config: MySQLConfig | None = None) -> None:
        self.config = config or MySQLConfig()
        if self.config.max_connections < 1:
            raise ValueError("max_connections debe ser al menos 1.")
        self._semaphore = asyncio.Semaphore(self.config.max_connections)

    @staticmethod
    def _driver() -> Any:
        try:
            import pymysql
        except ImportError as error:
            raise MySQLDependencyError(
                "driver_missing",
                "Falta PyMySQL. Instálalo con: pip install -r requirements.txt",
            ) from error
        return pymysql

    def _connect(self, database: str | None = None) -> Any:
        pymysql = self._driver()
        kwargs: dict[str, Any] = {
            "host": self.config.host,
            "user": self.config.user,
            "password": self.config.password,
            "port": self.config.port,
            "connect_timeout": self.config.connect_timeout,
            "read_timeout": self.config.read_timeout,
            "write_timeout": self.config.write_timeout,
            "charset": self.config.charset,
            "autocommit": True,
        }
        if database is not None:
            kwargs["database"] = database
        try:
            return pymysql.connect(**kwargs)
        except pymysql.err.OperationalError as error:
            errno = error.args[0] if error.args else None
            if errno == 1045:
                code, message = (
                    "access_denied",
                    "Credenciales MySQL incorrectas o acceso denegado.",
                )
            elif errno in {2002, 2003, 2006, 2013}:
                code, message = (
                    "connection_failed",
                    f"No se pudo conectar con MySQL en "
                    f"{self.config.host}:{self.config.port}.",
                )
            elif errno == 1049:
                code, message = (
                    "unknown_database",
                    "La base de datos solicitada no existe.",
                )
            else:
                code, message = (
                    "operational_error",
                    f"Error operativo de MySQL ({errno}): {error}",
                )
            raise MySQLDatabaseError(code, message) from error
        except pymysql.err.InterfaceError as error:
            raise MySQLDatabaseError(
                "connection_error",
                f"Error de interfaz/conexión MySQL: {error}",
            ) from error
        except pymysql.err.Error as error:
            raise MySQLDatabaseError(
                "database_error",
                f"Error de MySQL: {error}",
            ) from error
        except TimeoutError as error:
            raise MySQLDatabaseError(
                "timeout", "Se agotó el tiempo de espera al conectar con MySQL."
            ) from error

    @staticmethod
    def _validate_database_name(db_name: str) -> str:
        if not _IDENTIFIER_PATTERN.fullmatch(db_name):
            raise ValueError(
                "Nombre de base de datos inválido; use letras, números, "
                "guion bajo o guion."
            )
        return db_name

    @staticmethod
    def _validate_select(query: str) -> str:
        statement = query.strip()
        if not statement or not re.match(r"(?is)^SELECT\b", statement):
            raise ValueError("execute_query solo acepta sentencias SELECT.")
        if ";" in statement.rstrip(";"):
            raise ValueError("No se permiten múltiples sentencias SQL.")
        return statement.rstrip(";").strip()

    @staticmethod
    def _run_query(
        config_owner: "MySQLDatabase",
        sql: str,
        params: tuple[Any, ...] | None = None,
        database: str | None = None,
    ) -> list[dict[str, Any]]:
        connection = config_owner._connect(database)
        try:
            with connection.cursor(config_owner._driver().cursors.DictCursor) as cursor:
                cursor.execute(sql, params)
                return list(cursor.fetchall())
        except config_owner._driver().err.Error as error:
            errno = error.args[0] if error.args else None
            code = "query_error"
            message = f"Error ejecutando la consulta MySQL ({errno}): {error}"
            if errno == 1044:
                code, message = (
                    "access_denied",
                    "El usuario no tiene acceso a la base de datos solicitada.",
                )
            elif errno in {2006, 2013}:
                code, message = (
                    "connection_lost",
                    "La conexión MySQL se perdió durante la consulta.",
                )
            raise MySQLDatabaseError(code, message) from error
        finally:
            connection.close()

    async def _query(
        self,
        sql: str,
        params: tuple[Any, ...] | None = None,
        database: str | None = None,
    ) -> list[dict[str, Any]]:
        async with self._semaphore:
            return await asyncio.to_thread(
                self._run_query, self, sql, params, database
            )

    @staticmethod
    def _run_test_connection(owner: "MySQLDatabase") -> None:
        connection = owner._connect()
        connection.close()

    async def test_connection(self) -> bool:
        """Comprueba la conexión; errores claros se registran y devuelven False."""
        try:
            async with self._semaphore:
                await asyncio.to_thread(self._run_test_connection, self)
            return True
        except MySQLDatabaseError as error:
            logger.error("Prueba de conexión MySQL [%s]: %s", error.code, error)
            return False

    async def get_databases(self) -> list[str]:
        """Obtiene los nombres de las bases de datos visibles para el usuario."""
        rows = await self._query("SHOW DATABASES")
        return [str(next(iter(row.values()))) for row in rows]

    async def get_tables(self, db_name: str) -> list[str]:
        """Obtiene las tablas de una base de datos con nombre validado."""
        database = self._validate_database_name(db_name)
        rows = await self._query("SHOW TABLES", database=database)
        return [str(next(iter(row.values()))) for row in rows]

    async def execute_query(
        self,
        db_name: str,
        query: str,
        params: tuple[Any, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Ejecuta un SELECT parametrizado y retorna filas como diccionarios."""
        database = self._validate_database_name(db_name)
        statement = self._validate_select(query)
        return await self._query(statement, params, database)

    async def get_summary_stats(self) -> str:
        """Genera un resumen breve y apto para lectura por voz."""
        try:
            databases = await self.get_databases()
        except MySQLDatabaseError as error:
            return f"Conexión con MySQL no disponible. {error.message}"
        return (
            f"Conexión activa. Tienes {len(databases)} bases de datos registradas."
        )


_database = MySQLDatabase()


def configure_database(config: MySQLConfig) -> None:
    """Configura el administrador usado por las funciones del módulo."""
    global _database
    _database = MySQLDatabase(config)


async def test_connection() -> bool:
    """Comprueba si el servidor MySQL acepta conexiones."""
    return await _database.test_connection()


async def get_databases() -> list[str]:
    """Retorna las bases de datos disponibles."""
    return await _database.get_databases()


async def get_tables(db_name: str) -> list[str]:
    """Retorna las tablas de una base de datos."""
    return await _database.get_tables(db_name)


async def execute_query(
    db_name: str,
    query: str,
    params: tuple[Any, ...] | None = None,
) -> list[dict[str, Any]]:
    """Ejecuta una consulta SELECT parametrizada."""
    return await _database.execute_query(db_name, query, params)


async def get_summary_stats() -> str:
    """Retorna un resumen legible por voz del estado de MySQL."""
    return await _database.get_summary_stats()


if __name__ == "__main__":
    async def _smoke_test() -> None:
        print(await get_summary_stats())

    try:
        asyncio.run(_smoke_test())
    except KeyboardInterrupt:
        print("\nPrueba interrumpida.")
