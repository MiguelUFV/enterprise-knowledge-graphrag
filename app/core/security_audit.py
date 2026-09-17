import re

# Patrones de datos sensibles a enmascarar en la salida del LLM
_API_KEY_PATTERNS = [
    (re.compile(r"sk-or-v1-[a-zA-Z0-9]{40,}"), "[REDACTED_API_KEY]"),
    (re.compile(r"sk-[a-zA-Z0-9_-]{32,}"), "[REDACTED_API_KEY]"),
    (re.compile(r"lsv2_[a-zA-Z0-9_-]{30,}"), "[REDACTED_LANGSMITH_KEY]"),
]
_CREDIT_CARD_PATTERN = re.compile(r"\b(?:\d[ -]*?){13,16}\b")


class SecurityAuditEngine:
    """Filtro de salida para evitar fugas de información sensible (API Keys, tarjetas)."""

    def sanitize_output(self, response_text: str) -> str:
        sanitized = response_text
        for pattern, replacement in _API_KEY_PATTERNS:
            sanitized = pattern.sub(replacement, sanitized)
        return _CREDIT_CARD_PATTERN.sub("[REDACTED_CARD]", sanitized)


security_auditor = SecurityAuditEngine()
