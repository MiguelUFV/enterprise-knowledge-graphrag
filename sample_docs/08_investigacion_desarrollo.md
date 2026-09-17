# INVESTIGACIÓN Y DESARROLLO — PROYECTOS ACTIVOS 2026
## NexusAI Research Lab — NexusAI Solutions S.L.
### Director: Dr. Yuki Tanaka | Clasificación: CONFIDENCIAL R+D

---

## PROYECTO NEXUS-RESEARCH-001: GraphRAG Multimodal

**Estado**: EN INVESTIGACIÓN ACTIVA
**Inicio**: Enero 2026 | **Fin Estimado**: Junio 2027
**Investigadores**: Dr. Yuki Tanaka (Lead), Priya Sharma, Lena Fischer
**Presupuesto Asignado**: 280.000 EUR (de la Serie B)

### Descripción
Extensión del GraphRAG Engine actual para soportar documentos multimodales: imágenes, tablas escaneadas, diagramas arquitectónicos y figuras científicas. El objetivo es que el grafo de conocimiento incluya no solo información textual, sino también visual.

### Tecnologías en Investigación
- **LLaVA-1.6** (Large Language and Vision Assistant, Microsoft): Modelo open source para descripción de imágenes en contexto de RAG.
- **GPT-4V (OpenAI)**: API de visión como alternativa comercial cuando se requiere precisión máxima.
- **Gemini 1.5 Pro (Google)**: Especialmente promisorio para documentos de alta densidad informacional (papers científicos, informes financieros con gráficos).
- **CLIP (OpenAI)**: Para embeddings de imagen que se pueden comparar directamente con embeddings de texto.

### Hitos del Proyecto
| Hito | Fecha | Estado |
|---|---|---|
| Benchmark comparativo LLaVA vs GPT-4V vs Gemini | 30 junio 2026 | ✅ COMPLETADO |
| Integración de CLIP en ChromaDB para imágenes | 31 agosto 2026 | 🔄 EN PROGRESO |
| Prototipo de ingesta PDF con tablas y figuras | 30 septiembre 2026 | 📋 PENDIENTE |
| Prueba piloto con Pharma Nova (papers científicos) | 30 noviembre 2026 | 📋 PENDIENTE |
| Publicación en ACL 2027 | Enero 2027 | 📋 PENDIENTE |

### Resultado del Benchmark (Hito 1 — Completado)
Se evaluaron los tres modelos sobre 500 documentos de prueba (informes financieros con gráficas, papers con diagramas, contratos con tablas):

| Modelo | Precision Extracción Tablas | Latencia/doc | Coste/doc |
|---|---|---|---|
| LLaVA-1.6 (local) | 82% | 3.2s | 0.00 USD |
| GPT-4V (API) | 94% | 1.8s | 0.08 USD |
| Gemini 1.5 Pro (API) | 91% | 2.1s | 0.03 USD |

**Conclusión de Priya Sharma**: Usar Gemini 1.5 Pro como modelo por defecto (mejor balance coste/calidad) y GPT-4V para documentos críticos (facturas financieras, contratos legales).

---

## PROYECTO NEXUS-RESEARCH-002: GNN para Embeddings de Grafo

**Estado**: EN DESARROLLO ACTIVO (aprobado en Comité Ejecutivo de agosto 2026)
**Inicio**: 1 septiembre 2026 | **Fin Estimado**: 28 febrero 2027
**Investigadores**: Ahmed Al-Rashid (Lead), Lena Fischer
**Presupuesto Asignado**: 120.000 EUR (incluye costes de compute en GCP)

### Descripción
Reemplazar los embeddings de texto actuales (text-embedding-3-large de OpenAI) con embeddings que incorporen la estructura del grafo de conocimiento. Los Graph Neural Networks (GNN) aprenden representaciones vectoriales de nodos que capturan no solo el contenido del nodo, sino también su posición topológica en el grafo (vecinos, comunidades, importancia PageRank).

### Arquitectura Propuesta
- **Modelo Base**: GraphSAGE (Graph SAmple and agGrEgatE) — Hamilton et al.
- **Framework**: PyTorch Geometric (PyG) 2.x
- **Entrenamiento**: GCP Vertex AI con GPUs A100 (presupuesto compute: 8.000 USD/mes)
- **Datos de Entrenamiento**: Grafo de conocimiento de producción de NexusAI (anonimizado para clientes)
- **Métricas de Evaluación**: MRR@10, NDCG@10, Recall@5 (comparado con embeddings actuales)

### Mejora Esperada vs Estado Actual
| Métrica RAGAS | Actual (text-embedding-3-large) | Proyectado (GNN) |
|---|---|---|
| Context Recall | 0.89 | 0.95 (+6.7%) |
| Context Precision | 0.87 | 0.92 (+5.7%) |
| Answer Relevancy | 0.91 | 0.94 (+3.3%) |

---

## PROYECTO NEXUS-RESEARCH-003: RLHF para Respuestas Empresariales

**Estado**: INVESTIGACIÓN PRELIMINAR
**Inicio**: Septiembre 2026 | **Fin Estimado**: Diciembre 2027
**Investigadores**: Dr. Yuki Tanaka (Lead), María José Fernández
**Presupuesto**: Por determinar (incluir en presupuesto Serie C)

