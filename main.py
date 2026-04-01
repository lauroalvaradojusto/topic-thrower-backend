"""
Hermes Backend API para Topic Threader
Backend con FastAPI - Anthropic (primary) + DeepSeek (failover)
"""
from fastapi import FastAPI, HTTPException, Header, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
import redis
import rq
from rq import Queue
import json
import os
from datetime import datetime
import logging
import httpx
import uuid
import asyncio

from lancedb_memory import LanceDBMemory, LanceDBUnavailableError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Hermes Backend API",
    description="Backend para Topic Threader - Chat con Anthropic + DeepSeek failover",
    version="2.0.0"
)

# CORS middleware - allow all Vercel origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
HERMES_API_KEY = os.getenv("HERMES_API_KEY", "hermes-secret-key")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# Redis connection
redis_conn = None
try:
    redis_conn = redis.from_url(REDIS_URL)
    redis_conn.ping()
    logger.info(f"Connected to Redis: {REDIS_URL}")
except Exception as e:
    logger.warning(f"Redis not available: {e}")

# RQ Queues
queue_high = Queue("high", connection=redis_conn) if redis_conn else None
queue_default = Queue("default", connection=redis_conn) if redis_conn else None
queue_low = Queue("low", connection=redis_conn) if redis_conn else None

# LanceDB memory (mandatory lookup before responses)
lancedb_memory = LanceDBMemory(required=True)


def get_lancedb_system_context(user_query: str) -> Dict[str, Any]:
    try:
        return lancedb_memory.query_context(user_query, top_k=5)
    except LanceDBUnavailableError as e:
        raise HTTPException(status_code=503, detail=f"LanceDB unavailable: {str(e)}")


def compose_system_with_lancedb(user_query: str, existing_system: Optional[str] = None) -> str:
    lookup = get_lancedb_system_context(user_query)
    memory_block = (
        "[LANCEDB_CONTEXT]\n"
        f"Instance: {lookup['instance_name']}\n"
        f"Table: {lookup['table']}\n"
        f"Hits: {lookup['hits']}\n"
        f"{lookup['context']}\n"
        "[/LANCEDB_CONTEXT]"
    )

    instruction = (
        "You must consult and use the LanceDB context above before answering. "
        "If the context is insufficient, explicitly say what is missing."
    )

    if existing_system and existing_system.strip():
        return f"{existing_system.strip()}\n\n{memory_block}\n\n{instruction}"
    return f"{memory_block}\n\n{instruction}"


def persist_interaction_to_lancedb(user_id: Optional[str], user_message: str, assistant_reply: str) -> None:
    try:
        lancedb_memory.save_interaction(user_id=user_id or "global", user_message=user_message, assistant_reply=assistant_reply)
    except Exception as e:
        logger.warning(f"LanceDB save failed: {e}")


# ============ MODELS ============

class HealthResponse(BaseModel):
    status: str
    redis: bool
    supabase: bool
    lancedb: bool
    lancedb_instance: str
    workers: int = 0
    queue_depths: Dict[str, int] = {}

class TaskRequest(BaseModel):
    task_type: str
    payload: dict
    user_id: str
    priority: str = "default"
    callback_url: Optional[str] = None

class TaskResponse(BaseModel):
    task_id: str
    status: str
    message: str

class ChatMessage(BaseModel):
    role: str = Field(..., description="Role: system, user, or assistant")
    content: str = Field(..., description="Message content")

class ChatRequestStructured(BaseModel):
    """Structured request (OpenAI-compatible format)"""
    messages: List[ChatMessage] = Field(..., description="Conversation messages")
    temperature: float = Field(0.7, ge=0, le=2)
    max_tokens: int = Field(4096, ge=1, le=32768)
    stream: bool = Field(False)
    user_id: Optional[str] = None
    context: Optional[Dict[str, Any]] = None

class ChatRequestSimple(BaseModel):
    """Simple request format (what the frontend currently sends)"""
    message: str = Field(..., description="User message")
    chatId: Optional[str] = None
    userId: Optional[str] = None

class ChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[Dict[str, Any]]
    usage: Dict[str, Any]  # Changed from Dict[str, int] to avoid validation error with nested dicts
    response: Optional[str] = None  # For simple format compatibility


class MemorySaveRequest(BaseModel):
    content: str = Field(..., description="Memory content to store")
    user_id: Optional[str] = Field(default="global")
    role: str = Field(default="memory")
    source: str = Field(default="manual")


