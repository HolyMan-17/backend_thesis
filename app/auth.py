import time
from typing import Optional

import httpx
from fastapi import Depends, Security, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Usuario
from app.exceptions import (
    UnauthorizedException, ForbiddenException, SyncUnauthorizedException,
)

security = HTTPBearer(auto_error=False)

_jwks_cache: dict = {}
_jwks_last_fetch: float = 0.0
JWKS_CACHE_TTL = 3600


async def _fetch_jwks() -> dict:
    global _jwks_cache, _jwks_last_fetch
    now = time.time()
    if _jwks_cache and (now - _jwks_last_fetch) < JWKS_CACHE_TTL:
        return _jwks_cache

    async with httpx.AsyncClient() as client:
        response = await client.get(settings.AUTH0_JWKS_URI)
        response.raise_for_status()
        _jwks_cache = response.json()
        _jwks_last_fetch = now
        return _jwks_cache


def _get_signing_key(token_header: dict, jwks: dict) -> str:
    rsa_key = {}
    for key in jwks.get("keys", []):
        if key["kid"] == token_header.get("kid"):
            rsa_key = key
            break

    if not rsa_key:
        raise UnauthorizedException(message="Unable to find signing key")

    from jose.jwk import construct
    constructed = construct(rsa_key)
    return constructed.to_pem().decode("utf-8")


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
    db: AsyncSession = Depends(get_db),
) -> Usuario:
    if credentials is None:
        raise UnauthorizedException(message="Authorization header missing")

    token = credentials.credentials

    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError:
        raise UnauthorizedException(message="Invalid token header")

    jwks = await _fetch_jwks()
    signing_key = _get_signing_key(unverified_header, jwks)

    try:
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=settings.AUTH0_AUDIENCE,
            issuer=settings.AUTH0_ISSUER,
        )
    except JWTError as e:
        raise UnauthorizedException(message=f"Invalid token: {str(e)}")

    auth0_id = payload.get("sub")
    if not auth0_id:
        raise UnauthorizedException(message="Token missing sub claim")

    stmt = select(Usuario).where(Usuario.auth0_id == auth0_id, Usuario.activo == True)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise ForbiddenException(message="User not found or inactive", auth0_id=auth0_id)

    from sqlalchemy import func as sa_func
    user.ultimo_acceso = sa_func.now()
    await db.commit()

    request.state.user = user

    return user


def require_scope(required_scope: str):
    async def scope_checker(
        user: Usuario = Depends(get_current_user),
    ) -> Usuario:
        return user

    return scope_checker


def verify_sync_secret(request: Request) -> None:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        token = auth_header

    if not settings.BACKEND_SYNC_SECRET or token != settings.BACKEND_SYNC_SECRET:
        raise SyncUnauthorizedException()