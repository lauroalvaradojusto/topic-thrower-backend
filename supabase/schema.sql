-- ============================================
-- Hermes Topic Threader - Supabase Schema
-- ============================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================
-- Table: document_uploads
-- Almacena registros de documentos subidos por usuarios
-- ============================================
CREATE TABLE IF NOT EXISTS public.document_uploads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    file_name TEXT NOT NULL,
    file_size BIGINT NOT NULL CHECK (file_size > 0),
    file_url TEXT NOT NULL,
    file_type TEXT DEFAULT 'application/octet-stream',
    storage_path TEXT,
    processed BOOLEAN DEFAULT FALSE,
    processed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    CONSTRAINT valid_file_size CHECK (file_size <= 50 * 1024 * 1024) -- Max 50MB per file
);

-- Index for faster queries by user
CREATE INDEX IF NOT EXISTS idx_document_uploads_user_id ON public.document_uploads(user_id);
CREATE INDEX IF NOT EXISTS idx_document_uploads_created_at ON public.document_uploads(created_at);
CREATE INDEX IF NOT EXISTS idx_document_uploads_user_month ON public.document_uploads(user_id, created_at);

-- ============================================
-- Table: user_document_limits
-- Cache mensual de límites de documentos por usuario
-- ============================================
CREATE TABLE IF NOT EXISTS public.user_document_limits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,
    month_year TEXT NOT NULL, -- Format: '2026-03'
    document_count INTEGER DEFAULT 0,
    total_size_bytes BIGINT DEFAULT 0,
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    
    CONSTRAINT unique_user_month UNIQUE (user_id, month_year)
);

CREATE INDEX IF NOT EXISTS idx_user_document_limits_user ON public.user_document_limits(user_id);
CREATE INDEX IF NOT EXISTS idx_user_document_limits_month ON public.user_document_limits(month_year);

-- ============================================
-- Function: update_document_limits
-- Actualiza el conteo mensual de documentos
-- ============================================
CREATE OR REPLACE FUNCTION public.update_document_limits()
RETURNS TRIGGER AS $$
DECLARE
    v_month_year TEXT;
    v_user_id UUID;
BEGIN
    -- Get user_id and month
    IF TG_OP = 'INSERT' THEN
        v_user_id := NEW.user_id;
    ELSE
        v_user_id := OLD.user_id;
    END IF;
    
    v_month_year := TO_CHAR(COALESCE(NEW.created_at, OLD.created_at, NOW()), 'YYYY-MM');
    
    -- Upsert the limits record
    INSERT INTO public.user_document_limits (user_id, month_year, document_count, total_size_bytes, last_updated)
    SELECT 
        v_user_id,
        v_month_year,
        COUNT(*),
        COALESCE(SUM(file_size), 0),
        NOW()
    FROM public.document_uploads
    WHERE user_id = v_user_id
      AND TO_CHAR(created_at, 'YYYY-MM') = v_month_year
    ON CONFLICT (user_id, month_year) 
    DO UPDATE SET
        document_count = EXCLUDED.document_count,
        total_size_bytes = EXCLUDED.total_size_bytes,
        last_updated = NOW();
    
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================
-- Trigger: Update limits on document changes
-- ============================================
DROP TRIGGER IF EXISTS trigger_update_document_limits ON public.document_uploads;

CREATE TRIGGER trigger_update_document_limits
    AFTER INSERT OR DELETE ON public.document_uploads
    FOR EACH ROW
    EXECUTE FUNCTION public.update_document_limits();

-- ============================================
-- Function: check_user_limits
-- Verifica si un usuario puede subir más documentos
-- ============================================
CREATE OR REPLACE FUNCTION public.check_user_limits(p_user_id UUID)
RETURNS TABLE (
    can_upload BOOLEAN,
    documents_current INTEGER,
    documents_max INTEGER,
    storage_current_mb NUMERIC,
    storage_max_mb INTEGER,
    reason TEXT
) AS $$
DECLARE
    v_month_year TEXT := TO_CHAR(NOW(), 'YYYY-MM');
    v_doc_count INTEGER;
    v_total_size BIGINT;
    v_docs_limit CONSTANT INTEGER := 20;
    v_size_limit_mb CONSTANT INTEGER := 299;
