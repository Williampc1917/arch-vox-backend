# VIP Onboarding Feature

This module owns the VIP onboarding pipeline end-to-end: collect Gmail/Calendar
metadata, aggregate it into per-contact stats, score/rank top contacts, and
store the user-selected VIP list.

This is a metadata-only flow. Email addresses are hashed and no message content
is stored.

## Current Working Flow (Happy Path)

1) Gmail OAuth callback enqueues a VIP backfill job when enabled.
   - Entry point: app/arch-vox-backend/app/routes/gmail_auth/oauth.py
   - Trigger: settings.VIP_BACKFILL_ENABLED

2) Scheduler dedupes and enqueues job ID into Redis.
   - app/arch-vox-backend/app/features/vip_onboarding/services/scheduler.py

3) Worker pulls job ID from Redis and runs the pipeline.
   - app/arch-vox-backend/app/features/vip_onboarding/jobs/backfill_job.py
   - Steps:
     - mark job running
     - backfill metadata (Gmail + Calendar)
     - aggregate contacts
     - mark job completed/failed

4) Backfill collects metadata and persists raw rows.
   - app/arch-vox-backend/app/features/vip_onboarding/services/backfill_service.py
   - Writes to:
     - email_metadata
     - events_metadata
   - Uses hashing for email addresses and does not store content.

5) Aggregation computes per-contact stats and upserts contacts.
   - app/arch-vox-backend/app/features/vip_onboarding/pipeline/aggregation/service.py
   - Writes to:
     - contacts

6) API returns candidates and accepts final selection.
   - app/arch-vox-backend/app/features/vip_onboarding/api/router.py
   - GET /onboarding/vips -> runs scoring on demand
   - POST /onboarding/vips/selection -> stores VIP picks

7) Scoring ranks contacts and persists scores.
   - app/arch-vox-backend/app/features/vip_onboarding/pipeline/scoring/service.py
   - Writes to:
     - contacts.vip_score
     - vip_list (final selection)

## How Files Connect

High-level dependency flow:

routes/gmail_auth/oauth.py
  -> services/scheduler.enqueue_vip_backfill_job
      -> repository/vip_repository.create_job
      -> redis queue (VIP_BACKFILL_QUEUE_NAME)
          -> jobs/backfill_job._process_job
              -> services/backfill_service.VipBackfillService.run
                  -> repository/vip_repository.record_email_metadata
                  -> repository/vip_repository.record_event_metadata
              -> pipeline/aggregation.ContactAggregationService.aggregate_contacts_for_user
                  -> pipeline/aggregation.repository.ContactAggregationRepository.fetch_*
                  -> pipeline/aggregation.repository.ContactAggregationRepository.upsert_contacts

API usage:

api/router.py (GET /onboarding/vips)
  -> pipeline/scoring.scoring_service.score_contacts_for_user
      -> pipeline/scoring.repository.VipScoringRepository.fetch_contacts
      -> pipeline/scoring.repository.VipScoringRepository.update_contact_scores

api/router.py (POST /onboarding/vips/selection)
  -> pipeline/scoring.scoring_service.save_vip_selection
      -> pipeline/scoring.repository.VipScoringRepository.replace_vip_selection

## Key Limits and Defaults

- Scoring candidate list default: 50 (query param limit; max 100)
  - app/arch-vox-backend/app/features/vip_onboarding/api/router.py
  - app/arch-vox-backend/app/features/vip_onboarding/pipeline/scoring/service.py

- Max selected VIP contacts: 20
  - app/arch-vox-backend/app/features/vip_onboarding/pipeline/scoring/service.py

- Lookback windows:
  - Email: 30 days
  - Calendar: 30 days lookback, 2 days lookahead
  - app/arch-vox-backend/app/features/vip_onboarding/services/backfill_service.py
  - app/arch-vox-backend/app/features/vip_onboarding/pipeline/aggregation/service.py

## API Endpoints

- GET /onboarding/vips/status
  - Returns onboarding step + whether contacts exist
  - app/arch-vox-backend/app/features/vip_onboarding/api/router.py

- GET /onboarding/vips
  - Returns scored VIP candidates
  - app/arch-vox-backend/app/features/vip_onboarding/api/router.py

- POST /onboarding/vips/selection
  - Accepts contact_hashes, stores vip_list with rank order
  - app/arch-vox-backend/app/features/vip_onboarding/api/router.py

## Data Tables (expected)

- user_vip_backfill_jobs
- email_metadata
- events_metadata
- contacts
- vip_list

## Notes on Current Behavior

- Scoring runs on-demand when the GET endpoint is called (not precomputed).
- Backfill uses pruning before inserting new metadata for the window.
- Calendar data is skipped when scopes are missing; Gmail data is skipped when
  scopes are missing; backfill errors fail the job. In practice:
  - If OAuth tokens lack Gmail scopes, Gmail collection is skipped and no
    email_metadata rows are written.
  - If OAuth tokens lack Calendar scopes, Calendar collection is skipped and no
    events_metadata rows are written.
  - If a hard error occurs (missing/expired tokens or API errors), the worker
    marks the job as failed and aggregation does not run.
