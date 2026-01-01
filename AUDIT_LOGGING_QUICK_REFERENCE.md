# Audit Logging - Quick Reference

> **Copy-paste patterns for adding audit logging to your endpoints**

---

## 📋 Checklist: When to Audit Log

✅ **ALWAYS audit log when:**
- Accessing PII (display names, email addresses, phone numbers)
- Reading user emails/messages
- Sending emails on behalf of user
- Accessing calendar events
- Modifying user data
- Deleting user data
- High-risk actions (password reset, OAuth, etc.)

❌ **Don't audit log for:**
- Health checks (`/health`)
- Public endpoints (no user data)
- Non-PII data (aggregated stats, counts)

---

## 🚀 Quick Start: 3-Line Pattern

```python
from fastapi import Request
from app.utils.audit_helpers import audit_pii_access

@router.get("/my-endpoint")
async def my_endpoint(
    request: Request,  # 1️⃣ Add this
    claims: dict = Depends(auth_dependency),
):
    user_id = claims["sub"]
    data = await get_data(user_id)

    # 2️⃣ Add this
    await audit_pii_access(request, user_id, "data_viewed", resource_count=len(data))

    return data  # 3️⃣ Done!
```

---

## 📚 Helper Functions

### 1. PII Access (Most Common)

```python
from app.utils.audit_helpers import audit_pii_access

await audit_pii_access(
    request=request,
    user_id=user_id,
    action="vips_viewed",              # What happened
    resource_type="vip_contacts",      # Type of resource
    resource_count=50,                 # How many
    pii_fields=["display_name"],       # What PII fields
)
```

### 2. Data Modification

```python
from app.utils.audit_helpers import audit_data_modification

await audit_data_modification(
    request=request,
    user_id=user_id,
    action="profile_updated",
    resource_type="user_profile",
    changes={"name": {"old": old_name, "new": new_name}},
)
```

### 3. Gmail Actions

```python
from app.utils.audit_helpers import audit_gmail_action

await audit_gmail_action(
    request=request,
    user_id=user_id,
    action="email_sent",
    message_id=msg_id,
    metadata={"to": email.to, "subject": email.subject},
)
```

### 4. Security Events

```python
from app.utils.audit_helpers import audit_security_event

await audit_security_event(
    request=request,
    event_type="rate_limit_exceeded",
    severity="medium",
    description="User exceeded rate limit",
    user_id=user_id,
)
```

---

## 📖 Common Patterns

### Pattern: List Endpoint (Reading PII)

```python
@router.get("/contacts/")
async def list_contacts(
    request: Request,
    claims: dict = Depends(auth_dependency),
):
    user_id = claims["sub"]
    contacts = await get_contacts(user_id)

    await audit_pii_access(
        request=request,
        user_id=user_id,
        action="contacts_viewed",
        resource_type="contacts",
        resource_count=len(contacts),
        pii_fields=["name", "email"],
    )

    return {"contacts": contacts}
```

### Pattern: Get Single Item (Reading PII)

```python
@router.get("/contacts/{contact_id}")
async def get_contact(
    request: Request,
    contact_id: str,
    claims: dict = Depends(auth_dependency),
):
    user_id = claims["sub"]
    contact = await get_contact_by_id(user_id, contact_id)

    await audit_pii_access(
        request=request,
        user_id=user_id,
        action="contact_viewed",
        resource_type="contact",
        resource_id=contact_id,
        pii_fields=["name", "email", "phone"],
    )

    return contact
```

### Pattern: Create Endpoint (Data Modification)

```python
@router.post("/contacts/")
async def create_contact(
    request: Request,
    contact: ContactCreate,
    claims: dict = Depends(auth_dependency),
):
    user_id = claims["sub"]
    new_contact = await create_new_contact(user_id, contact)

    await audit_data_modification(
        request=request,
        user_id=user_id,
        action="contact_created",
        resource_type="contact",
        resource_id=new_contact.id,
        changes={"created_at": "now", "name": contact.name},
    )

    return new_contact
```

### Pattern: Update Endpoint (Data Modification)

```python
@router.put("/contacts/{contact_id}")
async def update_contact(
    request: Request,
    contact_id: str,
    contact_update: ContactUpdate,
    claims: dict = Depends(auth_dependency),
):
    user_id = claims["sub"]

    old_contact = await get_contact_by_id(user_id, contact_id)
    updated_contact = await update_contact_data(user_id, contact_id, contact_update)

    await audit_data_modification(
        request=request,
        user_id=user_id,
        action="contact_updated",
        resource_type="contact",
        resource_id=contact_id,
        changes={
            "name": {"old": old_contact.name, "new": updated_contact.name},
            "email": {"old": old_contact.email, "new": updated_contact.email},
        },
    )

    return updated_contact
```

### Pattern: Delete Endpoint (Data Modification)

```python
@router.delete("/contacts/{contact_id}")
async def delete_contact(
    request: Request,
    contact_id: str,
    claims: dict = Depends(auth_dependency),
):
    user_id = claims["sub"]

    contact = await get_contact_by_id(user_id, contact_id)
    await delete_contact_data(user_id, contact_id)

    await audit_data_modification(
        request=request,
        user_id=user_id,
        action="contact_deleted",
        resource_type="contact",
        resource_id=contact_id,
        changes={"deleted_at": "now", "name": contact.name},
    )

    return {"success": True}
```

### Pattern: Gmail Send Email

