from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError


class AppException(Exception):
    def __init__(self, error: str, message: str, status_code: int = 400, **context):
        self.error = error
        self.message = message
        self.status_code = status_code
        self.context = context


class NotFoundException(AppException):
    def __init__(self, message: str = "Recurso no encontrado", **context):
        super().__init__(error="not_found", message=message, status_code=404, **context)


class UnauthorizedException(AppException):
    def __init__(self, message: str = "No autorizado", **context):
        super().__init__(error="unauthorized", message=message, status_code=401, **context)


class ForbiddenException(AppException):
    def __init__(self, message: str = "Acceso denegado", **context):
        super().__init__(error="forbidden", message=message, status_code=403, **context)


class ValidationException(AppException):
    def __init__(self, message: str = "Datos inválidos", **context):
        super().__init__(error="validation_error", message=message, status_code=422, **context)


class RateLimitException(AppException):
    def __init__(self, message: str = "Demasiadas solicitudes", **context):
        super().__init__(error="rate_limited", message=message, status_code=429, **context)


class SyncUnauthorizedException(AppException):
    def __init__(self, message: str = "Secreto de sincronización inválido", **context):
        super().__init__(error="sync_unauthorized", message=message, status_code=401, **context)


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    body = {"error": exc.error, "message": exc.message}
    body.update(exc.context)
    return JSONResponse(status_code=exc.status_code, content=body)


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    detail = errors[0]["msg"] if errors else "Datos inválidos"
    loc = errors[0]["loc"] if errors else ()
    field = loc[-1] if loc else ""
    context = {"field": str(field)} if field else {}
    return JSONResponse(
        status_code=422,
        content={"error": "validation_error", "message": detail, **context},
    )