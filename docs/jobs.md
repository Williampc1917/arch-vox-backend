## Background job shortcuts

You can now launch any of the three background jobs via `make` in the backend root:

| Command              | Runs                                                                             |
|----------------------|----------------------------------------------------------------------------------|
| `make run-vip-worker`    | VIP backfill worker (`SERVICE_ROLE=worker`, `WORKER_JOB=vip_backfill`)               |
| `make run-token-worker`  | Token refresh scheduler (`SERVICE_ROLE=worker`, `WORKER_JOB=token_refresh`, flag on) |
| `make run-oauth-worker`  | OAuth cleanup scheduler (`SERVICE_ROLE=worker`, `WORKER_JOB=oauth_cleanup`)          |

These targets mirror `make run` (which starts FastAPI) but pass the correct env vars so each job behaves the same locally as it will in Docker/production. Use them in separate terminals if you want to watch logs per worker. 
