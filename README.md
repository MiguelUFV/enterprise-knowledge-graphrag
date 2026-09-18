# Enterprise Knowledge

[![tests](https://github.com/MiguelUFV/enterprise-knowledge-graphrag/actions/workflows/tests.yml/badge.svg)](https://github.com/MiguelUFV/enterprise-knowledge-graphrag/actions/workflows/tests.yml)

Asistente de conocimiento corporativo sobre GraphRAG. Una organización sube su
documentación y puede preguntar sobre ella en lenguaje natural. Cada respuesta llega
con las fuentes que la sostienen y con una medida de cuánta evidencia la respalda.

**Cuando la documentación no cubre la pregunta, el sistema se abstiene en lugar de
inventar.** Ese es el comportamiento que el proyecto intenta demostrar; el resto de
la arquitectura existe para hacerlo posible y para que sea auditable.

> Prototipo funcional. Corre en local con Docker o con Python 3.12.

---

## Por qué existe

Un RAG corriente siempre responde algo. Si el corpus no contiene la respuesta, rellena
el hueco con lo más parecido que encuentre, y lo presenta con el mismo tono seguro que
usaría con un dato verificado. En un contexto corporativo eso no es un detalle estético:
alguien toma una decisión con un dato que nadie escribió nunca.

Este proyecto persigue lo contrario. Mide la evidencia antes de generar, la reporta junto
a la respuesta, y calla cuando no la tiene.

## Cómo funciona

**Al ingerir un documento** se hacen tres cosas en paralelo: se trocea respetando su
estructura de secciones, se indexa para búsqueda semántica (ChromaDB) y léxica (BM25), y
un modelo extrae de él entidades y relaciones que van a un grafo Neo4j.

**Al preguntar**:

1. Un enrutador determinista decide cuánto esfuerzo merece la consulta: ruta directa,
   híbrida o grafo completo. Evita gastar llamadas al modelo en preguntas simples.
2. Se recuperan pasajes por vector y por BM25, se fusionan con Reciprocal Rank Fusion y
   un cross-encoder local (ONNX, sin red) los reordena por relevancia real.
3. Esa puntuación de relevancia determina el **nivel de evidencia** (A a D) mediante
   umbrales calibrados con medidas reales, sin llamar a ningún modelo.
4. Solo si hay evidencia se genera la respuesta. Un evaluador la audita después y
   confirma o rebaja el nivel.

Un guardrail de seguridad corre en paralelo a la recuperación, y nada se genera hasta que
confirma que la consulta es legítima.

### Los cuatro niveles de evidencia

| Nivel | Qué significa | Qué hace el sistema |
|-------|---------------|---------------------|
| A | La documentación sostiene la respuesta completa | Responde |
| B | Sostiene parte; falta algún dato pedido | Responde señalando lo que falta |
| C | Hay material del mismo ámbito, pero el dato concreto puede no estar | Responde avisando de que la evidencia es débil |
| D | El tema no aparece en la documentación | Se abstiene |

## Arquitectura

```
Navegador  ──►  FastAPI  ──►  Enrutador de consultas
                              │
                              ├─► ChromaDB (vectores)   ─┐
                              ├─► BM25 (léxico)          ├─► RRF ─► Cross-encoder ─► Nivel de evidencia
                              └─► Neo4j (grafo)         ─┘                                │
                                                                                          ▼
                              Guardrail ────────────────────────────────────────►  Generación ─► Auditoría
```

| Pieza | Elección | Por qué |
|---|---|---|
| API | FastAPI | Asíncrono; el trabajo bloqueante va a hilos aparte |
| Orquestación | LangGraph | El flujo tiene ramas y reintentos, no es una cadena lineal |
| Vectores | ChromaDB | Persistencia local, sin servicio externo |
| Léxico | Okapi BM25 propio | Los identificadores (`CNT-2025-018`) los encuentra el léxico, no el vector |
| Grafo | Neo4j | Las preguntas de varios saltos necesitan recorrer relaciones |
| Reordenado | FlashRank ONNX | Corre en CPU en decenas de ms y no sale de la máquina |
| Modelo | OpenRouter (gpt-4o-mini) | Intercambiable por variable de entorno |
| Voz | faster-whisper | Transcripción local |

## Puesta en marcha

### Requisitos

- Python 3.12
- Una clave de [OpenRouter](https://openrouter.ai/)
- Neo4j, opcional (sirve [Aura](https://neo4j.com/cloud/aura/), capa gratuita)

**Sin Neo4j la aplicación funciona igual**: responde con búsqueda vectorial y léxica sobre
los documentos, y la vista del grafo avisa de que no está disponible. Solo se pierden las
preguntas que necesitan recorrer relaciones.

### Instalación

```bash
git clone https://github.com/MiguelUFV/enterprise-knowledge-graphrag.git
cd enterprise-knowledge-graphrag

python -m venv venv
venv\Scripts\activate          # en Linux o macOS: source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env           # y rellena las claves
uvicorn app.main:app --reload
```

Abre `http://127.0.0.1:8000`, sube un documento y pregunta sobre él. El botón
**Cargar documentos de ejemplo** indexa el corpus ficticio de `sample_docs/` si quieres
probar sin subir nada propio.

### Con Docker

```bash
docker build -t enterprise-knowledge .
docker run -p 8000:8000 --env-file .env enterprise-knowledge
```

## Configuración

Las variables viven en `.env` (parte de `.env.example`):

| Variable | Para qué |
|---|---|
| `OPENROUTER_API_KEY` | Acceso al modelo generador |
| `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` | Conexión al grafo |
| `JWT_SECRET_KEY` | Firma de los tokens. Obligatoria en producción |
| `AUTH_REQUIRED` | `true` exige JWT en todas las rutas. Siempre activo en producción |
| `COMPANY_NAME` | Nombre que muestra la interfaz |
| `DEFAULT_TENANT_ID` | Organización por defecto en despliegues de una sola |

## Varias organizaciones

Cada organización tiene su propio corpus, grafo y caché, aislados entre sí. El
`tenant_id` viaja firmado dentro del JWT y se propaga a las cuatro capas de datos:
ChromaDB, BM25, Neo4j y la caché semántica.

Para un despliegue de una sola organización no hay nada que configurar. Para varias,
copia `tenants.example.json` a `tenants.json` y emite un token por organización:

```bash
curl -X POST localhost:8000/api/auth/token \
  -H 'Content-Type: application/json' \
  -d '{"user_id": "ana", "role": "admin", "tenant_id": "acme"}'
```

`tests/test_tenant_isolation.py` verifica el aislamiento contra Neo4j real, incluido el
caso que más fácil se rompe: dos empresas con una entidad que se llama igual.

## Seguridad

- **RBAC con JWT** en cuatro niveles (público, estándar, confidencial, administrador).
- **Guardrail de entrada** en dos fases: un filtro heurístico local instantáneo y una
  verificación con modelo que corre en paralelo a la recuperación. No se genera nada
  hasta que confirma que la consulta es legítima.
- **Cabeceras de seguridad** en cada respuesta: CSP que restringe de dónde puede cargar
  la página, `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy` y una `Permissions-Policy`
  que solo concede el micrófono (lo usa el dictado).
- **La documentación interactiva** (`/docs`, `/openapi.json`) se apaga en producción.
- **Cypher parametrizado** en todas las consultas, con los tipos de relación saneados.
- **Aislamiento entre organizaciones** verificado por tests, caché semántica incluida.
- Un administrador solo puede vaciar los datos de su propia organización.

## Pruebas

```bash
pip install -r requirements-dev.txt
pytest -q
```

Los tests que tocan Neo4j se omiten solos si la base no está accesible, así que la
integración continua (`.github/workflows/tests.yml`) los cubre sin levantar infraestructura.

Vale la pena mirar dos por lo que documentan:

- `tests/test_evidence_calibration.py` fija los umbrales de evidencia con medidas reales.
  El cross-encoder está entrenado en inglés: sobre documentos en español ordena bien pero
  comprime la escala (lo documentado puntúa 1e-4…7.7e-1, lo ajeno 0…7.9e-3). Por eso los
  cortes son valores pequeños y no porcentajes redondos. Subirlos "porque parecen bajos"
  hace que el sistema se calle ante preguntas que sí tienen respuesta.
- `tests/test_tenant_isolation.py` comprueba que ninguna organización alcanza los datos
  de otra por ninguna de las cuatro vías.

## Límites conocidos

Es un prototipo, y estas cosas están sin resolver a propósito:

- **Consultas panorámicas lentas.** Preguntas del tipo "¿qué temas cubren los documentos?"
  tardan más de 10 s porque meten muchos fragmentos en el prompt.
- **Dirección de relaciones.** Se corrige de forma determinista con los tipos de las
  entidades, pero cuando ambos extremos son del mismo tipo no hay señal para decidir y se
  respeta lo que dijo el modelo.
- **Duplicados entre idiomas.** `Berlin Facility` y `Planta de Berlín` no se unifican: la
  similitud textual no cruza idiomas.
- **Extracción de entidades sin revisión humana.** Hay filtros que descartan métricas y
  sintagmas genéricos, pero nadie valida el grafo resultante.
- **Sin control de versiones de documentos.** Volver a subir un archivo lo reindexa; no
  hay histórico.

## Estructura

```
app/
  agent/       flujo LangGraph (enrutado, recuperación, generación, auditoría)
  core/        evidencia, evaluador, guardrails, troceado, autenticación, BM25
  db/          Neo4j, ChromaDB y recuperación híbrida
  services/    ingesta, métricas, sugerencias, visualización, voz
static/        interfaz (sin framework ni compilación)
tests/         unitarios y de integración
sample_docs/   corpus ficticio para probar sin datos propios
```

## Licencia

MIT.