class MemorySearchRequest(BaseModel):
    query: str = Field(..., description="Query to search in LanceDB")
    top_k: int = Field(default=5, ge=1, le=20)


# ============ API KEY VALIDATION ============

def verify_api_key(key: Optional[str] = None):
    """Verify API key - allow if no key configured or if key matches"""
    if not HERMES_API_KEY or HERMES_API_KEY == "hermes-secret-key":
        # Permissive mode when using default key
        return True
    if not key:
        raise HTTPException(status_code=401, detail="API key required")
    if key != HERMES_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True


# ============ LLM PROVIDERS ============

async def call_anthropic(messages: List[Dict], system_content: Optional[str] = None, 
                          max_tokens: int = 4096, temperature: float = 0.7) -> Dict:
    """Call Anthropic API (primary)"""
    if not ANTHROPIC_API_KEY:
        raise Exception("Anthropic API key not configured")
    
    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system_content:
        payload["system"] = system_content
    
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01"
    }
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            json=payload,
            headers=headers
        )
        
        if response.status_code != 200:
            raise Exception(f"Anthropic API error {response.status_code}: {response.text[:500]}")
        
        data = response.json()
        
        content = ""
        if data.get("content") and len(data["content"]) > 0:
            content = data["content"][0].get("text", "")
        
        usage = data.get("usage", {})
        
        return {
            "id": data.get("id", f"chatcmpl-{uuid.uuid4()}"),
            "model": "claude-sonnet-4-20250514",
            "content": content,
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                # Preserve nested usage details without breaking Dict[str, Any]
                "prompt_tokens_details": usage.get("cache_read_input_tokens", 0),
            }
        }


async def call_deepseek(messages: List[Dict], system_content: Optional[str] = None,
                         max_tokens: int = 4096, temperature: float = 0.7) -> Dict:
    """Call DeepSeek API (failover) - OpenAI-compatible format"""
    if not DEEPSEEK_API_KEY:
        raise Exception("DeepSeek API key not configured")
    
    all_messages = []
    if system_content:
        all_messages.append({"role": "system", "content": system_content})
    all_messages.extend(messages)
    
    payload = {
        "model": "deepseek-chat",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": all_messages,
    }
    
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            "https://api.deepseek.com/v1/chat/completions",
            json=payload,
            headers=headers
        )
        
        if response.status_code != 200:
            raise Exception(f"DeepSeek API error {response.status_code}: {response.text[:500]}")
        
        data = response.json()
        
        content = ""
        choices = data.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")
        
        usage = data.get("usage", {})
        
        return {
            "id": data.get("id", f"chatcmpl-{uuid.uuid4()}"),
            "model": "deepseek-chat",
            "content": content,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
        }


async def chat_with_failover(messages: List[Dict], system_content: Optional[str] = None,
                              max_tokens: int = 4096, temperature: float = 0.7) -> Dict:
    """Try Anthropic first, fall back to DeepSeek on failure"""
    # Primary: Anthropic
    if ANTHROPIC_API_KEY:
        try:
            result = await call_anthropic(messages, system_content, max_tokens, temperature)
            logger.info(f"Anthropic response: {result['usage']}")
            return result
        except Exception as e:
            logger.warning(f"Anthropic failed: {e}. Falling back to DeepSeek...")
    
    # Failover: DeepSeek
    if DEEPSEEK_API_KEY:
        try:
            result = await call_deepseek(messages, system_content, max_tokens, temperature)
            logger.info(f"DeepSeek fallback response: {result['usage']}")
            return result
        except Exception as e:
            logger.error(f"DeepSeek also failed: {e}")
    
    raise HTTPException(
        status_code=503,
        detail="All LLM providers failed. Please try again later."
    )


# ============ ENDPOINTS ============

