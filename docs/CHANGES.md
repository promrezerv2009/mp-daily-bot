# Changelog

## Phase 0 - SDK and Middleware Setup
- Confirmed `/openapi.json` is exposed for schema generation.
- Scaffolded `packages/sdk` with OpenAPI type generation script and fetch helper.
- Enabled configurable CORS support via `CORS_ORIGINS` environment variable.
- Added refresh-token middleware stub (`RefreshTokenMiddleware`) with TODO for real logic.

## Phase 1 - Mobile API Scaffold
- Introduced `/api/mobile` router (summary, products, stock forecast, break-even) with tenant-aware stubs.
- Added `MobileService` for aggregating metrics using existing repositories.
- Created smoke tests for new endpoints (`tests/test_mobile_endpoints.py`).
- Expanded configuration with `TENANT_HEADER` for future multi-tenant support.

## Phase 2 - Billing and Trials
- Added Alembic migration for `subscriptions` and `trials` tables (tenant-ready).
- Implemented `AccessControlMiddleware` placeholder and `/api/billing/activate` endpoint.
- Extended settings/env for billing provider and trial duration; added smoke test `tests/test_billing.py`.

## Phase 3 - Telegram Bot Enhancements
- Rebuilt `app/bot.py` with `/start`, `/dashboard`, `/forecast`, `/subscribe` commands plus trial activation flow.
- Wired bot to mobile APIs for metrics and forecast responses.
- Added daily digest scheduler job (`send_daily_digest`) delivering summary to configured chat IDs.

## Phase 4 - n8n Stack
- Added `infra/n8n/docker-compose-n8n.yml` with Basic Auth environment variables.
- Documented planned automation flows (webhook billing + morning summary) as TODO.

## Phase 5 - A/B Testing Scaffolding
- Created migrations and models for A/B tests, variants, and results.
- Added service stubs (`app/services/abtest_ozon.py`) and `/api/abtest/*` routes with tests.
- TODO: integrate real Ozon Product/Analytics API interactions.

## Phase 6 - AI Recommendations
- Added rule-based `/api/ai/recommendations` endpoint combining mobile summary/products/forecast.
- TODO: replace heuristics with LLM-powered suggestions and introduce caching.
