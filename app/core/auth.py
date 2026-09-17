"""
Módulo de Seguridad y Autenticación RBAC (Role-Based Access Control) con JWT.
Proporciona verificación de identidad, generación y validación de tokens de acceso,
y control de niveles de autorización para la API empresarial de GraphRAG.
"""

import os
import re
import time
import logging
from enum import Enum
from typing import Optional
import jwt
from fastapi import HTTPException, Header, status, Depends
from pydantic import BaseModel

from app.core.secrets import get_jwt_secret, IS_PRODUCTION

logger = logging.getLogger(__name__)

# Configuración de Seguridad JWT
# En producción, JWT_SECRET_KEY es OBLIGATORIO y no usa defaults inseguros.
# En desarrollo, se genera un secreto aleatorio por sesión si no está configurado.
JWT_SECRET_KEY = get_jwt_secret()
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))  # 24 horas por defecto

# AUTH_REQUIRED: siempre True en producción/staging; en desarrollo configurable (False por defecto)
_auth_env = os.getenv("AUTH_REQUIRED", "")
AUTH_REQUIRED = IS_PRODUCTION or _auth_env.lower() in ("true", "1", "yes")


# Inquilino usado cuando el despliegue atiende a una sola organización
DEFAULT_TENANT_ID = os.getenv("DEFAULT_TENANT_ID", "default").strip() or "default"

# Un tenant_id acaba en claves de caché, metadatos de ChromaDB y propiedades de Neo4j:
# se restringe a un identificador simple para que no pueda usarse como vector de inyección.
_TENANT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")


def normalize_tenant_id(raw: Optional[str]) -> str:
    """Devuelve un tenant_id válido, o el de por defecto si no lo es."""
    candidate = (raw or "").strip()
    if not candidate:
        return DEFAULT_TENANT_ID
    if not _TENANT_ID_PATTERN.match(candidate):
        logger.warning(f"tenant_id no válido descartado: {candidate[:40]!r}. Se usa '{DEFAULT_TENANT_ID}'.")
        return DEFAULT_TENANT_ID
    return candidate


class Role(str, Enum):
    PUBLIC = "public"
    STANDARD = "standard"
    CONFIDENTIAL = "confidential"
    ADMIN = "admin"


# Jerarquía numérica de roles para control de acceso (mayor número = mayor privilegio)
ROLE_HIERARCHY = {
    Role.PUBLIC.value: 1,
    Role.STANDARD.value: 2,
    Role.CONFIDENTIAL.value: 3,
    Role.ADMIN.value: 4
}


class TokenPayload(BaseModel):
    user_id: str
    role: str = Role.STANDARD.value
    access_level: str = "standard"
    # Organización a la que pertenece el usuario. Particiona documentos, grafo y caché:
    # nadie ve ni borra datos de otro inquilino. Va firmada dentro del JWT.
    tenant_id: str = DEFAULT_TENANT_ID
    exp: Optional[int] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    user_id: str
    role: str
    access_level: str
    tenant_id: str = DEFAULT_TENANT_ID
    expires_in_seconds: int


def create_access_token(
    user_id: str,
    role: str = Role.STANDARD.value,
    access_level: str = "standard",
    tenant_id: Optional[str] = None,
    expires_delta_minutes: Optional[int] = None
) -> str:
    """
    Genera un token JWT firmado criptográficamente con los permisos del usuario.
    """
    expire_minutes = expires_delta_minutes or ACCESS_TOKEN_EXPIRE_MINUTES
    expire_timestamp = int(time.time()) + (expire_minutes * 60)

    payload = {
        "user_id": user_id,
        "role": role,
        "access_level": access_level,
        "tenant_id": normalize_tenant_id(tenant_id),
        "exp": expire_timestamp,
        "iat": int(time.time())
    }

    encoded_jwt = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt


def verify_jwt_token(token: str) -> TokenPayload:
    """
    Verifica y decodifica un token JWT. Lanza HTTPException si es inválido o ha expirado.
    """
    try:
        payload_dict = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        payload = TokenPayload(**payload_dict)
        # Un token sin tenant_id (emitido antes del particionado) queda en el inquilino
        # por defecto; nunca se le concede acceso a los datos de otra organización.
        payload.tenant_id = normalize_tenant_id(payload.tenant_id)
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El token de autenticación ha expirado."
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticación no válido."
        )


def get_current_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_user_id: Optional[str] = Header(None, alias="X-User-ID"),
    x_access_level: Optional[str] = Header(None, alias="X-Access-Level"),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID")
) -> TokenPayload:
    """
    Dependencia FastAPI que extrae y valida la identidad, rol y organización del usuario.
    Soporta:
    1. Cabecera 'Authorization: Bearer <token>' (JWT verificado; el tenant_id va firmado).
    2. Cabeceras 'X-User-ID' / 'X-Access-Level' / 'X-Tenant-ID' SOLO si la autenticación no
       es obligatoria (desarrollo local): no están firmadas y cualquiera podría declararse
       admin de cualquier organización.
    3. Usuario local por defecto si AUTH_REQUIRED es False.
    """
    # 1. Si se provee Bearer Token, validarlo estrictamente
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]
            return verify_jwt_token(token)
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Formato de cabecera Authorization no válido. Use: 'Bearer <token>'."
            )

    # 2. Si la autenticación es obligatoria, solo se acepta JWT
    if AUTH_REQUIRED:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticación requerida. Proporcione cabecera 'Authorization: Bearer <token>'."
        )

    # 3. Cabeceras directas para pruebas locales sin SSO
    if x_user_id:
        level = (x_access_level or Role.STANDARD.value).lower()
        role = level if level in ROLE_HIERARCHY else Role.STANDARD.value
        return TokenPayload(
            user_id=x_user_id, role=role, access_level=role,
            tenant_id=normalize_tenant_id(x_tenant_id),
        )

    # 4. Modo desarrollo / anónimo por defecto
    # Se otorga ADMIN para permitir borrar documentos desde la UI localmente sin token.
    return TokenPayload(
        user_id="default_user", role=Role.ADMIN.value, access_level="admin",
        tenant_id=normalize_tenant_id(x_tenant_id),
    )


def require_role(min_role: Role):
    """
    Fábrica de dependencias para restringir endpoints según el nivel jerárquico de rol.
    """
    def role_checker(current_user: TokenPayload = Depends(get_current_user)) -> TokenPayload:
        user_rank = ROLE_HIERARCHY.get(current_user.role, 1)
        required_rank = ROLE_HIERARCHY.get(min_role.value, 1)

        if user_rank < required_rank:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permisos insuficientes. Se requiere rol '{min_role.value}' o superior (rol actual: '{current_user.role}')."
            )
        return current_user

    return role_checker
