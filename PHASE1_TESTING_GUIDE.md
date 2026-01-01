# Phase 1: Audit Logging - Testing Guide

## Overview

This guide walks you through testing the audit logging infrastructure to ensure it's working correctly.

**What we built:**
- ✅ `audit_logs` database table
- ✅ `AuditLogger` service
- ✅ `RequestContextMiddleware` (adds request ID, IP, user-agent)
- ✅ Audit helper utilities
- ✅ Audit logging on VIP endpoints

---

## Prerequisites

Before testing, ensure:
1. Backend is running: `cd app/arch-vox-backend && uvicorn app.main:app --reload`
2. Database is accessible (check `.env.local` for `SUPABASE_DB_URL`)
3. You have a valid JWT token for authentication

---

## Step 1: Run the Database Migration

```bash
# Navigate to backend directory
cd /Users/william/Downloads/voice-gmail-assistant/app/arch-vox-backend

# Run the migration
psql $SUPABASE_DB_URL -f migrations/001_create_audit_logs.sql

# Verify table was created
psql $SUPABASE_DB_URL -c "\dt audit_logs"

# You should see:
#  Schema |    Name     | Type  |  Owner
# --------+-------------+-------+---------
#  public | audit_logs  | table | postgres
```

**Verify indexes:**
```bash
psql $SUPABASE_DB_URL -c "\d audit_logs"

# You should see the table structure and indexes:
# Indexes:
#     "audit_logs_pkey" PRIMARY KEY, btree (id)
#     "idx_audit_logs_action" btree (action)
#     "idx_audit_logs_created_at" btree (created_at DESC)
#     "idx_audit_logs_ip_address" btree (ip_address)
#     "idx_audit_logs_resource_type" btree (resource_type)
#     "idx_audit_logs_user_created" btree (user_id, created_at DESC)
#     "idx_audit_logs_user_id" btree (user_id)
```

---

## Step 2: Test Request Context Middleware

The middleware should automatically add request tracking to all requests.

```bash
# Test any endpoint
curl -v http://localhost:8000/health

# Check response headers - should include:
# X-Request-ID: <uuid>
```

**Expected behavior:**
- Every request gets a unique `X-Request-ID`
- Request ID is logged in application logs
- IP address and user-agent are captured

**Check logs:**
```bash
# In your terminal running uvicorn, you should see:
# {"event": "Request started", "request_id": "abc-123", "method": "GET", "path": "/health", ...}
# {"event": "HTTP request completed", "request_id": "abc-123", ...}
```

---

## Step 3: Test VIP Candidates Endpoint (Audit Logging)

This endpoint accesses PII (display names) and should create an audit log entry.

### Get a Valid JWT Token

```bash
# Option 1: From your iOS app (after OAuth login)
# Copy the JWT token from your app

# Option 2: Use Supabase Auth (if you have test users)
# Get token from Supabase dashboard or auth flow

export JWT_TOKEN="your-jwt-token-here"
```

### Call the VIP Candidates Endpoint

```bash
curl -H "Authorization: Bearer $JWT_TOKEN" \
  http://localhost:8000/onboarding/vips/
```

**Expected response:**
```json
{
  "vips": [
    {
      "contact_hash": "hash123",
      "display_name": "John Doe",
      "vip_score": 0.85,
      "confidence_score": 0.92,
      ...
    },
    ...
  ]
}
```

### Verify Audit Log Created

```bash
# Query the audit_logs table
psql $SUPABASE_DB_URL -c "
SELECT
    action,
    resource_type,
    resource_count,
    pii_fields,
    ip_address,
    user_agent,
    request_id,
    created_at
FROM audit_logs
ORDER BY created_at DESC
LIMIT 1;
"
```

**Expected output:**
```
        action         | resource_type | resource_count |        pii_fields         | ip_address  |  user_agent   |   request_id    |          created_at
-----------------------+---------------+----------------+---------------------------+-------------+---------------+-----------------+-------------------------------
 vip_candidates_viewed | vip_contacts  |             50 | ["display_name", "..."]   | 127.0.0.1   | curl/7.64.1   | abc-def-123...  | 2025-12-31 10:30:00.123456+00
```

