// Edge Function: process-topic
// Procesa documentos y genera threads estructurados
import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from 'jsr:@supabase/supabase-js@2';

const HERMES_BACKEND_URL = Deno.env.get('HERMES_BACKEND_URL') || 'https://hermes-api-production-1195.up.railway.app';
const HERMES_API_KEY = Deno.env.get('HERMES_API_KEY') || 'hermes_topic_threader_2026_secure_key';

const supabase = createClient(
  Deno.env.get('SUPABASE_URL') ?? '',
  Deno.env.get('SUPABASE_SERVICE_ROLE_KEY') ?? ''
);

// Constants for limits
const MAX_DOCS_PER_MONTH = 20;
const MAX_FILE_SIZE_MB = 50; // Max individual file
const MAX_TOTAL_SIZE_MB = 299;
const ALLOWED_FILE_TYPES = ['application/pdf', 'text/plain', 'text/html', 'text/markdown', 'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'];

interface ProcessRequest {
  topic_id?: string;
  document_ids?: string[];
  content?: string;
  options?: {
    tone?: 'professional' | 'casual' | 'educational';
    language?: 'es' | 'en';
    thread_length?: number;
    include_hashtags?: boolean;
    include_cta?: boolean;
  };
}

interface UploadRequest {
  file_name: string;
  file_size: number;
  file_type: string;
  file_url: string;
}

// Check user document limits
async function checkUserLimits(userId: string): Promise<{
  allowed: boolean;
  reason?: string;
  current?: number;
  max?: number;
}> {
  const now = new Date();
  const monthStart = new Date(now.getFullYear(), now.getMonth(), 1).toISOString();

  const { data: uploads, error } = await supabase
    .from('document_uploads')
    .select('id, file_size')
    .eq('user_id', userId)
    .gte('created_at', monthStart);

  if (error) {
    return { allowed: false, reason: 'Error checking limits' };
  }

  const currentCount = uploads?.length || 0;
  const totalSize = uploads?.reduce((sum: number, u: { file_size: number }) => sum + (u.file_size || 0), 0) || 0;

  if (currentCount >= MAX_DOCS_PER_MONTH) {
    return {
      allowed: false,
      reason: `Monthly limit reached (${currentCount}/${MAX_DOCS_PER_MONTH} documents)`,
      current: currentCount,
      max: MAX_DOCS_PER_MONTH
    };
  }

  if (totalSize >= MAX_TOTAL_SIZE_MB * 1024 * 1024) {
    return {
      allowed: false,
      reason: `Storage limit reached (${Math.round(totalSize / 1024 / 1024)}MB/${MAX_TOTAL_SIZE_MB}MB)`,
      current: Math.round(totalSize / 1024 / 1024),
      max: MAX_TOTAL_SIZE_MB
    };
  }

  return { allowed: true, current: currentCount, max: MAX_DOCS_PER_MONTH };
}

// Register a document upload
async function registerDocumentUpload(userId: string, upload: UploadRequest): Promise<{
  success: boolean;
  document?: { id: string };
  error?: string;
}> {
  // Validate file type
  if (!ALLOWED_FILE_TYPES.includes(upload.file_type)) {
    return {
      success: false,
      error: `File type not allowed. Allowed types: ${ALLOWED_FILE_TYPES.join(', ')}`
    };
  }

  // Validate file size
  if (upload.file_size > MAX_FILE_SIZE_MB * 1024 * 1024) {
    return {
      success: false,
      error: `File too large. Max size: ${MAX_FILE_SIZE_MB}MB`
    };
  }

  // Check limits
  const limitsCheck = await checkUserLimits(userId);
  if (!limitsCheck.allowed) {
    return {
      success: false,
      error: limitsCheck.reason
    };
  }

  // Insert document record
  const { data, error } = await supabase
    .from('document_uploads')
    .insert({
      user_id: userId,
      file_name: upload.file_name,
      file_size: upload.file_size,
      file_url: upload.file_url,
      file_type: upload.file_type
    })
    .select('id')
    .single();

  if (error) {
    return {
      success: false,
      error: error.message
    };
  }

  return {
    success: true,
    document: { id: data.id }
  };
}

// Get user documents
async function getUserDocuments(userId: string, limit: number = 50): Promise<{
  documents: Array<{
    id: string;
    file_name: string;
    file_size: number;
    file_url: string;
    created_at: string;
  }>;
  total: number;
}> {
  const { data, error, count } = await supabase
    .from('document_uploads')
    .select('id, file_name, file_size, file_url, created_at', { count: 'exact' })
    .eq('user_id', userId)
    .order('created_at', { ascending: false })
    .limit(limit);

  if (error) {
    return { documents: [], total: 0 };
  }

  return {
    documents: data || [],
    total: count || 0
  };
}