### Descripción
Implementar un pipeline de Reinforcement Learning from Human Feedback (RLHF) para fine-tunear el modelo de síntesis de respuestas del GraphRAG Engine. El objetivo es que las respuestas sean más precisas y alineadas con el dominio empresarial específico de cada cliente (finanzas, salud, legal).

### Enfoque Técnico
1. **Recolección de Datos**: Construir dataset de (query, contexto, respuesta_buena, respuesta_mala) a partir de feedback de clientes reales.
2. **Reward Model**: Entrenar un modelo de evaluación de calidad de respuestas (similar a RLHF de ChatGPT).
3. **Fine-tuning**: Aplicar PPO (Proximal Policy Optimization) sobre un modelo base open source (Llama 3.1 70B o Mistral Large).
4. **Evaluación**: Comparar el modelo fine-tuned vs Claude 3.5 Sonnet en benchmarks internos.

### Colaboraciones Externas
- **Universidad Politécnica de Madrid**: Convenio de colaboración investigación firmado en mayo 2026. Acceso a 3 estudiantes de doctorado.
- **Anthropic**: Discusión preliminar sobre acceso a datos de preferencias anónimos para benchmarking (NDA firmado).

---

## PROYECTO NEXUS-RESEARCH-004: Detección de Sesgos en Contexto Empresarial

**Estado**: COMPLETADO — Publicado
**Período**: Enero - Junio 2026
**Investigadores**: María José Fernández (Lead), Dr. Yuki Tanaka
**Paper**: "Systematic Bias Detection in Enterprise LLM Deployments: A Graph-Augmented Approach" — Aceptado en ACL 2026 (Bangkok, agosto 2026)

### Hallazgos Principales
El estudio analizó 10.000 respuestas generadas por sistemas RAG en entornos empresariales de finanzas y legal, detectando los siguientes sesgos sistemáticos:

1. **Sesgo de Recencia**: Los LLMs ponderan más información reciente aunque sea menos fiable que información histórica validada. Magnitud: +23% peso a información de últimos 30 días.
2. **Sesgo de Autoridad**: Respuestas de "CEO" o "Director" son citadas más frecuentemente que de "Analista", incluso cuando el contenido factual es equivalente. Detectado en 67% de los sistemas analizados.
3. **Sesgo de Completud Aparente**: Los LLMs tienden a dar respuestas largas y detalladas aunque el contexto disponible sea insuficiente, generando alucinaciones en el 18% de los casos cuando el contexto vectorial tiene baja similitud (<0.7 cosine).

### Mitigaciones Implementadas en NexusAI GraphRAG Engine
- **Anti-recency**: Ponderación temporal equilibrada en el reranker Cohere (penalización de documentos >6 meses aplicada solo si la query no es de naturaleza temporal).
- **Anti-autoridad**: Sistema de fuentes con puntuación de fiabilidad basada en el tipo de documento (contratos > emails > notas informales).
- **Anti-alucinación**: Implementación de "abstención controlada": si la similitud del contexto es <0.65, el sistema responde "No tengo información suficiente sobre este tema" en lugar de generar una respuesta potencialmente incorrecta.

---

## PUBLICACIONES Y PAPERS RECIENTES

| Año | Título | Conferencia | Autores |
|---|---|---|---|
| 2026 | "Systematic Bias Detection in Enterprise LLM Deployments" | ACL 2026 | M.J. Fernández, Y. Tanaka |
| 2026 | "Hybrid Graph-Vector RAG: Architecture and Benchmarks" | NeurIPS 2026 (aceptado) | Y. Tanaka, A. Al-Rashid, C. Mendoza |
| 2025 | "Graph-Augmented Retrieval for Financial Document Analysis" | EMNLP 2025 | A. Al-Rashid, Y. Tanaka |
| 2025 | "PromptGuard: Efficient Prompt Injection Detection at Scale" | ACL Findings 2025 | P. Sharma, Y. Tanaka, S. Alarcón |
| 2024 | "Knowledge Graphs as Semantic Memory for Conversational AI" | ICLR 2024 | Y. Tanaka, M.J. Fernández |

---

## HERRAMIENTAS Y LIBRERÍAS OPEN SOURCE DESARROLLADAS POR NEXUSAI

### nexusai-extract (GitHub: nexusai-solutions/nexusai-extract)
- **Descripción**: Librería Python para extracción de entidades y relaciones desde texto usando LLMs.
- **Licencia**: Apache 2.0
- **Estrellas GitHub**: 1.247 ⭐
- **Mantenedor Principal**: Ana García (Data Engineer)
- **Última Release**: v0.4.2 (julio 2026)

### cypher-query-builder (GitHub: nexusai-solutions/cypher-query-builder)
- **Descripción**: DSL Python para construir queries Cypher de forma programática y segura (previene Cypher injection).
- **Licencia**: MIT
- **Estrellas GitHub**: 432 ⭐
- **Mantenedor Principal**: Javier Soto (Graph Engineer)

### llm-guardrails-toolkit (GitHub: nexusai-solutions/llm-guardrails-toolkit)
- **Descripción**: Colección de detectores de prompt injection, jailbreak y toxicidad, integrables con cualquier sistema LLM.
- **Licencia**: Apache 2.0
- **Estrellas GitHub**: 3.891 ⭐ (el más popular de NexusAI)
- **Mantenedor Principal**: Sofía Alarcón (CISO) + Tomás Herrera (Security)
- **Citado en el paper de PromptGuard (ACL 2025)**
