"""Configuración independiente del ETL y autenticación de la demo local."""

from dataclasses import dataclass, field
import hmac
import os
import re

LOCAL_SERVER = r"HEBER-ENOC-PINE\SQLEXPRESS"


@dataclass(frozen=True, kw_only=True)
class WarehouseSettings:
    server: str = LOCAL_SERVER
    database: str = "WideWorldImporters_Analytics"
    driver: str = "ODBC Driver 17 for SQL Server"
    environment: str = "production"
    trust_certificate: bool = False
    login_timeout: int = 5
    query_timeout: int = 15
    def __post_init__(self):
        if self.environment not in {"local", "production"}:
            raise ValueError("API_ENVIRONMENT debe ser local o production.")
        if self.trust_certificate and (self.environment != "local" or self.server.casefold() != LOCAL_SERVER.casefold()):
            raise ValueError("La excepción TLS solo permite la instancia local autorizada en entorno local.")
        if (not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", self.database)
                or self.database.casefold() in {"wideworldimporters", "master", "tempdb", "model", "msdb"}):
            raise ValueError("API_SQL_DATABASE debe identificar una base analítica independiente.")
        for value in (self.server, self.driver):
            if not value or len(value) > 128 or any(c in value for c in "\r\n\0"):
                raise ValueError("Servidor o controlador API inválido.")
        if not (1 <= self.login_timeout <= 30 and 1 <= self.query_timeout <= 120):
            raise ValueError("Límites de conexión o concurrencia API inválidos.")

    @classmethod
    def from_env(cls, env=None, prefix="API"):
        env = os.environ if env is None else env
        def setting(name, default):
            return env.get(f"{prefix}_{name}", default).strip()
        trust = setting("TRUST_SERVER_CERTIFICATE", "false").lower()
        if trust not in {"true", "false", "yes", "no"}:
            raise ValueError("API_TRUST_SERVER_CERTIFICATE debe ser true/false.")
        return cls(server=setting("SQL_SERVER", LOCAL_SERVER),
                   database=setting("SQL_DATABASE", "WideWorldImporters_Analytics"),
                   driver=setting("ODBC_DRIVER", "ODBC Driver 17 for SQL Server"),
                   environment=setting("ENVIRONMENT", "production").lower(),
                   trust_certificate=trust in {"true", "yes"},
                   login_timeout=int(setting("LOGIN_TIMEOUT", "5")),
                   query_timeout=int(setting("QUERY_TIMEOUT", "15")))

    def connection_string(self):
        def escaped(value):
            return "{" + value.replace("}", "}}") + "}"
        return (f"DRIVER={escaped(self.driver)};SERVER={escaped(self.server)};"
                f"DATABASE={escaped(self.database)};Trusted_Connection=yes;Encrypt=yes;"
                f"TrustServerCertificate={'yes' if self.trust_certificate else 'no'};"
                "APP={WWI Analytics Read API};MARS_Connection=no;")


@dataclass(frozen=True)
class ApiSettings(WarehouseSettings):
    access_token: str = field(repr=False)
    max_concurrent: int = 4
    origins: tuple[str, ...] = ("http://127.0.0.1:5173", "http://localhost:5173")

    def __post_init__(self):
        super().__post_init__()
        if not 32 <= len(self.access_token) <= 512 or any(c.isspace() for c in self.access_token):
            raise ValueError("API_ACCESS_TOKEN requiere una clave de 32–512 caracteres sin espacios.")
        if not 1 <= self.max_concurrent <= 8:
            raise ValueError("Límite de concurrencia API inválido.")

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        sql = WarehouseSettings.from_env(env)
        return cls(**vars(sql), access_token=env.get("API_ACCESS_TOKEN", ""),
                   max_concurrent=int(env.get("API_MAX_CONCURRENT", "4")))

    def permits(self, token: str) -> bool:
        return hmac.compare_digest(token.encode("utf-8"), self.access_token.encode("utf-8"))
