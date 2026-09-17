"""
Módulo de Gestión de Secretos para Entornos de Producción.
Valida que los secretos críticos estén configurados explícitamente y no usen valores por defecto inseguros.
Centraliza la carga de credenciales sensibles para facilitar la migración futura a
Azure Key Vault, HashiCorp Vault o AWS Secrets Manager.
"""

import os
import secrets
import logging

logger = logging.getLogger(__name__)

# Valores por defecto inseguros que NUNCA deben usarse en producción
_INSECURE_DEFAULTS = {
    "nexusai_enterprise_graphrag_secret_2026",
    "secret_webhook_key_2026",
    "changeme",
    "password",
    "secret",
}

# Detectar entorno de ejecución
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
IS_PRODUCTION = ENVIRONMENT in ("production", "prod", "staging")


def _validate_secret(name: str, value: str, required_in_prod: bool = True) -> str:
    """
    Valida que un secreto no sea inseguro.
    En producción, lanza error si no está configurado o usa un valor por defecto conocido.
    En desarrollo, emite una advertencia pero permite continuar.
    """
    if not value or value.strip() == "":
        if IS_PRODUCTION and required_in_prod:
            raise ValueError(
                f"ERROR DE SEGURIDAD: La variable de entorno '{name}' es obligatoria en producción "
                f"(ENVIRONMENT={ENVIRONMENT}) pero no está configurada. "
                f"Configure un valor seguro antes de arrancar el servicio."
            )
        logger.warning(
            f"ADVERTENCIA: '{name}' no está configurada. "
            f"Usando valor por defecto (SOLO aceptable en desarrollo)."
        )
        return value

    if value.lower() in _INSECURE_DEFAULTS:
        if IS_PRODUCTION:
            raise ValueError(
                f"ERROR DE SEGURIDAD: La variable '{name}' usa un valor por defecto inseguro "
                f"('{value[:20]}...'). Genere un secreto aleatorio seguro para producción. "
                f"Ejemplo: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
            )
        logger.warning(
            f"ADVERTENCIA: '{name}' usa un valor por defecto inseguro. "
            f"Esto es aceptable SOLO en desarrollo local."
        )

    return value


def get_jwt_secret() -> str:
    """Obtiene y valida el secreto JWT."""
    raw = os.getenv("JWT_SECRET_KEY", "")
    if not raw and not IS_PRODUCTION:
        # En desarrollo, generar un secreto aleatorio por sesión
        raw = secrets.token_urlsafe(48)
        logger.info("JWT_SECRET_KEY no configurada. Generado secreto aleatorio de sesion (solo desarrollo).")
        return raw
    return _validate_secret("JWT_SECRET_KEY", raw, required_in_prod=True)


def get_webhook_api_key() -> str:
    """Obtiene y valida la API Key del Webhook."""
    raw = os.getenv("WEBHOOK_API_KEY", "")
    if not raw and not IS_PRODUCTION:
        raw = secrets.token_urlsafe(32)
        logger.info("WEBHOOK_API_KEY no configurada. Generado token aleatorio de sesion (solo desarrollo).")
        return raw
    return _validate_secret("WEBHOOK_API_KEY", raw, required_in_prod=True)


def get_neo4j_credentials() -> dict:
    """Obtiene las credenciales de Neo4j."""
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    username = os.getenv("NEO4J_USERNAME", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")

    if IS_PRODUCTION:
        if not password or password in _INSECURE_DEFAULTS:
            raise ValueError(
                "ERROR DE SEGURIDAD: NEO4J_PASSWORD debe estar configurada con un valor seguro en produccion."
            )
        if not uri.startswith("neo4j+s://") and not uri.startswith("neo4j+ssc://"):
            logger.warning(
                f"ADVERTENCIA: NEO4J_URI ({uri}) no usa protocolo cifrado (neo4j+s://). "
                f"En produccion se recomienda TLS obligatorio."
            )

    return {"uri": uri, "username": username, "password": password}
