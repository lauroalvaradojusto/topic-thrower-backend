# Hermes Backend - Topic Threader

Backend Dockerizado con FastAPI, Redis y RQ para orquestación de tareas de automatización.

## Arquitectura

```
┌─────────────────────────────────────────────────────────────┐
│                     VERCEL (Frontend)                     │
│  React + Vite + TypeScript                                 │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼ HTTP + API Key
┌─────────────────────────────────────────────────────────────┐
│              HERMES API (FastAPI on :8080)              │
│  - POST /api/v1/tasks (crear tarea)                     │
│  - GET  /api/v1/tasks/{id} (estado tarea)               │
│  - POST /api/v1/tasks/{id}/cancel (cancelar)            │
│  - GET  /api/v1/queues/stats (estadísticas)             │
│  - GET  /health (healthcheck)                            │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼ Redis
┌─────────────────────────────────────────────────────────────┐
│                    RQ Queues                             │
│  - high (alta prioridad, 3600s timeout)                 │
│  - default (prioridad normal, 1800s timeout)             │
│  - low (baja prioridad, 900s timeout)                   │
└────────────────────┬────────────────────────────────────────┘
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
┌───────────┐ ┌───────────┐ ┌───────────┐
│ Worker    │ │ Worker    │ │ Worker    │
│ High      │ │ Default   │ │ Low       │
│ Priority  │ │ Priority  │ │ Priority  │
└───────────┘ └───────────┘ └───────────┘
```

## Servicios

### 1. Redis
- Base de datos en memoria para colas RQ
- Persistencia con AOF (append-only file)
- Healthcheck cada 10s

### 2. Hermes API
- API REST con FastAPI
- Puertos: 8080 (localhost only)
- Auth: API Key header (`X-API-Key`)
- Healthcheck: `/health`

### 3. Workers
- **Worker High:** Tareas críticas (3600s timeout)
- **Worker Default:** Tareas normales (1800s timeout)
- **Worker Low:** Tareas de baja prioridad (900s timeout)

### 4. RQ Dashboard (opcional)
- Interfaz web para monitorear colas
- Puertos: 9181 (localhost only)
- URL: http://localhost:9181

## Workers Implementados

| Worker | Descripción | Timeout | Ejemplo Uso |
|--------|-------------|----------|--------------|
| `process_topic` | Generar thread estructurado | 3600s | Procesar tema y crear tweets |
| `analyze_doc` | Analizar documento (PDF/HTML) | 3600s | Extraer insights de PDF |
| `publish_twitter` | Publicar thread en X | 1800s | Publicar tweets con medios |
| `delete_twitter` | Eliminar thread de X | 900s | Borrar tweets publicados |
| `chat_enhanced` | Chat con contexto largo | 1800s | Conversación multi-turno |

## Instalación

### Prerrequisitos
- Docker & Docker Compose
- Supabase project ID y service key
- API key para Hermes

### 1. Clonar y configurar
```bash
cd /opt/hermes/lovable-migrate/hermes-backend
cp .env.example .env
```

### 2. Editar .env
```bash
# Supabase
SUPABASE_URL=https://dgrtziddrgpqenppehae.supabase.co
SUPABASE_SERVICE_KEY=tu_service_key

# API Security
HERMES_API_KEY=hermes-secret-key-cambiar-en-produccion
```

### 3. Levantar servicios
```bash
docker-compose up -d
```

### 4. Verificar estado
```bash
# Ver logs
docker-compose logs -f

# Ver estado de servicios
docker-compose ps

# Healthcheck
curl http://localhost:8080/health
```

## API Endpoints

### Crear Tarea
```bash
curl -X POST http://localhost:8080/api/v1/tasks \
  -H "Content-Type: application/json" \
  -H "X-API-Key: hermes-secret-key" \
  -d '{
    "task_type": "process_topic",
    "payload": {
      "topic": "AI en 2024",
      "tone": "professional",
      "length": 5
    },
    "user_id": "user-uuid",
    "priority": "high"
  }'
```

### Obtener Estado de Tarea
```bash
curl -X GET http://localhost:8080/api/v1/tasks/{task_id} \
  -H "X-API-Key: hermes-secret-key"
```

### Cancelar Tarea
```bash
curl -X POST http://localhost:8080/api/v1/tasks/{task_id}/cancel \
  -H "X-API-Key: hermes-secret-key"
```

### Estadísticas de Colas
```bash
curl -X GET http://localhost:8080/api/v1/queues/stats \
  -H "X-API-Key: hermes-secret-key"
```

## Health Check

```bash
curl http://localhost:8080/health
```

Response:
```json
{
  "status": "healthy",
  "redis": true,
  "supabase": true,
  "workers": 3,
  "queue_depths": {
    "high": 0,
    "default": 2,
    "low": 5
  }
}
```

## Monitoreo

### RQ Dashboard
- URL: http://localhost:9181
- Muestra colas, workers, jobs en tiempo real