**Verify fields:**
- ✅ `action` = "vip_candidates_viewed"
- ✅ `resource_type` = "vip_contacts"
- ✅ `resource_count` = number of candidates returned
- ✅ `pii_fields` = ["display_name", "contact_hash"]
- ✅ `ip_address` = your IP (127.0.0.1 for local)
- ✅ `user_agent` = your client's user agent
- ✅ `request_id` = UUID matching X-Request-ID header
- ✅ `created_at` = current timestamp

---

## Step 4: Test VIP Selection Endpoint (Audit Logging)

This endpoint modifies user data and should create an audit log entry.

```bash
curl -X POST \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "contacts": ["hash1", "hash2", "hash3"]
  }' \
  http://localhost:8000/onboarding/vips/selection
```

**Expected response:**
- HTTP 204 No Content

### Verify Audit Log Created

```bash
psql $SUPABASE_DB_URL -c "
SELECT
    action,
    resource_type,
    metadata,
    created_at
FROM audit_logs
WHERE action = 'vip_selection_saved'
ORDER BY created_at DESC
LIMIT 1;
"
```

**Expected output:**
```
       action        | resource_type |                metadata                |          created_at
---------------------+---------------+----------------------------------------+-------------------------------
 vip_selection_saved | vip_selections| {"changes": {"vip_count": 3, ...}}     | 2025-12-31 10:31:00.123456+00
```

---

## Step 5: Test Audit Log Query Performance

Verify that indexes are working properly.

```bash
# Explain query plan for user lookup
psql $SUPABASE_DB_URL -c "
EXPLAIN ANALYZE
SELECT * FROM audit_logs
WHERE user_id = 'your-user-id'
ORDER BY created_at DESC
LIMIT 10;
"
```

**Expected output should include:**
```
Index Scan using idx_audit_logs_user_created on audit_logs
```

This confirms the composite index is being used.

---

## Step 6: Test Audit Logging Failure Resilience

The audit logger should **never** fail the request if logging fails.

### Simulate Database Failure

```bash
# Option 1: Temporarily break database connection
# In .env.local, set invalid SUPABASE_DB_URL

# Option 2: Drop the audit_logs table temporarily
psql $SUPABASE_DB_URL -c "DROP TABLE audit_logs;"

# Call the VIP endpoint
curl -H "Authorization: Bearer $JWT_TOKEN" \
  http://localhost:8000/onboarding/vips/
```

**Expected behavior:**
- ✅ Endpoint still returns 200 OK with VIP data
- ✅ Error is logged to stdout (structured logs)
- ✅ User is NOT affected by audit logging failure

**Check logs for error:**
```bash
# You should see in logs:
# {"event": "CRITICAL: Failed to write audit log to database", "error": "...", ...}
```

### Restore Database

```bash
# Re-run migration to recreate table
psql $SUPABASE_DB_URL -f migrations/001_create_audit_logs.sql
```

---

## Step 7: Test Request ID Propagation

Request ID should be consistent across all logs for a single request.

```bash
# Call endpoint and capture request ID
RESPONSE=$(curl -v http://localhost:8000/onboarding/vips/ \
  -H "Authorization: Bearer $JWT_TOKEN" 2>&1)

# Extract X-Request-ID from response headers
REQUEST_ID=$(echo "$RESPONSE" | grep -i "X-Request-ID" | awk '{print $3}')

echo "Request ID: $REQUEST_ID"

# Query audit log by request ID
psql $SUPABASE_DB_URL -c "
SELECT action, resource_type, created_at
FROM audit_logs
WHERE request_id = '$REQUEST_ID';
"
```

**Expected:**
- Audit log entry has same request_id as response header

---

## Step 8: Verify Structured Logging

Check that audit events appear in structured logs (stdout).

```bash
# In your uvicorn terminal, after calling VIP endpoint, you should see:

{
  "event": "Audit event",
  "audit_action": "vip_candidates_viewed",
  "user_id": "user-123",
  "resource_type": "vip_contacts",
  "resource_count": 50,
  "pii_fields": ["display_name", "contact_hash"],
  "ip_address": "127.0.0.1",
  "request_id": "abc-def-123",
  "timestamp": "2025-12-31T10:30:00.123456Z"
}
```

