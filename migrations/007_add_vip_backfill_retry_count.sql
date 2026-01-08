-- Add per-job retry counter for VIP backfill jobs.

ALTER TABLE public.user_vip_backfill_jobs
  ADD COLUMN IF NOT EXISTS retry_count integer NOT NULL DEFAULT 0;