### Logs
```bash
# API logs
docker-compose logs -f hermes-api

# Worker logs
docker-compose logs -f worker-default

# Redis logs
docker-compose logs -f redis
```

### Stats API
```bash
curl http://localhost:8080/api/v1/queues/stats
```

## Escalado

### Agregar más workers
```bash
# Escalar worker-default a 3 instancias
docker-compose up -d --scale worker-default=3
```

### Cambiar timeouts
Editar `docker-compose.yml`:
```yaml
worker-high:
  command: python -m rq worker high --url redis://redis:6379/0 --default-timeout 7200
```

## Seguridad

### API Key
- Cambiar `HERMES_API_KEY` en producción
- Usar keys rotativas (cambiar cada 90 días)
- Nunca exponer en código o commits

### Supabase Service Key
- Guardar en `.env` (no en código)
- Usar solo para operaciones de sistema
- RLS de Supabase sigue protegiendo datos

### Network Security
- API expuesta solo en `127.0.0.1:8080`
- Usar nginx/caddy para proxy HTTPS en producción
- Whitelist IPs de Supabase

## Troubleshooting

### Workers no procesan tareas
```bash
# Verificar conexión a Redis
docker-compose exec redis redis-cli ping

# Verificar queues
docker-compose exec hermes-api python -c "from rq import Queue; import redis; r = redis.from_url('redis://redis:6379/0'); print(Queue('high', connection=r).is_empty())"
```

### API responde 503
```bash
# Verificar estado de Redis
curl http://localhost:8080/health

# Verificar logs de Redis
docker-compose logs redis
```

### Task stuck en queued
```bash
# Verificar workers conectados
docker-compose exec redis redis-cli smembers rq:workers

# Reiniciar workers
docker-compose restart worker-default
```

## Producción

### Checklist
- [ ] Cambiar `HERMES_API_KEY`
- [ ] Configurar `SUPABASE_SERVICE_KEY`
- [ ] Usar HTTPS con nginx/caddy
- [ ] Configurar backups de Redis
- [ ] Configurar logging externo (Sentry, etc)
- [ ] Configurar alertas (Prometheus, etc)
- [ ] Escalar workers según carga
- [ ] Configurar rate limiting

## Edge Functions (Supabase)

El proyecto incluye Edge Functions para Supabase:

### Estructura
```
edge-functions/
├── chat-deepseek/index.ts   # Chat con DeepSeek
├── process-topic/index.ts   # Procesamiento de documentos
├── deploy.sh                # Script de deployment
└── README.md                 # Documentación completa
```

### Deployment
```bash
cd edge-functions
chmod +x deploy.sh
./deploy.sh
```

### Variables de Entorno (Supabase Dashboard)
- `HERMES_BACKEND_URL`: https://hermes-api-production-1195.up.railway.app
- `HERMES_API_KEY`: (ver.env)
- `DEEPSEEK_API_KEY`: tu API key de DeepSeek

### Endpoints
- `POST /functions/v1/chat-deepseek` - Chat con DeepSeek
- `GET /functions/v1/process-topic/limits` - Obtener límites
- `POST /functions/v1/process-topic/upload` - Registrar documento
- `GET /functions/v1/process-topic/documents` - Listar documentos
- `POST /functions/v1/process-topic` - Procesar topic

## Base de Datos (Supabase SQL)

Ejecutar en Supabase SQL Editor:
```
supabase/migrations/001_document_uploads.sql
```

### Tablas creadas:
- `document_uploads` - Registros de documentos
- `user_document_limits` - Cache mensual de límites

### Funciones:
- `check_user_limits(uuid)` - Verificar límites
- `update_document_limits()` - Trigger automático

## Límites Implementados

| Límite | Valor |
|--------|-------|
| Documentos/mes | 20 |
| Almacenamiento total | 299 MB |
| Tamaño por archivo | 50 MB |
| Tipos permitidos | PDF, TXT, HTML, MD, DOC, DOCX |

## Flujo Completo

```
Frontend (Vercel) 
    │
    ├──▶ Edge Functions (Supabase)
    │         ├── Validación de límites
    │         ├── Registro de documentos
    │         └── Autenticación JWT
    │
    └──▶ Hermes Backend (Railway)
              ├── Colas RQ Workers
              ├── Integración DeepSeek
              └── Procesamiento asíncrono
```

## Siguiente Paso

1. ✅ Edge Functions creadas
2. ✅ SQL Schema listo
3. ✅ Validación frontend implementada
4. ⏳ Ejecutar SQL en Supabase Dashboard
5. ⏳ Deployar Edge Functions
6. ⏳ Configurar variables de entorno
7. ⏳ Integrar frontend con nuevos endpoints

## Contacto

- Issues: Reportar en GitHub repo
- Logs: `/app/logs/` en container
- Health: `http://localhost:8080/health`
