-- Create contact identities table for encrypted email/display name storage.

CREATE TABLE IF NOT EXISTS public.contact_identities (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL,
  contact_hash text NOT NULL,
  email_encrypted bytea NOT NULL,
  display_name_encrypted bytea,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT contact_identities_pkey PRIMARY KEY (id),
  CONSTRAINT contact_identities_user_hash_key UNIQUE (user_id, contact_hash),
  CONSTRAINT contact_identities_user_id_fkey FOREIGN KEY (user_id)
    REFERENCES public.users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS contact_identities_user_id_idx
  ON public.contact_identities (user_id);

CREATE INDEX IF NOT EXISTS contact_identities_contact_hash_idx
  ON public.contact_identities (contact_hash);

ALTER TABLE public.contact_identities ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'contact_identities'
      AND policyname = 'read own identities'
  ) THEN
    CREATE POLICY "read own identities"
      ON public.contact_identities
      FOR SELECT
      USING (auth.uid() = user_id);
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'contact_identities'
      AND policyname = 'write own identities'
  ) THEN
    CREATE POLICY "write own identities"
      ON public.contact_identities
      FOR INSERT
      WITH CHECK (auth.uid() = user_id);
  END IF;
END $$;
