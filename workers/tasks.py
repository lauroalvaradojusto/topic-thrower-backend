"""
Worker Tasks para Hermes Backend
Implementación de tareas específicas para Topic Threader
"""
import logging
import os
from typing import Dict, Any
import redis
from rq import get_current_job
import httpx

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Supabase configuration
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://dgrtziddrgpqenppehae.supabase.co")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# Async HTTP client
async_client = None


def get_supabase_client():
    """Get Supabase client for service operations"""
    try:
        from supabase import create_client, Client
        return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    except Exception as e:
        logger.error(f"Failed to create Supabase client: {e}")
        return None


def update_task_status(task_id: str, status: str, result: Any = None, error: str = None):
    """Update task status in Supabase"""
    try:
        client = get_supabase_client()
        if not client:
            return False
        
        data = {
            "status": status,
            "updated_at": "now()"
        }
        
        if result is not None:
            data["result"] = str(result)
        if error is not None:
            data["error"] = error
        
        client.table('tasks').update(data).eq('id', task_id).execute()
        return True
    except Exception as e:
        logger.error(f"Failed to update task status: {e}")
        return False


# ============================================================================
# TASK 1: Process Topic
# ============================================================================
def process_topic(payload: Dict[str, Any], user_id: str, callback_url: str = None):
    """
    Procesar un tema y generar un thread estructurado
    
    Payload esperado:
    - topic: str - El tema a procesar
    - tone: str - Tono del thread (professional, casual, etc)
    - length: int - Longitud del thread (número de tweets)
    """
    job = get_current_job()
    logger.info(f"Processing topic for user {user_id}")
    
    try:
        topic = payload.get('topic', '')
        tone = payload.get('tone', 'professional')
        length = payload.get('length', 5)
        
        if not topic:
            raise ValueError("Topic is required")
        
        # Simular procesamiento (en producción, usar AI)
        thread = [
            f"🧵 {topic}",
            f"📍 Punto 1: {topic[:50]}...",
            f"📍 Punto 2: Análisis de {topic[:50]}...",
            f"📍 Punto 3: Contexto de {topic[:50]}...",
            f"📍 Punto 4: Implicaciones de {topic[:50]}...",
            f"📍 Punto 5: Conclusión sobre {topic[:50]}..."
        ]
        
        # Guardar resultado en Supabase
        result = {
            "thread": thread,
            "length": len(thread),
            "tone": tone
        }
        
        update_task_status(job.id, "completed", result=result)
        
        logger.info(f"Topic processed successfully: {job.id}")
        return result
        
    except Exception as e:
        logger.error(f"Error processing topic: {e}")
        update_task_status(job.id, "failed", error=str(e))
        raise


# ============================================================================
# TASK 2: Analyze Document
# ============================================================================
def analyze_doc(payload: Dict[str, Any], user_id: str, callback_url: str = None):
    """
    Analizar un documento (PDF, HTML, etc)
    
    Payload esperado:
    - doc_url: str - URL del documento
    - doc_type: str - Tipo (pdf, html, text)
    - analysis_type: str - Tipo de análisis (summary, key_points, etc)
    """
    job = get_current_job()
    logger.info(f"Analyzing document for user {user_id}")
    
    try:
        doc_url = payload.get('doc_url', '')
        doc_type = payload.get('doc_type', 'text')
        analysis_type = payload.get('analysis_type', 'summary')
        
        if not doc_url:
            raise ValueError("Document URL is required")
        
        # Simular análisis (en producción, usar AI/OCR)
        result = {
            "doc_url": doc_url,
            "doc_type": doc_type,
            "summary": f"Summary of {doc_type} document",
            "key_points": [
                "Key point 1 from document",
                "Key point 2 from document",
                "Key point 3 from document"
            ],
            "word_count": 1500,
            "pages": 5 if doc_type == 'pdf' else 1
        }
        
        update_task_status(job.id, "completed", result=result)
        
        logger.info(f"Document analyzed successfully: {job.id}")
        return result
        
    except Exception as e:
        logger.error(f"Error analyzing document: {e}")
        update_task_status(job.id, "failed", error=str(e))
        raise