Deno.serve(async (req: Request) => {
  const corsHeaders = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'authorization, x-api-key, content-type',
    'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
  };

  // Handle preflight
  if (req.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: corsHeaders });
  }

  // Get user from auth header
  const authHeader = req.headers.get('authorization');
  let userId: string | undefined;

  if (authHeader?.startsWith('Bearer ')) {
    const token = authHeader.replace('Bearer ', '');
    const { data: { user }, error } = await supabase.auth.getUser(token);
    if (!error && user) {
      userId = user.id;
    }
  }

  // Route: GET /limits - Get user limits
  if (req.method === 'GET' && new URL(req.url).pathname.endsWith('/limits')) {
    if (!userId) {
      return new Response(
        JSON.stringify({ error: 'Unauthorized' }),
        { status: 401, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      );
    }

    const now = new Date();
    const monthStart = new Date(now.getFullYear(), now.getMonth(), 1).toISOString();

    const { data: uploads } = await supabase
      .from('document_uploads')
      .select('file_size')
      .eq('user_id', userId)
      .gte('created_at', monthStart);

    const totalSize = uploads?.reduce((sum: number, u: { file_size: number }) => sum + (u.file_size || 0), 0) || 0;

    return new Response(
      JSON.stringify({
        documents: {
          current: uploads?.length || 0,
          max: MAX_DOCS_PER_MONTH
        },
        storage: {
          currentMb: Math.round((totalSize / 1024 / 1024) * 100) / 100,
          maxMb: MAX_TOTAL_SIZE_MB,
          currentBytes: totalSize,
          maxBytes: MAX_TOTAL_SIZE_MB * 1024 * 1024
        },
        resetDate: new Date(now.getFullYear(), now.getMonth() + 1, 1).toISOString()
      }),
      { status: 200, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
    );
  }

  // Route: GET /documents - List user documents
  if (req.method === 'GET' && new URL(req.url).pathname.endsWith('/documents')) {
    if (!userId) {
      return new Response(
        JSON.stringify({ error: 'Unauthorized' }),
        { status: 401, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      );
    }

    const result = await getUserDocuments(userId);
    return new Response(
      JSON.stringify(result),
      { status: 200, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
    );
  }

  // Route: POST /upload - Register document upload
  if (req.method === 'POST' && new URL(req.url).pathname.endsWith('/upload')) {
    if (!userId) {
      return new Response(
        JSON.stringify({ error: 'Unauthorized' }),
        { status: 401, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      );
    }

    try {
      const upload: UploadRequest = await req.json();
      
      // Validate required fields
      if (!upload.file_name || !upload.file_size || !upload.file_url) {
        return new Response(
          JSON.stringify({ error: 'Missing required fields: file_name, file_size, file_url' }),
          { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );
      }

      const result = await registerDocumentUpload(userId, upload);
      
      if (!result.success) {
        return new Response(
          JSON.stringify({ error: result.error }),
          { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );
      }

      return new Response(
        JSON.stringify({ 
          success: true, 
          document_id: result.document?.id,
          message: 'Document registered successfully'
        }),
        { status: 201, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      );
    } catch (error) {
      return new Response(
        JSON.stringify({ error: 'Invalid request body' }),
        { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      );
    }
  }

  // Route: POST / - Process topic (main endpoint)
  if (req.method === 'POST') {
    if (!userId) {
      return new Response(
        JSON.stringify({ error: 'Unauthorized' }),
        { status: 401, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      );
    }

    try {
      const body: ProcessRequest = await req.json();
      
      // Forward to Hermes Backend for processing
      const response = await fetch(`${HERMES_BACKEND_URL}/api/v1/tasks`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': HERMES_API_KEY,
        },
        body: JSON.stringify({
          task_type: 'process_topic',
          payload: {
            topic_id: body.topic_id,
            document_ids: body.document_ids,
            content: body.content,
            options: body.options || {
              tone: 'professional',
              language: 'es',
              thread_length: 5,
              include_hashtags: true,
              include_cta: true
            }
          },
          user_id: userId,
          priority: 'high'
        }),
      });

      if (!response.ok) {
        const errorData = await response.text();
        console.error('Hermes backend error:', errorData);
        return new Response(
          JSON.stringify({ error: 'Backend error', details: errorData }),
          { status: response.status, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );
      }

      const data = await response.json();
      return new Response(
        JSON.stringify(data),
        { status: 200, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      );

    } catch (error) {
      console.error('Process topic error:', error);
      return new Response(
        JSON.stringify({ 
          error: 'Internal server error',
          message: error instanceof Error ? error.message : 'Unknown error'
        }),
        { status: 500, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      );
    }
  }

  // Route: DELETE /documents/:id - Delete a document
  if (req.method === 'DELETE') {
    if (!userId) {
      return new Response(
        JSON.stringify({ error: 'Unauthorized' }),
        { status: 401, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      );
    }

    const docId = new URL(req.url).pathname.split('/').pop();
    
    if (!docId) {
      return new Response(
        JSON.stringify({ error: 'Document ID required' }),
        { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      );
    }

    // Delete document (RLS ensures user owns it)
    const { error } = await supabase
      .from('document_uploads')
      .delete()
      .eq('id', docId)
      .eq('user_id', userId);

    if (error) {
      return new Response(
        JSON.stringify({ error: error.message }),
        { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      );
    }

    return new Response(
      JSON.stringify({ success: true, message: 'Document deleted' }),
      { status: 200, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
    );
  }

  // Method not allowed
  return new Response(
    JSON.stringify({ error: 'Method not allowed' }),
    { status: 405, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
  );
});