@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint"""
    redis_healthy = redis_conn is not None
    supabase_healthy = bool(SUPABASE_SERVICE_KEY)
    
    workers = 0
    if redis_healthy:
        try:
            workers = len(redis_conn.smembers('rq:workers'))
        except:
            pass
    
    queue_depths = {}
    if redis_healthy:
        for queue_name, queue in [('high', queue_high), ('default', queue_default), ('low', queue_low)]:
            try:
                queue_depths[queue_name] = len(queue)
            except:
                queue_depths[queue_name] = 0
    
    lancedb_status = lancedb_memory.status()

    return HealthResponse(
        status="healthy" if redis_healthy and lancedb_status.ready else "degraded",
        redis=redis_healthy,
        supabase=supabase_healthy,
        lancedb=lancedb_status.ready,
        lancedb_instance=lancedb_status.instance_name,
        workers=workers,
        queue_depths=queue_depths
    )


@app.get("/")
async def root():
    lancedb_status = lancedb_memory.status()

    return {
        "service": "Hermes Backend API",
        "version": "2.0.0",
        "status": "running",
        "providers": {
            "anthropic": bool(ANTHROPIC_API_KEY),
            "deepseek": bool(DEEPSEEK_API_KEY)
        },
        "lancedb": {
            "ready": lancedb_status.ready,
            "instance": lancedb_status.instance_name,
            "table": lancedb_status.table,
            "uri": lancedb_status.uri,
        },
        "timestamp": datetime.utcnow().isoformat()
    }


@app.post("/api/v1/memory/save")
async def memory_save(request: MemorySaveRequest, x_api_key: Optional[str] = Header(None)):
    """Store manual content into LanceDB memory."""
    verify_api_key(x_api_key)

    if not request.content.strip():
        raise HTTPException(status_code=400, detail="content cannot be empty")

    try:
        lancedb_memory.save_entry(
            content=request.content.strip(),
            role=request.role,
            user_id=request.user_id or "global",
            source=request.source,
        )
    except LanceDBUnavailableError as e:
        raise HTTPException(status_code=503, detail=f"LanceDB unavailable: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LanceDB save failed: {str(e)}")

    status = lancedb_memory.status()
    return {
        "ok": True,
        "stored": True,
        "instance": status.instance_name,
        "table": status.table,
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.post("/api/v1/memory/search")
async def memory_search(request: MemorySearchRequest, x_api_key: Optional[str] = Header(None)):
    """Search LanceDB memory directly."""
    verify_api_key(x_api_key)

    if not request.query.strip():
        raise HTTPException(status_code=400, detail="query cannot be empty")

    try:
        lookup = lancedb_memory.query_context(request.query.strip(), top_k=request.top_k)
    except LanceDBUnavailableError as e:
        raise HTTPException(status_code=503, detail=f"LanceDB unavailable: {str(e)}")

    return {
        "ok": True,
        "instance": lookup["instance_name"],
        "table": lookup["table"],
        "hits": lookup["hits"],
        "context": lookup["context"],
        "timestamp": datetime.utcnow().isoformat(),
    }


# ============ SIMPLE CHAT ENDPOINT (what the frontend uses) ============

@app.post("/api/v1/chat/deepseek")
async def chat_simple(request: ChatRequestSimple, x_api_key: Optional[str] = Header(None)):
    """Simple chat endpoint - compatible with frontend's current format.
    Accepts {message, chatId, userId} and returns {response, ...}
    Uses Anthropic as primary, DeepSeek as failover."""
    
    verify_api_key(x_api_key)
    
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    
    created = int(datetime.utcnow().timestamp())
    
    user_message = request.message.strip()

    # Mandatory LanceDB consult before response
    system_content = compose_system_with_lancedb(user_message)

    # Build message array for LLM
    messages = [{"role": "user", "content": user_message}]
    
    try:
        result = await chat_with_failover(
            messages=messages,
            system_content=system_content,
            max_tokens=4096,
            temperature=0.7
        )

        persist_interaction_to_lancedb(request.userId, user_message, result["content"])

        return {
            "id": result["id"],
            "object": "chat.completion",
            "created": created,
            "model": result["model"],
            "response": result["content"],
            "message": result["content"],
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": result["content"]
                },
                "finish_reason": "stop"
            }],
            "usage": result["usage"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=f"Chat error: {str(e)}")


# ============ FILE ANALYSIS CHAT ENDPOINT ============

class FileData(BaseModel):
    name: str = Field(..., description="File name")
    type: str = Field(..., description="File MIME type")
    content: str = Field(..., description="File content as base64 string")

class ChatWithFilesRequest(BaseModel):
    message: str = Field(..., description="User message")
    files: List[FileData] = Field(default=[], description="List of files to analyze")
    chatId: Optional[str] = None
    userId: str = Field(..., description="User ID")

@app.post("/api/v1/chat/analyze-file")
async def chat_with_files(request: ChatWithFilesRequest, x_api_key: Optional[str] = Header(None)):
    """Chat endpoint with file analysis - extracts text from files and includes in chat context"""
    from file_processors import process_multiple_files

    verify_api_key(x_api_key)

    if not request.message or not request.message.strip():
        if not request.files or len(request.files) == 0:
            raise HTTPException(status_code=400, detail="Message or files are required")
        request.message = "Please analyze the attached file(s)."

    # Process files if provided
    file_texts = ""
    if request.files:
        try:
            processed_files = process_multiple_files([f.dict() for f in request.files])
            for pf in processed_files:
                if 'error' in pf:
                    file_texts += f"\n[Error processing file '{pf['name']}': {pf['error']}]\n"
                else:
                    file_texts += f"\n--- File: {pf['name']} ({pf['type']}, {pf['size']} bytes) ---\n"
                    file_texts += pf['text']
                    file_texts += "\n--- End of file ---\n"
        except Exception as e:
            logger.error(f"Error processing files: {e}")
            raise HTTPException(status_code=400, detail=f"Failed to process files: {str(e)}")

    user_message = request.message.strip() if request.message and request.message.strip() else "Please analyze the attached file(s)."

    # Build message array for LLM
    base_system_content = f"""You are an AI assistant that can analyze documents. The user has provided files along with their message. Analyze the file content carefully and provide helpful insights.

