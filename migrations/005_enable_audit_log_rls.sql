-- Migration: Enable RLS on audit_logs and enforce append-only behavior
-- Purpose: Make audit logs insert-only for non-service roles
-- Date: 2025-01-01

-- Enable RLS (service role retains bypass privileges in Supabase)
ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'audit_logs'
      AND policyname = 'audit_logs_insert_only'
  ) THEN
    CREATE POLICY "audit_logs_insert_only"
      ON public.audit_logs
      FOR INSERT
      WITH CHECK (
        auth.uid() = user_id OR auth.role() = 'service_role'
      );
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'audit_logs'
      AND policyname = 'audit_logs_delete_service_role'
  ) THEN
    CREATE POLICY "audit_logs_delete_service_role"
      ON public.audit_logs
      FOR DELETE
      USING (
        auth.role() = 'service_role'
        AND created_at < now() - interval '1 year'
      );
  END IF;
END $$;

-- Note: No SELECT/UPDATE policies are created to keep audit logs append-only
-- for non-service roles.
