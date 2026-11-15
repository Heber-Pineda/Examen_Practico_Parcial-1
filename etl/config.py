"""Configuración externa y separación obligatoria entre origen y destino."""

from dataclasses import dataclass
import os
import re
from typing import Mapping

LOCAL_SQL_SERVER = r"HEBER-ENOC-PINE\SQLEXPRESS"


class ConfigurationError(ValueError):
    pass


def _text(env: Mapping[str, str], name: str, default: str) -> str:
    value = env.get(name, default).strip()
    if not value or len(value) > 128 or any(c in value for c in "\r\n\x00"):
        raise ConfigurationError(f"Valor inválido en {name}.")
    return value


def _integer(env: Mapping[str, str], name: str, default: int, maximum: int) -> int:
    try:
        value = int(env.get(name, str(default)))
    except ValueError:
        raise ConfigurationError(f"{name} debe ser entero.") from None
    if not 1 <= value <= maximum:
        raise ConfigurationError(f"{name} debe estar entre 1 y {maximum}.")
    return value


def _yes_no(env: Mapping[str, str], name: str, default: str) -> str:
    value = env.get(name, default).strip().lower()
    options = {"true": "yes", "false": "no", "yes": "yes", "no": "no"}
    if value not in options:
        raise ConfigurationError(f"{name} debe ser true/false o yes/no.")
    return options[value]


@dataclass(frozen=True)
class Settings:
    source_server: str
    source_database: str
    target_server: str
    target_database: str
    driver: str
    batch_size: int
    login_timeout: int
    query_timeout: int
    encrypt: str
    trust_server_certificate: str
    environment: str = "production"

    def __post_init__(self) -> None:
        if self.encrypt != "yes":
            raise ConfigurationError("ETL_ENCRYPT debe mantener el cifrado activo (yes).")
        if self.environment not in {"local", "production"}:
            raise ConfigurationError("ETL_ENVIRONMENT debe ser local o production.")
        if self.trust_server_certificate not in {"yes", "no"}:
            raise ConfigurationError("Valor interno inválido para confianza del certificado.")
        if self.trust_server_certificate == "yes":
            if self.environment != "local":
                raise ConfigurationError("La excepción de certificado solo se permite en ETL_ENVIRONMENT=local.")
            if any(server.casefold() != LOCAL_SQL_SERVER.casefold()
                   for server in (self.source_server, self.target_server)):
                raise ConfigurationError("La excepción requiere origen y destino en la instancia local autorizada.")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        source_server = _text(env, "ETL_SOURCE_SERVER", LOCAL_SQL_SERVER)
        source_database = _text(env, "ETL_SOURCE_DATABASE", "WideWorldImporters")
        target_database = _text(env, "ETL_TARGET_DATABASE", "WideWorldImporters_Analytics")
        for name, value in [("ETL_SOURCE_DATABASE", source_database),
                            ("ETL_TARGET_DATABASE", target_database)]:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", value):
                raise ConfigurationError(f"Identificador inválido en {name}.")
        forbidden = {source_database.casefold(), "wideworldimporters", "master", "model",
                     "msdb", "tempdb"}
        if target_database.casefold() in forbidden:
            raise ConfigurationError("El destino debe ser una base analítica independiente.")
        return cls(
            source_server, source_database,
            _text(env, "ETL_TARGET_SERVER", source_server), target_database,
            _text(env, "ETL_ODBC_DRIVER", "ODBC Driver 17 for SQL Server"),
            _integer(env, "ETL_BATCH_SIZE", 500, 2000),
            _integer(env, "ETL_LOGIN_TIMEOUT", 10, 120),
            _integer(env, "ETL_QUERY_TIMEOUT", 60, 3600),
            _yes_no(env, "ETL_ENCRYPT", "yes"),
            _yes_no(env, "ETL_TRUST_SERVER_CERTIFICATE", "false"),
            environment=_text(env, "ETL_ENVIRONMENT", "production").lower(),
        )

    def connection_string(self, *, source: bool) -> str:
        # ODBC usa llaves, no comillas SQL, para proteger valores con separadores.
        def escaped(value: str) -> str:
            return "{" + value.replace("}", "}}") + "}"

        server = self.source_server if source else self.target_server
        database = self.source_database if source else self.target_database
        return (
            f"DRIVER={escaped(self.driver)};SERVER={escaped(server)};"
            f"DATABASE={escaped(database)};Trusted_Connection=yes;"
            f"Encrypt={self.encrypt};TrustServerCertificate={self.trust_server_certificate};"
            "APP={WWI Sales Raw ETL};MARS_Connection=no;"
        )

    def connection_diagnostics(self, *, source: bool) -> dict:
        # Lista permitida de opciones; nunca devolver la cadena completa ni secretos.
        def identifier(value: str) -> str:
            return value if re.fullmatch(r"[\w .,:\\-]+", value) else "<revisar valor en entorno>"

        return {
            "server": identifier(self.source_server if source else self.target_server),
            "database": identifier(self.source_database if source else self.target_database),
            "driver": identifier(self.driver), "authentication": "Windows integrated",
            "environment": self.environment,
            "encrypt": self.encrypt, "trust_server_certificate": self.trust_server_certificate,
            "login_timeout": self.login_timeout, "query_timeout": self.query_timeout,
            "autocommit": False, "readonly": source, "mars_connection": False,
            "application": "WWI Sales Raw ETL",
        }