```python
@router.post("/gmail/send")
async def send_email(
    request: Request,
    email: EmailRequest,
    claims: dict = Depends(auth_dependency),
):
    user_id = claims["sub"]
    message_id = await gmail_service.send_email(user_id, email)

    await audit_gmail_action(
        request=request,
        user_id=user_id,
        action="email_sent",
        message_id=message_id,
        metadata={
            "to": email.to,
            "subject": email.subject,
            "has_attachments": len(email.attachments) > 0,
        },
    )

    return {"message_id": message_id}
```

### Pattern: Gmail List Messages

```python
@router.get("/gmail/messages")
async def list_messages(
    request: Request,
    claims: dict = Depends(auth_dependency),
    limit: int = Query(10, ge=1, le=100),
):
    user_id = claims["sub"]
    messages = await gmail_service.list_messages(user_id, limit)

    await audit_gmail_action(
        request=request,
        user_id=user_id,
        action="messages_listed",
        message_count=len(messages),
        metadata={"limit": limit},
    )

    return {"messages": messages}
```

---

## 🎯 Action Naming Convention

**Format:** `{resource}_{action}`

**Examples:**
- `vip_candidates_viewed`
- `vip_selection_saved`
- `gmail_email_sent`
- `gmail_messages_listed`
- `profile_updated`
- `contact_deleted`

**Common actions:**
- `viewed` - Read/accessed data
- `listed` - Fetched multiple items
- `created` - Created new resource
- `updated` - Modified existing resource
- `deleted` - Removed resource
- `sent` - Sent email/message
- `imported` - Imported data
- `exported` - Exported data

---

## 🔍 PII Fields Reference

**Common PII fields to track:**
- `display_name` - User's name
- `email` - Email address
- `phone` - Phone number
- `subject` - Email subject
- `body` - Email body
- `sender` - Email sender
- `recipients` - Email recipients
- `contact_hash` - Hashed contact identifier
- `address` - Physical address
- `message_content` - Message text

---

## ⚠️ Common Mistakes

### ❌ Don't do this:
```python
# Missing Request parameter
async def my_endpoint(claims: dict = Depends(auth_dependency)):
    # Error: request is undefined
    await audit_pii_access(request=request, ...)
```

### ✅ Do this:
```python
# Include Request parameter
async def my_endpoint(
    request: Request,  # ✅ Add this
    claims: dict = Depends(auth_dependency),
):
    await audit_pii_access(request=request, ...)
```

---

### ❌ Don't do this:
```python
# Audit logging BEFORE getting data
await audit_pii_access(request, user_id, "data_viewed", resource_count=0)
data = await get_data(user_id)  # What if this fails?
```

### ✅ Do this:
```python
# Audit logging AFTER getting data
data = await get_data(user_id)
await audit_pii_access(request, user_id, "data_viewed", resource_count=len(data))
```

---

### ❌ Don't do this:
```python
# Forgetting to track what PII was accessed
await audit_pii_access(request, user_id, "contacts_viewed")
# Missing: pii_fields parameter
```

### ✅ Do this:
```python
# Always specify PII fields accessed
await audit_pii_access(
    request, user_id, "contacts_viewed",
    pii_fields=["name", "email", "phone"],  # ✅ Explicit
)
```

---

## 💡 Pro Tips

1. **Always audit AFTER the action succeeds**
   - If the action fails, you don't want to log it

2. **Include meaningful metadata**
   - Future you will thank you during investigations

3. **Use descriptive action names**
   - `email_sent` is better than `action_performed`

4. **Track resource counts**
   - Helps identify unusual patterns (scraping, abuse)

5. **Log deletions with details**
   - You can't query deleted data, so log what was deleted

---

## 📊 Querying Audit Logs

```sql
-- Recent actions by user
SELECT action, resource_type, created_at
FROM audit_logs
WHERE user_id = 'user-id-here'
ORDER BY created_at DESC
LIMIT 20;

-- All PII access today
SELECT user_id, action, pii_fields, created_at
FROM audit_logs
WHERE pii_fields IS NOT NULL
  AND created_at > NOW() - INTERVAL '1 day'
ORDER BY created_at DESC;

-- Actions by specific IP
SELECT user_id, action, created_at
FROM audit_logs
WHERE ip_address = '192.168.1.1'
ORDER BY created_at DESC;

-- Find security events
SELECT *
FROM audit_logs
WHERE action = 'security_event'
ORDER BY created_at DESC;
```

---

## 🚀 Next Feature Checklist

When adding a new feature:

- [ ] Does it access PII?
  - [ ] Add `request: Request` parameter
  - [ ] Call `audit_pii_access()` after accessing data
  - [ ] Specify `pii_fields` parameter

- [ ] Does it modify data?
  - [ ] Add `request: Request` parameter
  - [ ] Call `audit_data_modification()` after modification
  - [ ] Include `changes` parameter

- [ ] Is it a Gmail action?
  - [ ] Use `audit_gmail_action()` helper

- [ ] Is it a security event?
  - [ ] Use `audit_security_event()` helper

---

## 📞 Need Help?

**Documentation:**
- Full guide: [PHASE1_IMPLEMENTATION_SUMMARY.md](PHASE1_IMPLEMENTATION_SUMMARY.md)
- Testing: [PHASE1_TESTING_GUIDE.md](PHASE1_TESTING_GUIDE.md)

**Code references:**
- Audit logger: `app/infrastructure/audit/audit_logger.py`
- Helpers: `app/utils/audit_helpers.py`
- Example: `app/features/vip_onboarding/api/router.py`

---

**Last Updated:** 2025-12-31
**Version:** 1.0.0
