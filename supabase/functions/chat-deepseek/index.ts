// Edge Function: chat-deepseek
// Endpoint para chat con modelo DeepSeek via Hermes Backend
import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from 'jsr:@supabase/supabase-js@2';

const HERMES_BACKEND_URL = Deno.env.get('HERMES_BACKEND_URL') || 'https://hermes-api-production-1195.up.railway.app';
const HERMES_API_KEY = Deno.env.get('HERMES_API_KEY') || 'hermes_topic_threader_2026_secure_key';

// Supabase client con service role para operaciones admin
const supabase = createClient(
  Deno.env.get('SUPABASE_URL') ?? '',
  Deno.env.get('SUPABASE_SERVICE_ROLE_KEY') ?? ''
);

interface ChatMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

interface ChatRequest {
  messages: ChatMessage[];
  temperature?: number;
  max_tokens?: number;
  stream?: boolean;
  user_id?: string;
  context?: {
    topic_id?: string;
    conversation_id?: string;
  };
}

// Helper para obtener límites del usuario
async function getUserLimits(userId: string): Promise<{
  monthlyDocs: number;
  totalSizeBytes: number;
  maxDocs: number;
  maxSizeBytes: number;
}> {
  const MAX_DOCS_PER_MONTH = 20;
  const MAX_TOTAL_SIZE_MB = 299;
  const MAX_TOTAL_SIZE_BYTES = MAX_TOTAL_SIZE_MB * 1024 * 1024;

  // Get current month start
  const now = new Date();
  const monthStart = new Date(now.getFullYear(), now.getMonth(), 1).toISOString();

  const { data: uploads, error } = await supabase
    .from('document_uploads')
    .select('file_size')
    .eq('user_id', userId)
    .gte('created_at', monthStart);

  if (error) {
    console.error('Error fetching user limits:', error);
    return {
      monthlyDocs: 0,
      totalSizeBytes: 0,
      maxDocs: MAX_DOCS_PER_MONTH,
      maxSizeBytes: MAX_TOTAL_SIZE_BYTES
    };
  }

  const totalSize = uploads?.reduce((sum: number, u: { file_size: number }) => sum + (u.file_size || 0), 0) || 0;

  return {
    monthlyDocs: uploads?.length || 0,
    totalSizeBytes: totalSize,
    maxDocs: MAX_DOCS_PER_MONTH,
    maxSizeBytes: MAX_TOTAL_SIZE_BYTES
  };
}

Deno.serve(async (req: Request) => {
  // CORS headers
  const corsHeaders = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'authorization, x-api-key, content-type',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
  };

  // Handle preflight
  if (req.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: corsHeaders });
  }

  // Only allow POST
  if (req.method !== 'POST') {
    return new Response(
      JSON.stringify({ error: 'Method not allowed' }),
      { status: 405, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
    );
  }

  try {
    // Parse request body
    const body: ChatRequest = await req.json();

    // Validate required fields
    if (!body.messages || !Array.isArray(body.messages) || body.messages.length === 0) {
      return new Response(
        JSON.stringify({ error: 'messages array is required' }),
        { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      );
    }

    // Get user from auth header
    const authHeader = req.headers.get('authorization');
    let userId = body.user_id;

    if (authHeader?.startsWith('Bearer ')) {
      const token = authHeader.replace('Bearer ', '');
      const { data: { user }, error: authError } = await supabase.auth.getUser(token);
      if (!authError && user) {
        userId = user.id;
      }
    }

    // Check limits if user is authenticated
    if (userId) {
      const limits = await getUserLimits(userId);
      
      if (limits.monthlyDocs >= limits.maxDocs) {
        return new Response(
          JSON.stringify({ 
            error: 'Monthly document limit reached',
            details: {
              current: limits.monthlyDocs,
              max: limits.maxDocs,
              resetDate: new Date(new Date().getFullYear(), new Date().getMonth() + 1, 1).toISOString()
            }
          }),
          { status: 403, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );
      }

      if (limits.totalSizeBytes >= limits.maxSizeBytes) {
        return new Response(
          JSON.stringify({ 
            error: 'Storage limit reached',
            details: {
              currentMb: Math.round((limits.totalSizeBytes / 1024 / 1024) * 100) / 100,
              maxMb: limits.maxSizeBytes / 1024 / 1024
            }
          }),
          { status: 403, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );
      }
    }

    // Forward to Hermes Backend - DeepSeek integration
    const response = await fetch(`${HERMES_BACKEND_URL}/api/v1/chat/deepseek`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': HERMES_API_KEY,
      },
      body: JSON.stringify({
        messages: body.messages,
        temperature: body.temperature ?? 0.7,
        max_tokens: body.max_tokens ?? 4096,
        stream: body.stream ?? false,
        user_id: userId,
        context: body.context
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

    // Handle streaming vs non-streaming
    if (body.stream) {
      // Stream response back
      return new Response(response.body, {
        status: 200,
        headers: {
          ...corsHeaders,
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'Connection': 'keep-alive',
        },
      });
    } else {
      const data = await response.json();
      return new Response(
        JSON.stringify(data),
        { status: 200, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      );
    }

  } catch (error) {
    console.error('Edge function error:', error);
    return new Response(
      JSON.stringify({ 
        error: 'Internal server error',
        message: error instanceof Error ? error.message : 'Unknown error'
      }),
      { status: 500, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
    );
  }
});