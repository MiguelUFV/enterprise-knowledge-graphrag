# ACTAS DE REUNIONES Y DECISIONES ESTRATÉGICAS
## NexusAI Solutions S.L. — Registro Interno de Comités
### Clasificación: CONFIDENCIAL INTERNO

---

## ACTA COMITÉ EJECUTIVO — 4 AGOSTO 2026

**Fecha**: 4 de agosto de 2026 — 10:00 a 13:30 (Madrid)
**Tipo de Reunión**: Comité Ejecutivo Mensual
**Participantes**:
- Elena Rostova (CEO) — Presidenta de la reunión
- Carlos Mendoza (CTO)
- Valentina Cruz (CFO)
- Sofía Alarcón (CISO)
- Diego Ríos (CPO)
- Isabel Moreno (VP Sales)
- Patricia Solano (Directora Legal)
- Marco Ferraro (Head of Customer Success) — Invitado especial

**Ausentes con Justificación**: Dr. Yuki Tanaka (conferencia NeurIPS en Viena)

---

### PUNTO 1: Revisión de KPIs de Julio 2026

**Valentina Cruz (CFO)** presentó los resultados financieros de julio 2026:
- Facturación julio: 820.000 EUR (objetivo: 750.000 EUR) → **+9.3% vs objetivo**
- MRR (Monthly Recurring Revenue): 785.000 EUR (nuevo máximo histórico)
- ARR Proyectado 2026: 9.420.000 EUR
- Churn rate: 1.2% (muy por debajo del objetivo del 3%)
- Cash burn mensual: 680.000 EUR
- Runway financiero actual: 19 meses sin nueva captación de fondos

**Comentario Elena Rostova**: Los resultados son excelentes. El cierre del contrato con Pharma Nova en febrero está traccionando muy bien. Debemos acelerar el pipeline del sector financiero, especialmente AltaFinanz.

---

### PUNTO 2: Estado del Pipeline Comercial

**Isabel Moreno (VP Sales)** presentó el estado del embudo comercial:
- Oportunidades activas: 23 (valor total pipeline: 2.800.000 EUR ARR)
- Propuestas enviadas en julio: 7
- Contratos cerrados en julio: 2 (Tiendas Verde Orgánico + ampliación InvestGroup)
- Oportunidades calientes (cierre estimado <90 días): AltaFinanz, Ministerio de Justicia, UNAM México

**Decisión tomada**: Elena Rostova aprobó un presupuesto de eventos de 15.000 EUR para la participación de NexusAI en el evento "FinTech Innovation Summit Madrid" (octubre 2026) como sponsor Gold, con objetivo de generar al menos 5 leads cualificados.

---

### PUNTO 3: Riesgo de No Renovación MegaRetail Iberia

**Marco Ferraro (CS)** y **Rafael Torres (KAM)** presentaron el análisis de riesgo del contrato de MegaRetail (180.000 EUR/año, renovación enero 2027):
- Competidor identificado: Cognigy (empresa alemana de conversational AI)
- Ventaja de Cognigy: UI/UX más moderna y marketplace de integraciones más amplio
- Ventaja de NexusAI: Profundidad del grafo de conocimiento, personalización más alta, soporte en español

**Plan de Retención Aprobado por Elena Rostova**:
1. Oferta de precio bloqueado durante 2 años si renuevan antes del 15 de noviembre 2026
2. Acceso gratuito a NexusAI Platform Beta durante 6 meses
3. Sesión de "Executive Briefing" con CTO Alberto García organizada por Carlos Mendoza
4. Demo personalizada de las nuevas features Q4 2026 (multi-idioma, integración Teams)

**Fecha objetivo de respuesta de MegaRetail**: 30 de noviembre 2026

---

### PUNTO 4: Decisión de Arquitectura — Migración a GNN

**Carlos Mendoza (CTO)** presentó la propuesta técnica del equipo de Research (Dr. Yuki Tanaka + Ahmed Al-Rashid) de integrar Graph Neural Networks (GNN) en el GraphRAG Engine:
- Tecnología propuesta: PyTorch Geometric + Graph SAGE para embeddings de grafo
- Mejora esperada en recall de respuestas: +23% (según benchmark interno)
- Coste de implementación: 3 meses de trabajo de Lena Fischer + Ahmed Al-Rashid
- Impacto en clientes: Ninguno durante la migración (arquitectura aditiva, no sustitutiva)

**Decisión**: APROBADA. Carlos Mendoza inicia el proyecto el 1 de septiembre 2026. Hito de validación: 30 de noviembre 2026.

---

### PUNTO 5: Proceso SOC 2 Type II

