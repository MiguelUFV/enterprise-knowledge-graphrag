# INCIDENTES DE SEGURIDAD Y REPORTES TÉCNICOS
## NexusAI Solutions S.L. — Sistema de Gestión de Incidentes
### Clasificación: CONFIDENCIAL — Solo acceso interno

---

## INCIDENTE INC-2026-001 — CRÍTICO (RESUELTO)

**Título**: Intento de Prompt Injection en Sistema Customer360 de BancoCentral Hispano
**Fecha de Detección**: 15 de enero de 2026 — 14:32 UTC
**Fecha de Resolución**: 15 de enero de 2026 — 17:05 UTC
**Severidad**: CRÍTICA (P1)
**Duración del Incidente**: 2 horas 33 minutos
**Sistemas Afectados**: Customer360 Agent (instancia cliente BancoCentral Hispano)

### Descripción del Incidente
A las 14:32 UTC, el sistema de monitoreo de Sofía Alarcón (CISO) detectó una serie de 47 consultas anómalas provenientes de la IP 185.220.101.47 (nodo TOR identificado) intentando extraer el system prompt del agente Customer360 desplegado para BancoCentral Hispano.

El atacante utilizó la técnica "Ignore Previous Instructions" combinada con codificación Base64 para evadir los filtros de regex estándar. 23 de los 47 intentos lograron parcialmente bypassear la primera capa de validación (regex check), pero fueron bloqueados por la segunda capa (validación semántica via PromptGuard).

### Cronología del Incidente
- **14:32** — Prometheus detecta anomalía en tasa de error del endpoint /ask (spike del 340%)
- **14:35** — PagerDuty alerta a Rodrigo Peña (SRE) y Tomás Herrera (Security Engineer)
- **14:42** — Sofía Alarcón notificada como CISO. Se activa el Procedimiento de Respuesta a Incidentes (IRP v2.3)
- **14:50** — IP 185.220.101.47 bloqueada en Cloudflare WAF (regla manual)
- **15:10** — Análisis forense de los 47 intentos. Se identifican 3 patrones de ataque distintos.
- **15:30** — Notificación a BancoCentral Hispano (contacto: Javier Morales, CISO del banco)
- **16:00** — Parche de guardrails: Se añaden 12 nuevas reglas de detección de codificación maliciosa (Base64, ROT13, Hex)
- **17:05** — Sistema restaurado al 100%. Incidente cerrado.

### Impacto
- Datos de clientes expuestos: NINGUNO (los guardrails de segunda capa actuaron correctamente)
- Tiempo de indisponibilidad parcial: 18 minutos (latencia degradada, no caída total)
- Datos del system prompt expuestos: NINGUNO (confirmado por auditoría de logs)

### Acciones Correctivas Implementadas
- Actualización de PromptGuard a versión 2.1.4 con detección de codificación ofuscada
- Integración de Cloudflare Bot Management (nivel Enterprise) para todos los clientes Enterprise
- Implementación de rate limiting más agresivo: 5 req/min por IP en /ask para clientes no autenticados
- Formación de emergencia para todo el equipo de Customer Success sobre cómo comunicar incidentes a clientes

### Personas Involucradas
- **Detector**: Rodrigo Peña (SRE) — Sistema Prometheus
- **Incident Commander**: Sofía Alarcón (CISO)
- **Technical Lead**: Tomás Herrera (Security Engineer)
- **Comunicación con Cliente**: Marco Ferraro (Head of Customer Success) + Carolina Lima (CSM)
- **Aprobación Parche**: Carlos Mendoza (CTO)

---

## INCIDENTE INC-2025-047 — ALTO (RESUELTO)

**Título**: Degradación de Performance en Neo4j AuraDB — Instancia de Producción
**Fecha de Detección**: 23 de octubre de 2025 — 09:15 UTC
**Fecha de Resolución**: 23 de octubre de 2025 — 13:40 UTC
**Severidad**: ALTA (P2)
**Duración del Incidente**: 4 horas 25 minutos
**Sistemas Afectados**: Neo4j AuraDB (instancia prod-eu-001), GraphRAG Engine, Customer360

### Descripción
La instancia principal de Neo4j AuraDB sufrió una degradación severa del rendimiento causada por una query Cypher no optimizada (falta de índice en el atributo `entity_type`). El tiempo medio de las queries pasó de 45ms a 8.200ms, afectando a todos los clientes en producción.

La query problemática fue introducida por Javier Soto (Graph Engineer) como parte de un feature de exportación de datos para el cliente MegaRetail Iberia, sin pasar por el proceso de Code Review completo (el PR tenía solo 1 aprobador en lugar de los 2 requeridos).

### Causa Raíz
Missing composite index en `(:Entity {entity_type, created_at})`. La query de exportación realizaba un full scan de todos los nodos del grafo (>2.4M nodos) en lugar de usar el índice.

