# Edge Functions - Hermes Backend

Edge Functions de Supabase para el Topic Threader.

## 📁 Estructura

```
edge-functions/
├── chat-deepseek/
│   └── index.ts      # Chat con modelo DeepSeek
├── process-topic/
│   └── index.ts      # Procesamiento de documentos
├── deploy.sh         # Script de deployment
└── README.md         # Este archivo
```

## 🚀 Deployment

### Prerrequisitos

1. Supabase CLI instalado:
```bash
npm install -g supabase
```

2. Autenticado en Supabase:
```bash
supabase login
```

### Deployar todas las funciones

```bash
cd edge-functions
chmod +x deploy.sh
./deploy.sh
```

### Deployar una función específica

```bash
./deploy.sh chat-deepseek
# o
./deploy.sh process-topic
```

## 🔧 Variables de Entorno

Configurar en Supabase Dashboard > Edge Functions > Settings:

| Variable | Descripción |
|----------|-------------|
| `HERMES_BACKEND_URL` | URL del backend Railway (default: `https://hermes-api-production-1195.up.railway.app`) |
| `HERMES_API_KEY` | API keypara autenticación con el backend |
| `DEEPSEEK_API_KEY` | API key para el modelo DeepSeek |

## 📡 Endpoints

### chat-deepseek

Chat con modelo DeepSeek.

**URL:** `POST https://zehaldntdigaiakhjasi.supabase.co/functions/v1/chat-deepseek`

**Headers:**
```json
{
  "Authorization": "Bearer <supabase_token>",
  "Content-Type": "application/json"
}
```

**Body:**
```json
{
  "messages": [
    { "role": "system", "content": "Eres un asistente..." },
    { "role": "user", "content": "Hola" }
  ],
  "temperature": 0.7,
  "max_tokens": 4096,
  "stream": false
}
```

### process-topic

Procesamiento de documentos y generación de threads.

**URL:** `https://zehaldntdigaiakhjasi.supabase.co/functions/v1/process-topic`

#### GET /limits

Obtener límites del usuario.

```bash
curl -X GET \
  https://zehaldntdigaiakhjasi.supabase.co/functions/v1/process-topic/limits \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "documents": { "current":5, "max": 20 },
  "storage": { "currentMb": 12.5, "maxMb": 299 },
  "resetDate": "2026-04-01T00:00:00.000Z"
}
```

#### POST /upload

Registrar un documento subido.

```bash
curl -X POST \
  https://zehaldntdigaiakhjasi.supabase.co/functions/v1/process-topic/upload \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "file_name": "documento.pdf",
    "file_size": 1024000,
    "file_type": "application/pdf",
    "file_url": "https://storage.supabase.co/..."
  }'
```

#### GET /documents

Listar documentos del usuario.

```bash
curl -X GET \
  https://zehaldntdigaiakhjasi.supabase.co/functions/v1/process-topic/documents \
  -H "Authorization: Bearer <token>"
```

#### POST /

Procesar un topic con documentos.

```bash
curl -X POST \
  https://zehaldntdigaiakhjasi.supabase.co/functions/v1/process-topic \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "topic_id": "uuid-optional",
    "document_ids": ["doc-uuid-1", "doc-uuid-2"],
    "content": "Texto adicional...",
    "options": {
      "tone": "professional",
      "language": "es",
      "thread_length": 5
    }
  }'
```

## 🔒 Límites Implementados

| Límite | Valor |
|--------|-------|
| Documentos por mes | 20 |
| Almacenamiento total | 299 MB |
| Tamaño máximo por archivo | 50 MB |
| Archivos permitidos | PDF, TXT, HTML, MD, DOC, DOCX |

## 🧪 Testing Local

1. Iniciar Supabase local:
```bash
supabase start
```

2. Deployar funciones localmente:
```bash
supabase functions serve
```

3. Probar endpoint:
```bash
curl -X POST http://localhost:54321/functions/v1/chat-deepseek \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "test"}]}'
```

## 📊 Monitoreo

Ver logs en tiempo real:
```bash
supabase functions logs chat-deepseek
supabase functions logs process-topic
```

## 🔗 Conexión con Backend Railway

El flujo completo:

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Frontend      │────▶│Edge Functions    │────▶│ Hermes Backend  │
│   (Vercel)      │     │ (Supabase)        │     │ (Railway)       │
└─────────────────┘     └──────────────────┘     └─────────────────┘│                               │
                               │                        │
                               ▼                        ▼
                        ┌─────────────────┐     ┌──────────────┐
                        │ SupabaseDB      │     │ DeepSeek API │
                        │ (document_      │     │ (Chat Model) │
                        │  uploads)       │     └──────────────┘
                        └─────────────────┘
```

## ⚠️ Troubleshooting

### Error: "Monthly document limit reached"

El usuario ha alcanzado el límite de 20 documentos este mes. Esperar al siguiente mes o eliminar documentos existentes.

### Error: "Storage limit reached"

El usuario ha alcanzado el límite de 299MB. Eliminar documentos para liberar espacio.

### Error: "DeepSeek API key not configured"

Configurar `DEEPSEEK_API_KEY` en las variables de entorno de Edge Functions.