**Sofía Alarcón (CISO)** presentó el avance del proceso de certificación SOC 2 Type II:
- Auditor externo seleccionado: Schellman & Company LLC
- Período de observación: 1 julio 2026 — 31 diciembre 2026
- Controles en revisión: 87 controles (cobertura de categorías: Security, Availability, Processing Integrity)
- Controles que necesitan acción: 7 controles con evidencias pendientes
- Responsables de cerrar evidencias pendientes: Rodrigo Peña (3 controles SRE), Mateo Benítez (2 controles DevOps), Nuria Blanco (2 controles RGPD)
- Fecha estimada de reporte final: Febrero 2027

---

## REUNIÓN TÉCNICA — REVISIÓN ARQUITECTURA GRAPHRAG — 18 JULIO 2026

**Participantes**: Carlos Mendoza (CTO), Mateo Benítez, Ana García, Javier Soto, Lena Fischer, Pablo Ruiz, Rodrigo Peña
**Objetivo**: Revisar el performance del sistema tras el incidente de degradación Neo4j (INC-2025-047)

### Decisiones Técnicas Tomadas

**DEC-2026-T-01**: Implementar Neo4j Query Performance Monitoring con alertas automáticas
- **Responsable**: Rodrigo Peña
- **Fecha límite**: 30 agosto 2026
- **Herramienta**: Neo4j Aura Dashboard + alertas Prometheus

**DEC-2026-T-02**: Refactorizar el sistema de indexación de documentos para soportar chunking semántico
- **Contexto**: El chunking actual (párrafos por saltos de línea) pierde contexto semántico entre chunks
- **Solución propuesta**: Implementar `SemanticChunker` de LangChain
- **Responsable**: Ana García + Lena Fischer
- **Fecha límite**: 15 septiembre 2026

**DEC-2026-T-03**: Implementar BM25 como capa de búsqueda léxica adicional (complementando embeddings)
- **Contexto**: Las búsquedas por términos exactos (nombres propios, códigos de producto) tienen recall bajo con embeddings puros
- **Herramienta**: ElasticSearch 8.x (ya partnership activo con Elastic)
- **Responsable**: Pablo Ruiz + Javier Soto
- **Estimación**: 4 semanas de desarrollo

**DEC-2026-T-04**: Migrar el Webhook de Ingesta (`/webhook/ingest`) a autenticación OAuth2
- **Contexto**: Vulnerabilidad VULN-2026-08-B identificada por Sofía Alarcón
- **Responsable**: Pablo Ruiz
- **Fecha límite**: 31 octubre 2026

---

## REUNIÓN CONSEJO DE ADMINISTRACIÓN — 20 JUNIO 2026

**Fecha**: 20 de junio de 2026 — 09:00 a 12:00 (Madrid, Sede Principal)
**Participantes**:
- Elena Rostova (CEO / Consejera)
- Carlos Mendoza (Co-Fundador / Consejero)
- Valentina Cruz (CFO — Secretaria del Consejo)
- Alexandra Voss (NorthStar Fund — Consejera Independiente)
- Roberto Teyco (Grupo Industrial Teyco — Consejero)
- Federico Blum (Business Angels Pool — Consejero Observador)

### Resoluciones del Consejo

**RESOLUCIÓN CA-2026-01**: Aprobación del presupuesto de expansión a Brasil
- Apertura de oficina en São Paulo prevista para Q1 2027
- Presupuesto aprobado: 350.000 EUR (primeros 18 meses)
- Responsable: Andrés Castro (BDM LATAM, reubicación a São Paulo)
- Persona de nuevo contrato para LATAM: pendiente de selección

**RESOLUCIÓN CA-2026-02**: Exploración de Ronda Serie C
- Valentina Cruz (CFO) encargada de elaborar el Information Memorandum (IM) para presentar a fondos de growth equity en Q4 2026
- Objetivo de recaudación: 25.000.000 EUR
- Uso de fondos: expansión geográfica (Brasil, DACH), desarrollo de NexusAI Platform, contratación de 80 personas adicionales

**RESOLUCIÓN CA-2026-03**: Ratificación de la política de dividendos
- No se repartirán dividendos durante el ejercicio 2026 ni 2027. Los beneficios se reinvertirán en el crecimiento de la empresa.
- Aprobado por unanimidad.

---

## REUNIÓN ONE-ON-ONE: Elena Rostova — Dr. Yuki Tanaka — 10 JULIO 2026

**Temas discutidos**:
1. **Publicación en NeurIPS 2026**: El paper "Hybrid Graph-Vector RAG for Enterprise Knowledge Systems" fue aceptado para presentación oral en la conferencia NeurIPS de Viena (diciembre 2026). NexusAI es el primer autor institucional.
2. **Contratación de 2 investigadores nuevos**: Yuki Tanaka solicitó 2 posiciones de investigador para Q1 2027 (especialidades: RLHF y arquitecturas de atención eficiente). Elena aprobó incluirlo en el presupuesto de la Serie C.
3. **Open Source Strategy**: Discusión sobre si liberar el componente de extracción de entidades del GraphRAG Engine como open source para generar notoriedad en la comunidad. Elena solicitó análisis de pros/contras para el próximo Comité Ejecutivo.