### Acciones Correctivas
- Índice creado de emergencia: `CREATE INDEX entity_type_date FOR (e:Entity) ON (e.entity_type, e.created_at)`
- Actualización de las políticas de Code Review: mínimo 2 aprobadores + 1 revisión de queries Cypher por Carlos Mendoza o Javier Soto
- Implementación de Neo4j Query Log Analyzer como paso obligatorio en el pipeline CI/CD
- Post-mortem publicado internamente en Notion (espacio Engineering)

---

## INCIDENTE INC-2026-003 — MEDIO (EN SEGUIMIENTO)

**Título**: Alucinaciones Sistemáticas en Respuestas sobre Precios de Productos
**Fecha de Detección**: 5 de marzo de 2026
**Severidad**: MEDIA (P3)
**Estado**: EN SEGUIMIENTO (acción correctiva implementada, monitoreando)
**Sistemas Afectados**: Customer360 (modo sin contexto de grafo activado por error)

### Descripción
Un cliente de prueba (Tiendas Verde Orgánico) reportó que el chatbot Customer360 estaba informando precios incorrectos de productos (el sistema afirmó que el Starter Plan costaba 1.200 EUR/mes en lugar de 890 EUR/mes).

La causa raíz fue que la configuración de su instancia de Customer360 tenía el `GraphRAG context injection` desactivado por un error en el proceso de onboarding realizado por Sebastián Mora (CSM Junior). El modelo LLM estaba respondiendo únicamente desde su conocimiento paramétrico (entrenamiento), sin acceso al grafo de conocimiento actualizado.

### Acciones Correctivas
- Corrección de la configuración de la instancia de Tiendas Verde Orgánico
- Implementación de un "Health Check" post-onboarding obligatorio que verifica que el grafo está conectado y activo
- Formación adicional para Sebastián Mora sobre el proceso de verificación de onboarding
- Añadida alarma en Grafana: si el número de nodos de grafo consultados en una sesión es 0 durante más de 10 minutos, se dispara una alerta al CSM responsable

---

## VULNERABILIDADES IDENTIFICADAS EN AUDITORÍA — PENDIENTES

### VULN-2026-08-A — ALTA PRIORIDAD
- **Descripción**: Las credenciales de Neo4j AuraDB están hardcodeadas en una variable de entorno sin rotación automática (vigente desde hace 14 meses).
- **Riesgo**: Si un atacante obtiene acceso al archivo `.env`, puede acceder directamente a toda la base de datos de grafos.
- **Responsable de Resolución**: Sofía Alarcón (CISO) + Rodrigo Peña (SRE)
- **Solución Planificada**: Migración a Google Secret Manager con rotación automática cada 90 días. Fecha objetivo: 30 de septiembre de 2026.
- **Estado**: EN PROGRESO (50% completado)

### VULN-2026-08-B — MEDIA PRIORIDAD  
- **Descripción**: El endpoint `/webhook/ingest` usa un token estático (`WEBHOOK_API_KEY`) sin capacidad de revocación individual por cliente.
- **Riesgo**: Si el token se filtra, cualquier atacante puede ingestar datos maliciosos en el grafo de conocimiento.
- **Responsable de Resolución**: Pablo Ruiz (Backend Engineer) + Sofía Alarcón
- **Solución Planificada**: Implementar sistema de API Keys per-cliente con capacidad de revocación individual (OAuth2 Client Credentials Flow).
- **Estado**: PLANIFICADO para Q4 2026

### VULN-2026-08-C — BAJA PRIORIDAD
- **Descripción**: Los logs de la aplicación incluyen el texto completo de las queries de usuario, lo que puede constituir datos personales bajo RGPD.
- **Responsable**: Nuria Blanco (DPO) + Mateo Benítez (Engineering Lead)
- **Solución Planificada**: Anonimización de entidades personales en logs usando NER antes de escribir a Prometheus/Grafana.
- **Estado**: PLANIFICADO para Q1 2027

---

## MÉTRICAS DE SEGURIDAD — AGOSTO 2026

| Métrica | Valor | Objetivo |
|---|---|---|
| Incidentes P1 en los últimos 12 meses | 1 | 0 |
| Incidentes P2 en los últimos 12 meses | 3 | <5 |
| MTTR (Mean Time To Resolve) P1 | 2h 33min | <4h |
| MTTR (Mean Time To Resolve) P2 | 4h 25min | <8h |
| Vulnerabilidades críticas sin resolver | 0 | 0 |
| Vulnerabilidades altas sin resolver | 1 | 0 |
| Tiempo desde último pentest externo | 6 meses | <12 meses |
| Última actualización de dependencias | 12 agosto 2026 | Mensual |
| Cobertura de 2FA en cuentas privilegiadas | 100% | 100% |