BEGIN
    -- Get current month stats
    SELECT 
        COALESCE(udl.document_count, 0),
        COALESCE(udl.total_size_bytes, 0)
    INTO v_doc_count, v_total_size
    FROM public.user_document_limits udl
    WHERE udl.user_id = p_user_id
      AND udl.month_year = v_month_year;
    
    -- If no record exists, user has 0 documents
    IF v_doc_count IS NULL THEN
        v_doc_count := 0;
        v_total_size := 0;
    END IF;
    
    -- Return result
    RETURN QUERY SELECT
        CASE
            WHEN v_doc_count >= v_docs_limit THEN FALSE
            WHEN v_total_size >= v_size_limit_mb * 1024 * 1024 THEN FALSE
            ELSE TRUE
        END AS can_upload,
        v_doc_count AS documents_current,
        v_docs_limit AS documents_max,
        ROUND(v_total_size::NUMERIC / 1024 / 1024, 2) AS storage_current_mb,
        v_size_limit_mb AS storage_max_mb,
        CASE
            WHEN v_doc_count >= v_docs_limit THEN 'Monthly document limit reached'
            WHEN v_total_size >= v_size_limit_mb * 1024 * 1024 THEN 'Storage limit reached'
            ELSE NULL
        END AS reason;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================
-- Function: get_user_document_stats
-- Estadísticas de documentos del usuario
-- ============================================
CREATE OR REPLACE FUNCTION public.get_user_document_stats(p_user_id UUID)
RETURNS TABLE (
    total_documents INTEGER,
    this_month_documents INTEGER,
    total_size_bytes BIGINT,
    this_month_size_bytes BIGINT,
    oldest_upload TIMESTAMPTZ,
    newest_upload TIMESTAMPTZ
) AS $$
DECLARE
    v_month_year TEXT := TO_CHAR(NOW(), 'YYYY-MM');
BEGIN
    RETURN QUERY SELECT
        (SELECT COUNT(*) FROM public.document_uploads WHERE user_id = p_user_id)::INTEGER,
        (SELECT COUNT(*) FROM public.document_uploads WHERE user_id = p_user_id AND TO_CHAR(created_at, 'YYYY-MM') = v_month_year)::INTEGER,
        COALESCE((SELECT SUM(file_size) FROM public.document_uploads WHERE user_id = p_user_id), 0),
        COALESCE((SELECT SUM(file_size) FROM public.document_uploads WHERE user_id = p_user_id AND TO_CHAR(created_at, 'YYYY-MM') = v_month_year), 0),
        (SELECT MIN(created_at) FROM public.document_uploads WHERE user_id = p_user_id),
        (SELECT MAX(created_at) FROM public.document_uploads WHERE user_id = p_user_id);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================
-- RLS (Row Level Security) Policies
-- ============================================
ALTER TABLE public.document_uploads ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_document_limits ENABLE ROW LEVEL SECURITY;

-- Users can only see their own documents
CREATE POLICY "Users can view own documents" ON public.document_uploads
    FOR SELECT USING (auth.uid() = user_id);

-- Users can only insert their own documents
CREATE POLICY "Users can insert own documents" ON public.document_uploads
    FOR INSERT WITH CHECK (auth.uid() = user_id);

-- Users can only delete their own documents
CREATE POLICY "Users can delete own documents" ON public.document_uploads
    FOR DELETE USING (auth.uid() = user_id);

-- Users can only see their own limits
CREATE POLICY "Users can view own limits" ON public.user_document_limits
    FOR SELECT USING (auth.uid() = user_id);

-- ============================================
-- View: user_document_summary
-- Vista resumida para frontend
-- ============================================
CREATE OR REPLACE VIEW public.user_document_summary AS
SELECT 
    du.user_id,
    COUNT(*) OVER (PARTITION BY du.user_id) as total_documents,
    COUNT(*) FILTER (WHERE TO_CHAR(du.created_at, 'YYYY-MM') = TO_CHAR(NOW(), 'YYYY-MM')) 
        OVER (PARTITION BY du.user_id) as this_month_documents,
    SUM(du.file_size) OVER (PARTITION BY du.user_id) as total_size_bytes,
    20 as max_documents_per_month,
    299 as max_storage_mb
FROM public.document_uploads du;

-- ============================================
-- Sample queries for testing
-- ============================================
-- Check limits for a user:
-- SELECT * FROM public.check_user_limits('user-uuid-here');
-- 
-- Get user stats:
-- SELECT * FROM public.get_user_document_stats('user-uuid-here');
--
-- Count monthly documents (from trigger):
-- SELECT user_id, month_year, document_count, total_size_bytes 
-- FROM public.user_document_limits 
-- WHERE month_year = TO_CHAR(NOW(), 'YYYY-MM');

-- ============================================
-- Grant permissions for service role
-- ============================================
GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role;
GRANT ALL ON ALL FUNCTIONS IN SCHEMA public TO service_role;

-- ============================================
-- Initial setup complete
-- ============================================
-- Run: SELECT 'Schema created successfully' as status;