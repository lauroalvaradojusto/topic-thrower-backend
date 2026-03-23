-- Migration: 001_initial_schema.sql
-- Apply this migration to create the document_uploads table and related objects
-- 
-- Usage:
--   1. Go to Supabase Dashboard > SQL Editor
--   2. Paste this content
--   3. Execute
-- 
-- Or via CLI:
--   supabase db push

-- ============================================
-- STEP 1: Create document_uploads table
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
    
    CONSTRAINT valid_file_size CHECK (file_size <= 50 * 1024 * 1024)
);

-- ============================================
-- STEP 2: Create indexes
-- ============================================
CREATE INDEX IF NOT EXISTS idx_document_uploads_user_id ON public.document_uploads(user_id);
CREATE INDEX IF NOT EXISTS idx_document_uploads_created_at ON public.document_uploads(created_at);
CREATE INDEX IF NOT EXISTS idx_document_uploads_user_month ON public.document_uploads(user_id, created_at);

-- ============================================
-- STEP 3: Create user_document_limits cache table
-- ============================================
CREATE TABLE IF NOT EXISTS public.user_document_limits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    month_year TEXT NOT NULL,
    document_count INTEGER DEFAULT 0,
    total_size_bytes BIGINT DEFAULT 0,
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    
    CONSTRAINT unique_user_month UNIQUE (user_id, month_year)
);

CREATE INDEX IF NOT EXISTS idx_user_document_limits_user ON public.user_document_limits(user_id);
CREATE INDEX IF NOT EXISTS idx_user_document_limits_month ON public.user_document_limits(month_year);

-- ============================================
-- STEP 4: Create trigger function
-- ============================================
CREATE OR REPLACE FUNCTION public.update_document_limits()
RETURNS TRIGGER AS $$
DECLARE
    v_month_year TEXT;
    v_user_id UUID;
BEGIN
    IF TG_OP = 'INSERT' THEN
        v_user_id := NEW.user_id;
    ELSE
        v_user_id := OLD.user_id;
    END IF;
    
    v_month_year := TO_CHAR(COALESCE(NEW.created_at, OLD.created_at, NOW()), 'YYYY-MM');
    
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
-- STEP 5: Create trigger
-- ============================================
DROP TRIGGER IF EXISTS trigger_update_document_limits ON public.document_uploads;
CREATE TRIGGER trigger_update_document_limits
    AFTER INSERT OR DELETE ON public.document_uploads
    FOR EACH ROW
    EXECUTE FUNCTION public.update_document_limits();

-- ============================================
-- STEP 6: Create helper functions
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
BEGIN
    SELECT 
        COALESCE(document_count, 0),
        COALESCE(total_size_bytes, 0)
    INTO v_doc_count, v_total_size
    FROM public.user_document_limits
    WHERE user_id = p_user_id AND month_year = v_month_year;
    
    IF v_doc_count IS NULL THEN
        v_doc_count := 0;
        v_total_size := 0;
    END IF;
    
    RETURN QUERY SELECT
        v_doc_count < 20 AND v_total_size <299 * 1024 * 1024,
        v_doc_count,
        20,
        ROUND(v_total_size::NUMERIC / 1024 / 1024, 2),
        299,
        CASE
            WHEN v_doc_count >= 20 THEN 'Monthly document limit reached'
            WHEN v_total_size >= 299 * 1024 * 1024 THEN 'Storage limit reached'
            ELSE NULL
        END;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================
-- STEP 7: Enable RLS
-- ============================================
ALTER TABLE public.document_uploads ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_document_limits ENABLE ROW LEVEL SECURITY;

-- Policies
CREATE POLICY "Users can view own documents" ON public.document_uploads
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own documents" ON public.document_uploads
    FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can delete own documents" ON public.document_uploads
    FOR DELETE USING (auth.uid() = user_id);

CREATE POLICY "Users can view own limits" ON public.user_document_limits
    FOR SELECT USING (auth.uid() = user_id);

-- ============================================
-- STEP 8: Grant permissions
-- ============================================
GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role;
GRANT ALL ON ALL FUNCTIONS IN SCHEMA public TO service_role;

-- ============================================
-- Done!
-- ============================================
SELECT 'Schema migration complete!' as status;