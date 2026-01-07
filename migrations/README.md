# Database Migrations

This folder contains SQL migrations for the voice-gmail-assistant database.

## Running Migrations

### Local Development

```bash
# Run migration against your local database
psql $SUPABASE_DB_URL -f migrations/001_create_audit_logs.sql
```

### Production

```bash
# Set production database URL
export PROD_DB_URL="postgresql://..."

# Run migration
psql $PROD_DB_URL -f migrations/001_create_audit_logs.sql
```

## Migration Files

- `001_create_audit_logs.sql` - Creates audit_logs table for PII access tracking (Gmail API compliance)
- `002_create_contact_identities.sql` - Creates contact_identities table for encrypted emails/display names
- `003_add_contact_domain_shared_inbox.sql` - Adds contacts.email_domain + contacts.is_shared_inbox

## Migration Checklist

Before running a migration:
- [ ] Review the SQL file
- [ ] Test on local database first
- [ ] Backup production database
- [ ] Run during low-traffic period
- [ ] Verify migration succeeded

After running a migration:
- [ ] Check table exists: `\dt audit_logs`
- [ ] Check indexes: `\di audit_logs*`
- [ ] Test insert/select operations
- [ ] Update application code to use new schema
