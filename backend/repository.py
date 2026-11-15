"""Adaptador DW de solo lectura; no conecta durante la importación ni el arranque."""

from contextlib import contextmanager
import importlib
import logging
import re


class ApiProblem(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def unavailable(exc):
    state = str(exc.args[0]) if exc.args else ""
    logging.getLogger("analytics").warning("DW read failed: type=%s sqlstate=%s", type(exc).__name__,
                                            state if re.fullmatch(r"[A-Z0-9]{5}", state) else "unavailable")
    return ApiProblem(503, "DW_UNAVAILABLE", "No fue posible consultar los datos publicados. Intenta nuevamente.")


class Reader:
    def __init__(self, connection):
        self.connection = connection

    def query(self, sql, parameters=()):
        if not sql.lstrip().upper().startswith("SELECT "):
            raise ValueError("El repositorio API admite únicamente consultas SELECT.")
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, *parameters)
            columns = [column[0].lower() for column in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        except Exception as exc:
            raise unavailable(exc) from None
        finally:
            cursor.close()


class SqlRepository:
    def __init__(self, settings):
        self.settings = settings

    @contextmanager
    def session(self):
        connection = None
        try:
            module = importlib.import_module("pyodbc")
            connection = module.connect(self.settings.connection_string(), readonly=True, autocommit=True,
                                        timeout=self.settings.login_timeout)
            connection.timeout = self.settings.query_timeout
            cursor = connection.cursor()
            try:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED;")
            finally:
                cursor.close()
            reader = Reader(connection)
            identity = reader.query("SELECT DB_NAME() AS database_name;")
            if not identity or identity[0]["database_name"].casefold() != self.settings.database.casefold():
                raise ValueError("Identidad de base API incompatible.")
        except Exception as exc:
            if connection is not None:
                connection.close()
            if isinstance(exc, ApiProblem):
                raise
            raise unavailable(exc) from None
        try:
            yield reader
        finally:
            try:
                connection.close()
            except Exception:
                logging.getLogger("analytics").warning("DW reader close failed")