{file_texts}

Focus on:
- Key information in the documents
- Data patterns, trends, or insights
- Actionable recommendations
- Answers to the user's specific questions

If you find data in the files, reference it clearly in your response."""

    system_content = compose_system_with_lancedb(user_message, existing_system=base_system_content)

    messages = [
        {"role": "user", "content": user_message}
    ]

    try:
        result = await chat_with_failover(
            messages=messages,
            system_content=system_content,
            max_tokens=4096,
            temperature=0.7
        )

        persist_interaction_to_lancedb(request.userId, user_message, result["content"])

        return {
            "id": result["id"],
            "object": "chat.completion",
            "created": int(datetime.utcnow().timestamp()),
            "model": result["model"],
            "response": result["content"],
            "message": result["content"],
            "files_processed": len(request.files),
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": result["content"]
                },
                "finish_reason": "stop"
            }],
            "usage": result["usage"]
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat with files error: {e}")
        raise HTTPException(status_code=500, detail=f"Chat with files error: {str(e)}")


# ============ STRUCTURED CHAT ENDPOINT (OpenAI-compatible) ============

@app.post("/api/v1/chat/completions")
async def chat_structured(request: ChatRequestStructured, x_api_key: Optional[str] = Header(None)):
    """Structured chat endpoint - OpenAI-compatible format.
    Accepts {messages: [{role, content}], temperature, max_tokens, ...}"""
    
    verify_api_key(x_api_key)
    
    created = int(datetime.utcnow().timestamp())
    
    # Separate system from messages
    messages = []
    system_content = None
    
    for m in request.messages:
        if m.role == "system":
            system_content = m.content
        else:
            messages.append({"role": m.role, "content": m.content})
    
    if not messages:
        raise HTTPException(status_code=400, detail="At least one user/assistant message required")

    latest_user_message = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    system_content = compose_system_with_lancedb(latest_user_message, existing_system=system_content)
    
    try:
        result = await chat_with_failover(
            messages=messages,
            system_content=system_content,
            max_tokens=request.max_tokens,
            temperature=request.temperature
        )

        persist_interaction_to_lancedb(request.user_id, latest_user_message, result["content"])
        
        return {
            "id": result["id"],
            "object": "chat.completion",
            "created": created,
            "model": result["model"],
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": result["content"]
                },
                "finish_reason": "stop"
            }],
            "usage": result["usage"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=f"Chat error: {str(e)}")


# ============ STREAMING CHAT ============

@app.post("/api/v1/chat/deepseek/stream")
async def chat_stream(request: ChatRequestSimple, x_api_key: Optional[str] = Header(None)):
    """Streaming chat endpoint with Anthropic primary + DeepSeek failover"""
    from fastapi.responses import StreamingResponse
    
    verify_api_key(x_api_key)
    
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    
    user_message = request.message.strip()
    system_content = compose_system_with_lancedb(user_message)
    messages = [{"role": "user", "content": user_message}]
    
    async def generate_stream():
        # Try Anthropic first
        if ANTHROPIC_API_KEY:
            try:
                payload = {
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 4096,
                    "system": system_content,
                    "messages": messages,
                    "stream": True
                }
                headers = {
                    "x-api-key": ANTHROPIC_API_KEY,
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01"
                }
                async with httpx.AsyncClient(timeout=120.0) as client:
                    async with client.stream("POST", "https://api.anthropic.com/v1/messages", json=payload, headers=headers) as response:
                        if response.status_code == 200:
                            async for chunk in response.aiter_bytes():
                                yield chunk
                            return
            except Exception as e:
                logger.warning(f"Anthropic stream failed: {e}")
        
        # Failover: DeepSeek streaming
        if DEEPSEEK_API_KEY:
            try:
                payload = {
                    "model": "deepseek-chat",
                    "max_tokens": 4096,
                    "messages": [{"role": "system", "content": system_content}] + messages,
                    "stream": True
                }
                headers = {
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json"
                }
                async with httpx.AsyncClient(timeout=120.0) as client:
                    async with client.stream("POST", "https://api.deepseek.com/v1/chat/completions", json=payload, headers=headers) as response:
                        async for chunk in response.aiter_bytes():
                            yield chunk
                return
            except Exception as e:
                logger.error(f"DeepSeek stream also failed: {e}")
        
        yield b'data: {"error": "All providers failed"}\n\n'
    
    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


# ============ TASK QUEUE ENDPOINTS ============

@app.post("/api/v1/tasks", response_model=TaskResponse)
async def create_task(task: TaskRequest, background_tasks: BackgroundTasks, x_api_key: Optional[str] = Header(None)):
    """Create a new task in the queue"""
    verify_api_key(x_api_key)
    
    if not redis_conn:
        raise HTTPException(status_code=503, detail="Redis not available - task queue unavailable")
    
    queue_map = {
        'high': queue_high,
        'default': queue_default,
        'low': queue_low
    }
    
    queue = queue_map.get(task.priority, queue_default)
    
    try:
        job = queue.enqueue(
            'workers.tasks.' + task.task_type,
            task.payload,
            user_id=task.user_id,
            callback_url=task.callback_url,
            job_timeout=3600 if task.priority == 'high' else 1800
        )
        
        logger.info(f"Task created: {job.id} - {task.task_type} for user {task.user_id}")
        
        return TaskResponse(
            task_id=job.id,
            status="queued",
            message=f"Task {task.task_type} queued successfully"
        )
    except Exception as e:
        logger.error(f"Failed to enqueue task: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to enqueue task: {str(e)}")


@app.get("/api/v1/tasks/{task_id}")
async def get_task_status(task_id: str, x_api_key: Optional[str] = Header(None)):
    """Get task status"""
    verify_api_key(x_api_key)
    
    if not redis_conn:
        raise HTTPException(status_code=503, detail="Redis not available")
    
    for queue in [queue_high, queue_default, queue_low]:
        if not queue:
            continue
        try:
            job = queue.fetch_job(task_id)
            if job:
                return {
                    "task_id": job.id,
                    "status": job.get_status(),
                    "result": job.result,
                    "error": str(job.exc_info) if job.exc_info else None,
                    "created_at": job.created_at,
                    "started_at": job.started_at,
                    "ended_at": job.ended_at
                }
        except:
            pass
    
    raise HTTPException(status_code=404, detail="Task not found")


@app.post("/api/v1/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, x_api_key: Optional[str] = Header(None)):
    """Cancel a task"""
    verify_api_key(x_api_key)
    
    if not redis_conn:
        raise HTTPException(status_code=503, detail="Redis not available")
    
    for queue in [queue_high, queue_default, queue_low]:
        if not queue:
            continue
        try:
            job = queue.fetch_job(task_id)
            if job:
                job.cancel()
                logger.info(f"Task cancelled: {job.id}")
                return {"task_id": job.id, "status": "cancelled"}
        except:
            pass
    
    raise HTTPException(status_code=404, detail="Task not found")


@app.get("/api/v1/queues/stats")
async def get_queue_stats(x_api_key: Optional[str] = Header(None)):
    """Get queue statistics"""
    verify_api_key(x_api_key)
    
    if not redis_conn:
        raise HTTPException(status_code=503, detail="Redis not available")
    
    stats = {}
    for queue_name, queue in [('high', queue_high), ('default', queue_default), ('low', queue_low)]:
        try:
            stats[queue_name] = {
                "queued": len(queue),
                "started": len(queue.started_job_registry),
                "finished": len(queue.finished_job_registry),
                "failed": len(queue.failed_job_registry)
            }
        except Exception as e:
            stats[queue_name] = {"error": str(e)}
    
    return stats


# ============ RUN ============

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