---

## Step 9: Test Audit Log Queries (Common Patterns)

### Query 1: All actions by a user
```sql
SELECT action, resource_type, created_at
FROM audit_logs
WHERE user_id = 'your-user-id'
ORDER BY created_at DESC
LIMIT 20;
```

### Query 2: All PII access events
```sql
SELECT user_id, action, pii_fields, created_at
FROM audit_logs
WHERE pii_fields IS NOT NULL
ORDER BY created_at DESC
LIMIT 50;
```

### Query 3: Specific action count
```sql
SELECT COUNT(*)
FROM audit_logs
WHERE action = 'vip_candidates_viewed';
```

### Query 4: Actions by IP address
```sql
SELECT user_id, action, created_at
FROM audit_logs
WHERE ip_address = '127.0.0.1'
ORDER BY created_at DESC;
```

### Query 5: Recent security events
```sql
SELECT *
FROM audit_logs
WHERE action = 'security_event'
ORDER BY created_at DESC
LIMIT 10;
```

---

## Step 10: Integration Test Checklist

Run through this checklist to verify everything works:

- [ ] **Migration ran successfully**
  - `audit_logs` table exists
  - All indexes created

- [ ] **Request Context Middleware**
  - X-Request-ID header in responses
  - Request ID in logs
  - IP address captured correctly

- [ ] **VIP Candidates Endpoint**
  - Returns VIP data
  - Audit log created in database
  - Structured log event emitted
  - Request ID matches

- [ ] **VIP Selection Endpoint**
  - Saves selection
  - Audit log created
  - Metadata includes changes

- [ ] **Audit Logger Resilience**
  - Request succeeds even if audit logging fails
  - Error is logged but not propagated

- [ ] **Performance**
  - Indexes are used in queries
  - Queries are fast (<10ms)

- [ ] **Structured Logs**
  - Audit events appear in stdout
  - JSON formatted
  - Includes all context

---

## Troubleshooting

### Issue: Migration fails with "table already exists"

**Solution:**
```bash
# Drop and recreate
psql $SUPABASE_DB_URL -c "DROP TABLE IF EXISTS audit_logs CASCADE;"
psql $SUPABASE_DB_URL -f migrations/001_create_audit_logs.sql
```

### Issue: No audit logs appearing in database

**Checklist:**
1. Check database connection is working
2. Verify migration ran successfully
3. Check application logs for errors
4. Ensure endpoint is actually being called (check 200 response)

### Issue: Request ID not appearing in response headers

**Checklist:**
1. Verify `RequestContextMiddleware` is added to app
2. Check middleware order in `main.py`
3. Restart uvicorn server

### Issue: Audit log missing fields (NULL values)

**Cause:** Request context not populated

**Solution:**
1. Ensure `RequestContextMiddleware` runs FIRST
2. Verify `request.state.request_id` exists in endpoint
3. Check middleware is properly imported

---

## Next Steps

After Phase 1 is verified:

1. **Add audit logging to other endpoints** (Gmail, Calendar)
   - Copy the pattern from VIP endpoints
   - Use `audit_pii_access()` helper for PII
   - Use `audit_gmail_action()` helper for Gmail

2. **Monitor audit logs in production**
   - Export to external logging system (Datadog, CloudWatch)
   - Set up alerts for unusual patterns
   - Regular compliance reviews

3. **Implement Phase 2: Rate Limiting**
   - Build on audit logging foundation
   - Track rate limit violations in audit logs

---

## Success Criteria ✅

Phase 1 is complete when:

- ✅ All VIP endpoints have audit logging
- ✅ Audit logs written to database
- ✅ Structured logs written to stdout
- ✅ Request ID propagation working
- ✅ No failed requests due to audit logging
- ✅ All tests in this guide pass

**Congratulations!** You've built production-ready audit logging! 🎉

This foundation will make future compliance work much easier.
