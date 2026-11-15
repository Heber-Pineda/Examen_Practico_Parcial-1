"""FastAPI: autenticación, validación y endpoints de solo lectura."""

from datetime import date
import logging
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field

from .config import ApiSettings
from .domain import SalesFilters
from .forecast import ForecastService
from .repository import ApiProblem, SqlRepository
from .service import AnalyticsService
from .notifications import NotificationService
from generative_ai.assistant import AssistantService

bearer = HTTPBearer(auto_error=False)


class AssistantQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=2, max_length=1000)
    publication_id: UUID | None = None
    date_from: date | None = None
    date_to: date | None = None
    customer_id: int | None = Field(None, ge=-(2**31), lt=2**31)
    bill_to_customer_id: int | None = Field(None, ge=-(2**31), lt=2**31)
    stock_item_id: int | None = Field(None, ge=-(2**31), lt=2**31)
    is_credit_note: bool | None = None
    customer_role: Literal["buyer", "bill_to"] = "buyer"


def parse_filters(date_from: date | None = None, date_to: date | None = None,
                  customer_id: int | None = Query(None, ge=-(2**31), lt=2**31),
                  bill_to_customer_id: int | None = Query(None, ge=-(2**31), lt=2**31),
                  stock_item_id: int | None = Query(None, ge=-(2**31), lt=2**31),
                  is_credit_note: bool | None = None):
    try:
        return SalesFilters(date_from, date_to, customer_id, bill_to_customer_id, stock_item_id, is_credit_note)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "INVALID_FILTERS", "message": str(exc)}) from None


def create_app(settings=None, service=None, forecast_service=None, assistant_service=None, notifications_service=None):
    settings = settings or ApiSettings.from_env()
    service = service or AnalyticsService(SqlRepository(settings), settings.max_concurrent)
    forecast_service = forecast_service if forecast_service is not None else ForecastService(service)
    assistant_service = assistant_service if assistant_service is not None else AssistantService(service)
    notifications_service = notifications_service if notifications_service is not None else NotificationService(settings)
    app = FastAPI(title="Analítica de ventas · WideWorldImporters", version="1.0.0",
                  docs_url="/docs" if settings.environment == "local" else None,
                  redoc_url=None, openapi_url="/openapi.json" if settings.environment == "local" else None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.origins),
                       allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"], allow_credentials=False)

    def authorize(credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)]):
        if credentials is None or not settings.permits(credentials.credentials):
            raise HTTPException(401, detail={"code": "UNAUTHORIZED", "message": "La clave de acceso no es válida."},
                                headers={"WWW-Authenticate": "Bearer"})

    @app.middleware("http")
    async def headers(request: Request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                 "X-Request-ID": request.state.request_id})
        return response

    def error_response(request, status, code, message, headers=None):
        return JSONResponse(status_code=status, content={"error": {"code": code, "message": message,
                            "request_id": getattr(request.state, "request_id", None)}}, headers=headers)

    @app.exception_handler(ApiProblem)
    async def api_error(request: Request, exc: ApiProblem):
        return error_response(request, exc.status, exc.code, exc.message,
                              {"Retry-After": "60" if exc.status == 429 else "2"} if exc.status in (429, 503) else None)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "HTTP_ERROR", "message": "Solicitud no disponible."}
        return error_response(request, exc.status_code, detail["code"], detail["message"], exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return error_response(request, 422, "INVALID_FILTERS", "Revisa fechas, identificadores y paginación.")

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        logging.getLogger("analytics").error("API failed: request_id=%s type=%s", request.state.request_id, type(exc).__name__)
        return error_response(request, 503, "API_UNAVAILABLE", "No fue posible completar la consulta. Intenta nuevamente.")

    @app.get("/health")
    def health():
        return {"status": "ok", "database_checked": False}

    protected = [Depends(authorize)]

    @app.get("/api/v1/catalogs", dependencies=protected)
    def catalogs():
        return service.catalogs()

    @app.get("/api/v1/overview", dependencies=protected)
    def overview(filters: Annotated[SalesFilters, Depends(parse_filters)],
                 customer_role: Literal["buyer", "bill_to"] = "buyer", publication_id: UUID | None = None):
        return service.overview(filters, customer_role, publication_id)

    @app.get("/api/v1/sales", dependencies=protected)
    def sales(filters: Annotated[SalesFilters, Depends(parse_filters)],
              page: int = Query(1, ge=1, le=100000), page_size: int = Query(25, ge=1, le=100),
              sort: Literal["date_desc", "amount_desc"] = "date_desc", publication_id: UUID | None = None):
        return service.details(filters, page, page_size, sort, publication_id)

    @app.get("/api/v1/ml/predictions", dependencies=protected)
    def predictions(publication_id: UUID | None = None):
        return forecast_service.predictions(publication_id)

    @app.get("/api/v1/ml/metrics", dependencies=protected)
    def model_metrics(publication_id: UUID | None = None):
        return forecast_service.metrics(publication_id)

    @app.get("/api/v1/assistant/status", dependencies=protected)
    def assistant_status():
        return assistant_service.status()

    @app.post("/api/v1/assistant/ask", dependencies=protected)
    def assistant_ask(body: AssistantQuestion):
        try:
            filters = SalesFilters(body.date_from, body.date_to, body.customer_id, body.bill_to_customer_id,
                                   body.stock_item_id, body.is_credit_note)
        except ValueError:
            raise HTTPException(422, detail={"code": "INVALID_FILTERS", "message": "Revisa el intervalo de fechas."}) from None
        return assistant_service.ask(body.question, filters, body.customer_role, body.publication_id)

    @app.get("/api/v1/notifications", dependencies=protected)
    def notifications(limit: int = Query(30, ge=1, le=100)):
        return notifications_service.list(limit)

    @app.post("/api/v1/notifications/{notification_id}/read", dependencies=protected)
    def notification_read(notification_id: UUID):
        return notifications_service.mark_read(notification_id)

    return app
