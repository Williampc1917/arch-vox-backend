-- Migration: Create audit_logs table for PII access tracking
-- Purpose: Gmail API compliance - track all access to user data
-- Date: 2025-12-31

-- ============================================================================
-- AUDIT LOGS TABLE
-- ============================================================================
-- This table stores an immutable audit trail of all access to user PII.
-- Required for Gmail API security audit and GDPR compliance.

CREATE TABLE IF NOT EXISTS audit_logs (
    -- Primary key
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- User who performed the action
    user_id UUID NOT NULL,

    -- Action details
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50),
    resource_id VARCHAR(255),
    resource_count INTEGER,

    -- PII tracking
    pii_fields JSONB,

    -- Request context
    ip_address INET,
    user_agent TEXT,
    request_id VARCHAR(100),

    -- Additional metadata
    metadata JSONB,

    -- Timestamp (immutable)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- INDEXES FOR PERFORMANCE
-- ============================================================================
-- These indexes optimize common audit log queries

-- Query audit logs by user
CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id
    ON audit_logs(user_id);

-- Query audit logs by action type
CREATE INDEX IF NOT EXISTS idx_audit_logs_action
    ON audit_logs(action);

-- Query audit logs by date (most recent first)
CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at
    ON audit_logs(created_at DESC);

-- Query audit logs by resource type
CREATE INDEX IF NOT EXISTS idx_audit_logs_resource_type
    ON audit_logs(resource_type);

-- Composite index for user + date queries (most common)
CREATE INDEX IF NOT EXISTS idx_audit_logs_user_created
    ON audit_logs(user_id, created_at DESC);

-- Index for IP-based queries (security investigations)
CREATE INDEX IF NOT EXISTS idx_audit_logs_ip_address
    ON audit_logs(ip_address);

-- ============================================================================
-- ROW LEVEL SECURITY (Optional - for high security environments)
-- ============================================================================
-- Uncomment these policies to make audit logs truly immutable

-- Enable RLS
-- ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;

-- Policy: Allow INSERT only
-- CREATE POLICY audit_insert_only ON audit_logs
--     FOR INSERT
--     WITH CHECK (true);

-- Policy: Deny UPDATE
-- CREATE POLICY audit_no_update ON audit_logs
--     FOR UPDATE
--     USING (false);

-- Policy: Deny DELETE
-- CREATE POLICY audit_no_delete ON audit_logs
--     FOR DELETE
--     USING (false);

-- Policy: Allow SELECT for admins only (adjust as needed)
-- CREATE POLICY audit_read_admin ON audit_logs
--     FOR SELECT
--     USING (true);  -- Adjust based on your auth system

-- ============================================================================
-- COMMENTS FOR DOCUMENTATION
-- ============================================================================

COMMENT ON TABLE audit_logs IS
    'Immutable audit trail for PII access. Required for Gmail API compliance and GDPR. Records are append-only.';

COMMENT ON COLUMN audit_logs.action IS
    'Action performed (e.g., vip_candidates_viewed, gmail_message_sent)';

COMMENT ON COLUMN audit_logs.resource_type IS
    'Type of resource accessed (e.g., vip_contacts, gmail_messages)';

COMMENT ON COLUMN audit_logs.resource_count IS
    'Number of resources accessed in this action';

COMMENT ON COLUMN audit_logs.pii_fields IS
    'JSON array of PII field names accessed (e.g., ["display_name", "email"])';

COMMENT ON COLUMN audit_logs.metadata IS
    'Additional context about the action (JSON)';

-- ============================================================================
-- VERIFICATION
-- ============================================================================
-- Run these queries to verify the migration succeeded:

-- Check table exists
-- SELECT EXISTS (
--     SELECT FROM information_schema.tables
--     WHERE table_name = 'audit_logs'
-- );

-- Check indexes
-- SELECT indexname FROM pg_indexes WHERE tablename = 'audit_logs';

-- Test insert
-- INSERT INTO audit_logs (user_id, action, resource_type, ip_address)
-- VALUES (gen_random_uuid(), 'test_action', 'test_resource', '127.0.0.1'::inet);
-- SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 1;
