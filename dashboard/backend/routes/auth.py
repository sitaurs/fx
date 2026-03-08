"""
dashboard/backend/routes/auth.py — JWT Authentication for Dashboard V3.

Provides:
  - POST /api/auth/login  — Login with API key or email/password
  - GET  /api/auth/me     — Get current user info from JWT
  - POST /api/auth/refresh — Refresh JWT token

Lightweight JWT implementation using PyJWT or fallback HMAC.
Does NOT modify any core trading system files.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from base64 import urlsafe_b64encode, urlsafe_b64decode
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from config.settings import DASHBOARD_API_KEY, DASHBOARD_WS_TOKEN

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# JWT secret — derived from API key or env var
_JWT_SECRET = os.environ.get("DASHBOARD_JWT_SECRET", DASHBOARD_API_KEY or "dev-secret-change-me")
_JWT_EXPIRY = 86400 * 7  # 7 days

# Simple admin credentials (from env or defaults)
_ADMIN_EMAIL = os.environ.get("DASHBOARD_ADMIN_EMAIL", "admin@fxagent.local")
_ADMIN_PASSWORD = os.environ.get("DASHBOARD_ADMIN_PASSWORD", "")


# ---------------------------------------------------------------------------
# Lightweight JWT (no PyJWT dependency required)
# ---------------------------------------------------------------------------

def _b64e(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode()

def _b64d(s: str) -> bytes:
    pad = 4 - len(s) % 4
    return urlsafe_b64decode(s + "=" * pad)

def _create_jwt(payload: dict) -> str:
    header = _b64e(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64e(json.dumps(payload).encode())
    sig_input = f"{header}.{body}".encode()
    sig = _b64e(hmac.new(_JWT_SECRET.encode(), sig_input, hashlib.sha256).digest())
    return f"{header}.{body}.{sig}"

def _verify_jwt(token: str) -> dict | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        sig_input = f"{parts[0]}.{parts[1]}".encode()
        expected = _b64e(hmac.new(_JWT_SECRET.encode(), sig_input, hashlib.sha256).digest())
        if not hmac.compare_digest(parts[2], expected):
            return None
        payload = json.loads(_b64d(parts[1]))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    email: str | None = None
    password: str | None = None
    api_key: str | None = None


class TokenResponse(BaseModel):
    token: str
    user: dict


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/login")
async def login(req: LoginRequest) -> dict:
    """Authenticate via API key or email/password."""
    authenticated = False

    # Method 1: Explicit API key login
    if req.api_key:
        if DASHBOARD_API_KEY and req.api_key == DASHBOARD_API_KEY:
            authenticated = True
        else:
            raise HTTPException(status_code=401, detail="Invalid API key")

    # Method 2: Email/password login
    elif req.email and req.password:
        if _ADMIN_PASSWORD and req.email == _ADMIN_EMAIL and req.password == _ADMIN_PASSWORD:
            authenticated = True
        elif DASHBOARD_API_KEY and req.password == DASHBOARD_API_KEY:
            # Fallback: password = API key
            authenticated = True
        else:
            raise HTTPException(status_code=401, detail="Invalid credentials")

    # Method 3: Password-only (treat as API key — UI sends password field only)
    elif req.password and not req.email:
        if DASHBOARD_API_KEY and req.password == DASHBOARD_API_KEY:
            authenticated = True
        else:
            raise HTTPException(status_code=401, detail="Invalid API key")
    else:
        raise HTTPException(status_code=400, detail="Provide api_key or email+password")

    if not authenticated:
        raise HTTPException(status_code=401, detail="Authentication failed")

    now = time.time()
    payload = {
        "sub": req.email or "api-key-user",
        "role": "admin",
        "iat": int(now),
        "exp": int(now + _JWT_EXPIRY),
    }
    token = _create_jwt(payload)

    return {
        "token": token,
        "ws_token": DASHBOARD_WS_TOKEN or "",
        "user": {
            "email": req.email or "api-key-user",
            "role": "admin",
        },
    }


@router.get("/me")
async def get_me(authorization: str | None = Header(None)) -> dict:
    """Return current user info from JWT."""
    if not authorization:
        raise HTTPException(status_code=401, detail="No authorization header")

    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
    payload = _verify_jwt(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return {
        "email": payload.get("sub", "unknown"),
        "role": payload.get("role", "viewer"),
    }


@router.post("/refresh")
async def refresh_token(authorization: str | None = Header(None)) -> dict:
    """Refresh JWT token."""
    if not authorization:
        raise HTTPException(status_code=401, detail="No authorization header")

    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
    payload = _verify_jwt(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    now = time.time()
    # Use fractional seconds to guarantee a distinct token even if
    # refresh is called within the same second as login.
    new_payload = {
        "sub": payload["sub"],
        "role": payload.get("role", "admin"),
        "iat": now,            # float — keeps sub-second precision
        "exp": int(now + _JWT_EXPIRY),
    }

    return {
        "token": _create_jwt(new_payload),
        "user": {
            "email": payload["sub"],
            "role": payload.get("role", "admin"),
        },
    }
