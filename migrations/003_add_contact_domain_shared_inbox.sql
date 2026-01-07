-- Add domain intelligence fields to contacts.

ALTER TABLE public.contacts
  ADD COLUMN IF NOT EXISTS email_domain text,
  ADD COLUMN IF NOT EXISTS is_shared_inbox boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS contacts_email_domain_idx
  ON public.contacts (email_domain);

CREATE INDEX IF NOT EXISTS contacts_is_shared_inbox_idx
  ON public.contacts (is_shared_inbox);
