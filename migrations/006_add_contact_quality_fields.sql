-- Migration: Add quality-related fields to contacts
-- Purpose: Support recency buckets and manual contact flag for VIP scoring
-- Date: 2025-01-01

ALTER TABLE public.contacts
  ADD COLUMN IF NOT EXISTS email_count_7d integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS email_count_8_30d integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS email_count_31_90d integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS manual_added boolean NOT NULL DEFAULT false;