# ============================================================================
# TASK 3: Publish to Twitter
# ============================================================================
def publish_twitter(payload: Dict[str, Any], user_id: str, callback_url: str = None):
    """
    Publicar un thread en Twitter/X
    
    Payload esperado:
    - thread: list[str] - Lista de tweets
    - media_urls: list[str] - URLs de imágenes/medios (opcional)
    """
    job = get_current_job()
    logger.info(f"Publishing to Twitter for user {user_id}")
    
    try:
        thread = payload.get('thread', [])
        media_urls = payload.get('media_urls', [])
        
        if not thread:
            raise ValueError("Thread is required")
        
        # Simular publicación (en producción, usar Twitter API v2)
        tweets = []
        for i, tweet in enumerate(thread):
            tweet_id = f"tweet_{job.id}_{i+1}"
            tweets.append({
                "id": tweet_id,
                "text": tweet,
                "position": i + 1,
                "url": f"https://twitter.com/user/status/{tweet_id}"
            })
        
        result = {
            "thread_published": True,
            "tweets": tweets,
            "first_tweet_id": tweets[0]['id'] if tweets else None,
            "media_uploaded": len(media_urls)
        }
        
        update_task_status(job.id, "completed", result=result)
        
        logger.info(f"Thread published successfully: {job.id}")
        return result
        
    except Exception as e:
        logger.error(f"Error publishing to Twitter: {e}")
        update_task_status(job.id, "failed", error=str(e))
        raise


# ============================================================================
# TASK 4: Delete from Twitter
# ============================================================================
def delete_twitter(payload: Dict[str, Any], user_id: str, callback_url: str = None):
    """
    Eliminar un thread de Twitter/X
    
    Payload esperado:
    - tweet_ids: list[str] - Lista de IDs de tweets a eliminar
    """
    job = get_current_job()
    logger.info(f"Deleting from Twitter for user {user_id}")
    
    try:
        tweet_ids = payload.get('tweet_ids', [])
        
        if not tweet_ids:
            raise ValueError("Tweet IDs are required")
        
        # Simular eliminación (en producción, usar Twitter API v2)
        deleted = []
        for tweet_id in tweet_ids:
            deleted.append({
                "tweet_id": tweet_id,
                "deleted": True
            })
        
        result = {
            "tweets_deleted": len(deleted),
            "tweet_ids": tweet_ids
        }
        
        update_task_status(job.id, "completed", result=result)
        
        logger.info(f"Tweets deleted successfully: {job.id}")
        return result
        
    except Exception as e:
        logger.error(f"Error deleting from Twitter: {e}")
        update_task_status(job.id, "failed", error=str(e))
        raise


# ============================================================================
# TASK 5: Enhanced Chat
# ============================================================================
def chat_enhanced(payload: Dict[str, Any], user_id: str, callback_url: str = None):
    """
    Chat mejorado con contexto largo y memoria
    
    Payload esperado:
    - message: str - Mensaje del usuario
    - chat_id: str - ID del chat (para mantener contexto)
    - context: dict - Contexto adicional (opcional)
    """
    job = get_current_job()
    logger.info(f"Enhanced chat for user {user_id}")
    
    try:
        message = payload.get('message', '')
        chat_id = payload.get('chat_id', '')
        context = payload.get('context', {})
        
        if not message:
            raise ValueError("Message is required")
        
        # Simular respuesta AI (en producción, usar DeepSeek/OpenAI)
        result = {
            "chat_id": chat_id,
            "response": f"Response to: {message[:100]}...",
            "model": "deepseek-chat",
            "tokens_used": 150,
            "context_used": len(context)
        }
        
        update_task_status(job.id, "completed", result=result)
        
        logger.info(f"Enhanced chat completed: {job.id}")
        return result
        
    except Exception as e:
        logger.error(f"Error in enhanced chat: {e}")
        update_task_status(job.id, "failed", error=str(e))
        raise
