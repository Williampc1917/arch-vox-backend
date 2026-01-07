-- Add VIP onboarding skip + acquisition state to users.

ALTER TABLE public.users
  ADD COLUMN IF NOT EXISTS vip_onboarding_skipped boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS vip_acquisition_status text NOT NULL DEFAULT 'active',
  ADD COLUMN IF NOT EXISTS vip_last_attempt_at timestamp with time zone;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'users_vip_acquisition_status_check'
  ) THEN
    ALTER TABLE public.users
      ADD CONSTRAINT users_vip_acquisition_status_check
      CHECK (vip_acquisition_status IN ('active', 'pending'));
  END IF;
END $$;
