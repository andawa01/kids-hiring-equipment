# PartyPlay Backend — Gap Analysis & Improvement Plan

**Date:** 2026-11-25
**Scope:** `partyplay-backend` (FastAPI + SQLAlchemy 2.0 + PostgreSQL API, `backend/` + `docker-compose.yml`)
**Method:** Full read-through of every source file, schema, migration, test, config and container definition; verification of runtime behaviour against the running dev database (`alembic check`, index/constraint inspection, `pytest`).

> **Out of scope:** the separate frontend project at `~/projects/partyplay` (Node/React) was **not** reviewed. Items that depend on the client contract (Decimal-as-string, cookie/CORS behaviour, `/api/v1` base URL) are flagged as cross-cutting and should be re-checked there.

---

## How to read this document

| Field | Meaning |
|---|---|
| **Severity** | `Critical` = data loss/security exposure or blocks production · `High` = serious correctness/security/scale risk · `Medium` = notable gap or operational risk · `Low` = polish/hardening |
| **Effort** | `S` ≤ half a day · `M` 1–3 days · `L` > 3 days |

The codebase is a clean, well-structured MVP. The booking/concurrency core is genuinely good (row-locked availability checks with real multi-threaded tests). The gaps below are mostly about **production hardening, feature completeness and scale**, not about broken foundations.

---

## Executive summary — top 10 actions

| # | Action | Why it matters | Refs |
|---|---|---|---|
| 1 | Refuse to boot with the default `JWT_SECRET`; add an `ENVIRONMENT` switch that also forces `COOKIE_SECURE=true` and disables `/docs` | A default secret lets anyone mint admin tokens | SEC-1, OPS-2 |
| 2 | Add CSRF protection and login rate limiting to the cookie-authenticated admin surface | Cookie auth + no CSRF + no throttling = account takeover / brute force | SEC-2, SEC-3 |
| 3 | Prevent inventory from being lowered below already-held stock | Admin can create overbookings silently | BOOK-1 |
| 4 | Add idempotency to `POST /bookings` | Double-submits/retries create duplicate bookings | BOOK-6 |
| 5 | Apply offers/discounts in pricing and add payment endpoints | Core commercial features are stubbed/absent | BOOK-10, BOOK-11 |
| 6 | Paginate all list endpoints | Admin booking list and catalogue will degrade and can be dumped wholesale | API-1 |
| 7 | Add indexes on the 9 unindexed FK columns | Every join/lookup will degrade as data grows (verified in DB) | DB-1 |
| 8 | Run migrations in tests (not `create_all`) | Migrations can drift from models without tests noticing | TEST-1 |
| 9 | Add a production Dockerfile, CI, pinned dependencies and structured logging | No reproducible build, no quality gate, no observability | OPS-3, OPS-4, OPS-5, OPS-7 |
| 10 | Stop putting the customer phone in a query string | PII leaks into access logs, browser history and referrers | SEC-5 |

---

## Architecture snapshot (what exists today)

```
app/
  main.py            FastAPI app, CORS, error handlers, /health
  core/              config (pydantic-settings), db (sync SQLAlchemy), security (Argon2 + JWT), errors
  api/               public.py (catalogue + bookings), admin.py (auth, bookings, equipment/inventory), deps.py
  models/            Admin, Customer, Booking(+items/packages/history), Payment, catalog
  schemas/           Pydantic I/O models
  services/          availability.py (locking + peak usage), pricing.py, bookings.py (lifecycle)
  tasks/             expire_holds.py (manual/cron entry point)
  scripts/           create_admin.py, seed.py
alembic/             one initial migration (UUID ids)
tests/               31 tests incl. real threaded concurrency tests
```

**Endpoints (20):** catalogue reads, availability, offers, transport, public settings, create/lookup booking; admin login/logout/me, booking list/approve/reject/cancel, equipment list/create/update, inventory update.

**Verified healthy:** `pytest` → 31 passed; `alembic check` → no drift; availability correctly uses **peak** daily usage and `SELECT … FOR UPDATE` in sorted order to avoid deadlocks.

---

## 1. Security & authentication

| ID | Severity | Gap / risk | Evidence | Recommended fix |
|---|---|---|---|---|
| SEC-1 | **Critical** | `JWT_SECRET` defaults to `"change-me-in-production"`; nothing stops production boot with it | `app/core/config.py:10` | Fail startup if secret is default/short; generate in `.env.example`; add secret-rotation note |
| SEC-2 | **High** | Admin writes are authenticated by a cookie but have **no CSRF defence** (no token, no Origin/Referer check) | `app/api/admin.py` (all POST/PUT), `app/api/deps.py` | Double-submit CSRF token or strict Origin check; `SameSite=Strict` for admin cookie |
| SEC-3 | **High** | No login rate limiting, lockout or backoff | `app/api/admin.py:23` | Per-IP + per-email throttle (Redis or DB), exponential backoff, audit failed logins |
| SEC-4 | **Medium** | Logout only deletes the cookie; the JWT stays valid until expiry. No `jti`, denylist or token version | `app/api/admin.py:36`, `app/core/security.py` | Add `jti` + revocation store, or a per-admin `token_version` bumped on logout/password change |
| SEC-5 | **Medium** | Booking lookup sends the **phone number in the query string** (`GET /bookings/{ref}?phone=…`) | `app/api/public.py:107` | Move lookup to `POST` with body, or accept phone via header; never log query strings containing PII |
| SEC-6 | **Medium** | `COOKIE_SECURE` defaults to `false`; no HSTS/CSP/`X-Content-Type-Options`; no HTTPS redirect | `app/core/config.py:13`, `app/main.py` | Environment-driven secure cookie, security-headers middleware, force HTTPS in prod |
| SEC-7 | **Medium** | Password policy is length-only (≥10); no reset flow, no 2FA, no breached-password check | `app/scripts/create_admin.py` | Add reset flow + optional TOTP; zxcvbn/breach check; document policy |
| SEC-8 | **Low** | Login returns faster for unknown emails (skips Argon2 verify) — account enumeration side-channel | `app/api/admin.py:23` | Always run a dummy verify; uniform error/timing |
| SEC-9 | **Low** | CORS allows all methods/headers with credentials | `app/main.py` | Restrict to needed methods/headers |
| SEC-10 | **Medium** | No request-body size limit or list-length cap (`items`/`packages` arrays unbounded per request) | `app/schemas/booking.py` | Cap array sizes, add reverse-proxy body limit |
| SEC-11 | **Medium** | No admin management API (create/disable/rotate); only a CLI script | `app/scripts/create_admin.py` | Admin CRUD + disable/last-login; protect with super-admin role |
| SEC-12 | **Medium** | Short booking reference + phone can be brute-forced; no rate limit on lookup | `app/api/public.py:107` | Rate-limit lookups; consider longer reference or OTP |

---

## 2. Booking domain — correctness & concurrency

| ID | Severity | Gap / risk | Evidence | Recommended fix |
|---|---|---|---|---|
| BOOK-1 | **High** | `PUT /inventory/{id}` can set stock **below existing holds**; no validation against future/active bookings → silent overbooking | `app/api/admin.py:110` | Reject decreases below peak held units for upcoming dates; return a conflict with details |
| BOOK-2 | **Medium** | A package whose equipment is deactivated (or has no inventory row) can still be booked | `app/services/bookings.py:88` (only filters `Package.is_active`) | Join/validate package items’ equipment `is_active`; block or warn |
| BOOK-3 | **Low** | Reference generation is check-then-insert with no retry; a rare unique collision becomes a 500 | `app/services/bookings.py:60` | Catch `IntegrityError` and retry (bounded), or use a DB sequence/ULID |
| BOOK-4 | **Medium** | Customer “upsert by phone” is racy and `customers.phone` is **not unique** → duplicate customers under concurrency | `app/services/bookings.py:88`, `app/models/booking.py` | Unique constraint on normalized phone + `ON CONFLICT` upsert |
| BOOK-5 | **Low** | Every booking overwrites the stored customer name/email | `app/services/bookings.py` | Treat customer as its own editable record; only fill blanks |
| BOOK-6 | **High** | No idempotency: client retries/double-clicks create duplicate bookings (and duplicate holds) | `app/api/public.py:101` | Accept `Idempotency-Key`, store key→booking, return the same result |
| BOOK-7 | **Medium** | A pending booking whose hold has lapsed can still be **approved** (only availability is re-checked) | `app/services/bookings.py:210` | Reject/expire lapsed pending bookings on approve, or require explicit override |
| BOOK-8 | **Medium** | Customers cannot cancel or reschedule; only admins can cancel | `app/api/public.py`, `app/services/bookings.py` | Add customer cancel/reschedule with the same reference+phone auth and policy rules |
| BOOK-9 | **Low** | No automatic `approved → completed` transition after the end date | `app/services/bookings.py` | Add a scheduled completion task alongside `expire_holds` |
| BOOK-10 | **High** (feature) | Offers/discounts are stored but **never applied** to `total_amount` | `app/services/pricing.py:10` | Implement discount evaluation (type, window, stacking, caps) and snapshot applied discounts |
| BOOK-11 | **High** (feature) | `payments` table exists but has no endpoints or reconciliation | `app/models/booking.py`, `app/api` | Add payment record/list, balance/paid state, M-Pesa callback handling, idempotent references |
| BOOK-12 | **Medium** | No minimum lead time, maximum advance horizon, or blackout/holiday dates | `app/services/bookings.py:88` | Add configurable rules and validation |
| BOOK-13 | **Low** (design) | Double-booking is prevented only by application locks; any writer bypassing the service can violate it | `app/services/availability.py` | Document the invariant; optionally add a DB-level guard/trigger or serializable isolation |
| BOOK-14 | **Medium** | Availability expands every overlapping booking **day-by-day in Python**, one query per equipment (N+1) | `app/services/availability.py:54,105` | Aggregate per-day usage in SQL (generate_series/CTE) or cache; batch all equipment in one query |
| BOOK-15 | **Medium** | `list_offers` uses server-local `date.today()` while booking logic uses the configured `Africa/Nairobi` timezone | `app/api/public.py:79` vs `app/services/bookings.py:88` | Use one timezone-aware “today” helper everywhere |
| BOOK-16 | **Low** | Package contents/price are not validated (e.g. empty package, zero price, item qty vs inventory) | `app/models/catalog.py`, admin (no package CRUD) | Validate on write once package CRUD exists |
| BOOK-17 | **Low** | No tax/fees, no currency field; KES is implicit | `app/models/*` | Add currency + tax/fee lines if multi-currency or invoicing is planned |

---

## 3. Data model & database

| ID | Severity | Gap / risk | Evidence | Recommended fix |
|---|---|---|---|---|
| DB-1 | **Medium** | **9 FK columns have no index** (Postgres does not auto-index FKs) | Verified in DB: `bookings.customer_id`, `booking_items.booking_id`, `booking_items.package_id`, `booking_packages.booking_id`, `booking_packages.package_id`, `booking_status_history.booking_id`, `booking_status_history.changed_by_admin_id`, `payments.booking_id`, `package_items.equipment_id` | Add indexes in a new migration |
| DB-2 | **Medium** | Status/type fields are free-text strings with no CHECK constraint: `bookings.status`, `offers.discount_type`, `payments.method`, `transport_providers.type`, `packages.age_category` | `app/models/booking.py`, `app/models/catalog.py` | Add CHECK constraints (or native PG enums) matching the Python enums |
| DB-3 | **Low** | `AgeCategory` enum is defined but the model stores a plain `str` | `app/models/catalog.py` | Use the enum type consistently or remove it |
| DB-4 | **Low** | Money columns have no currency | `app/models/booking.py` | Add `currency` (default `KES`) if needed |
| DB-5 | **Low** | `updated_at` relies on SQLAlchemy `onupdate`, not a DB trigger; raw SQL bypasses it | `app/models/base.py` | Add a DB trigger or accept the limitation |
| DB-6 | **Medium** | No audit trail for catalogue/inventory/price changes (only booking status history) | `app/api/admin.py` | Add an `audit_log` table for admin mutations |
| DB-7 | **Medium** | `Payment` lacks status, provider reference uniqueness, idempotency and reconciliation fields | `app/models/booking.py` | Extend model before building payments |
| DB-8 | **Low** | No soft delete; FK `RESTRICT` means equipment/packages referenced by bookings can never be removed | `app/models/catalog.py` | Use `is_active` consistently (already present) and document; avoid hard deletes |
| DB-9 | **Low** | No data-retention/anonymisation policy for customer PII | `app/models/booking.py` | Add retention job + documented policy |

---

## 4. API design & contract

| ID | Severity | Gap / risk | Evidence | Recommended fix |
|---|---|---|---|---|
| API-1 | **High** | **No pagination** on `GET /admin/bookings`, `/equipment`, `/packages`, `/offers`, `/transport` | `app/api/admin.py:48`, `app/api/public.py` | Add `limit`/`offset` (or cursor) with sane caps and `total` metadata |
| API-2 | **Medium** | N+1 queries when serialising admin booking lists (lazy `item.equipment` per row) | `app/schemas/booking.py` `booking_to_out` | `selectinload`/`joinedload` items+equipment+packages |
| API-3 | **Low** | Partial update is exposed as `PUT` (semantics mismatch) | `app/api/admin.py:96` | Use `PATCH` or require full replacement |
| API-4 | **Low** | Several endpoints lack response models (`/health`, `/settings/public`, login, `/me`, inventory update) | `app/main.py:24`, `app/api/admin.py` | Add explicit response schemas |
| API-5 | **Medium** | No admin “get one booking” or status-history endpoint | `app/api/admin.py` | Add `GET /admin/bookings/{id}` incl. history |
| API-6 | **High** (feature) | No admin CRUD for packages, offers, transport providers or site settings | `app/api/admin.py` | Build the CRUD surface described in the README roadmap |
| API-7 | **Low** | No DELETE endpoints anywhere | `app/api` | Add soft-delete/deactivate endpoints where appropriate |
| API-8 | **Low** | No search/sort beyond `category`/`status` | `app/api` | Add query params (`q`, `sort`, `active`) |
| API-9 | **Medium** | Unhandled exceptions return Starlette’s non-JSON 500, breaking the `{error:{…}}` contract | `app/core/errors.py` | Add a catch-all handler + log with a request ID |
| API-10 | **Low** | No OpenAPI tags/summaries/examples; docs are bare | `app/main.py`, routers | Add tags/descriptions and request examples |
| API-11 | **Medium** | `/health` always returns `ok` without checking the DB | `app/main.py:24` | Split liveness vs readiness; readiness runs `SELECT 1` |
| API-12 | **Low** | No request IDs/correlation IDs in logs or responses | `app/main.py` | Add middleware + include in error payloads |
| API-13 | Info | `201` responses lack a `Location` header | `app/api/public.py:101`, admin create | Add `Location` where useful |
| API-14 | Info | Decimals are serialised as **strings** (e.g. `"10000.00"`); undocumented contract | `app/schemas/*`, verified in tests | Document for the client; keep consistent |
| API-15 | **Low** | No versioning/deprecation policy beyond the `/api/v1` prefix | `app/main.py` | Document the policy before v2 |

---

## 5. Configuration, deployment & operations

| ID | Severity | Gap / risk | Evidence | Recommended fix |
|---|---|---|---|---|
| OPS-1 | **Medium** | `env_file=".env"` is **relative to CWD**; running from the repo root silently used the `localhost:5432` default (this caused a real auth failure) | `app/core/config.py:7` | Resolve `.env` to an absolute path based on the package location |
| OPS-2 | **High** | No `ENVIRONMENT`/mode; no prod guards for default secret, insecure cookie, or exposed `/docs` | `app/core/config.py` | Add `environment: dev\|prod` and enforce safe values at startup |
| OPS-3 | **High** | No application `Dockerfile`, no production server config (workers), only a DB container | `docker-compose.yml` | Add multi-stage Dockerfile + gunicorn/uvicorn workers + app service |
| OPS-4 | **Medium** | Dependencies are unpinned (`>=`) with no lockfile → non-reproducible builds | `backend/requirements*.txt` | Pin versions / add `uv.lock`, `pip-tools` or Poetry |
| OPS-5 | **Medium** | No CI (no `.github/`, no test/lint pipeline) | repo root | Add CI: install, lint, type-check, `alembic upgrade head`, `pytest`, `alembic check` |
| OPS-6 | **Medium** | No lint/format/type config (`.ruff_cache` is git-ignored but ruff isn’t configured/depended on) | repo root | Add `ruff` + `mypy`/`pyright` config and pre-commit |
| OPS-7 | **Medium** | No logging configuration, structured logs, or error tracking | `app/main.py` | JSON logs, request logging, Sentry/OpenTelemetry |
| OPS-8 | **Medium** | `expire_holds` has no scheduler; it only runs when invoked manually | `app/tasks/expire_holds.py`, `docker-compose.yml` | Add cron/systemd timer or a scheduler container |
| OPS-9 | **Medium** | No backup/restore/rollback runbook or migration rollback practice | — | Document DB backups, migration rollout/rollback, restore drills |
| OPS-10 | **Low** | Compose DB has no healthcheck; no app service | `docker-compose.yml` | Add `pg_isready` healthcheck and `depends_on: condition: service_healthy` |
| OPS-11 | **Medium** | `.env.example` drifted from reality: still `5432`, no `TEST_DATABASE_URL`, while compose maps `5434` | `backend/.env.example`, `docker-compose.yml` | Update `.env.example` (and document the test DB) |
| OPS-12 | **Low** | `get_settings()` is `lru_cache`d, making test overrides awkward | `app/core/config.py` | Allow cache clear / dependency-injected settings |
| OPS-13 | **Low** | No license file | repo root | Add a license |
| OPS-14 | **Low** | Admin bootstrap is a manual CLI step; seed doesn’t create an admin | `app/scripts/*` | Document/automate first-admin creation per environment |

---

## 6. Testing

| ID | Severity | Gap / risk | Evidence | Recommended fix |
|---|---|---|---|---|
| TEST-1 | **High** | Tests build the schema with `Base.metadata.create_all`, **not migrations** → migration drift is invisible to CI | `tests/conftest.py:29` | Run `alembic upgrade head` against the test DB and assert `alembic check` is clean |
| TEST-2 | **Medium** | Missing coverage for: auth expiry/tampered JWT, CSRF, rate limiting, pagination, inventory shrink below holds, inactive package equipment, idempotency, offers/settings endpoints, 500 error shape, reference collision, customer dedupe, timezone edge cases | `tests/` | Add targeted tests per finding |
| TEST-3 | **Medium** | No coverage measurement or threshold | `pytest.ini` | Add `pytest-cov` and a CI gate |
| TEST-4 | **Low** | No load/performance test for availability under many overlapping bookings | `tests/` | Add a seeded benchmark for `check_availability` |
| TEST-5 | **Medium** | No security-focused tests | `tests/` | Add auth/CSRF/throttle regression tests |
| — | ✅ | Strong point: real multi-threaded concurrency tests (last-unit and stock-cap) | `tests/test_booking_rules.py` | Keep and extend to approve-vs-create races |

---

## 7. Documentation

| ID | Severity | Gap / risk | Evidence | Recommended fix |
|---|---|---|---|---|
| DOC-1 | **Low** | Test docstring references `BUILD_PLAN.md`, which does not exist | `tests/test_booking_rules.py:1` | Remove the stale reference or add the file |
| DOC-2 | **Medium** | No API usage examples, architecture diagram, ADRs, env-var reference, deployment/runbook | `backend/README.md` | Add `docs/` (this file is a start) |
| DOC-3 | **Low** | No CONTRIBUTING/CHANGELOG | repo root | Add both |
| DOC-4 | **Low** | README “Not built yet” list is accurate but has no owners/priorities/dates | `backend/README.md:63` | Track as issues/milestones |

---

## 8. Performance & scalability

| ID | Severity | Gap / risk | Evidence | Recommended fix |
|---|---|---|---|---|
| PERF-1 | **Medium** | Sync endpoints + single uvicorn process; no worker/pool sizing guidance | `README.md` run command | Document production worker count; tune SQLAlchemy pool (`pool_size`, `max_overflow`, timeouts) |
| PERF-2 | **Low** | No caching for catalogue/settings (read-heavy, rarely changing) | `app/api/public.py` | Short-TTL cache or ETags |
| PERF-3 | **Medium** | Availability recomputed from scratch per request; O(overlapping bookings × days) | `app/services/availability.py` | SQL aggregation + optional materialised daily-usage table |
| PERF-4 | **Low** | No statement timeouts or slow-query logging | `app/core/db.py` | Set `statement_timeout`, log slow queries |

---

## Suggested phased roadmap

### Phase 0 — Production safety (≈1 week)
1. OPS-2 / SEC-1: environment switch + refuse default secret + secure cookies in prod.
2. OPS-3 / OPS-4 / OPS-5 / OPS-6: Dockerfile, pinned deps, CI (lint + tests + `alembic check`), ruff/mypy.
3. OPS-1 / OPS-11: absolute `.env` path; fix `.env.example`.
4. API-9 / API-11 / OPS-7: catch-all JSON errors, real readiness check, structured logging.

### Phase 1 — Security hardening (≈1 week)
1. SEC-2 CSRF, SEC-3 rate limiting/lockout, SEC-4 token revocation.
2. SEC-5 remove phone from query string; SEC-6 security headers/HTTPS.
3. SEC-10 request/array limits; SEC-11 admin management + roles.

### Phase 2 — Booking correctness & commerce (≈2 weeks)
1. BOOK-1 inventory shrink guard, BOOK-6 idempotency, BOOK-7 expired-approval guard.
2. BOOK-4 unique customer phone + upsert; BOOK-3 reference retry.
3. BOOK-10 discounts, BOOK-11 payments (incl. M-Pesa callbacks).
4. BOOK-2 inactive package equipment; BOOK-15 timezone consistency.

### Phase 3 — Scale & data (≈1–2 weeks)
1. API-1 pagination, API-2 eager loading, DB-1 FK indexes, DB-2 CHECK constraints.
2. BOOK-14 / PERF-3 SQL-based availability aggregation.
3. DB-6 audit log; OPS-8 scheduled jobs; OPS-9 backups/runbook.

### Phase 4 — Product completeness (ongoing)
1. API-6 admin CRUD (packages, offers, transport, settings), API-5 booking detail/history.
2. BOOK-8 customer cancel/reschedule, BOOK-9 auto-completion, BOOK-12 booking rules.
3. DOC-2/3 docs, API-10 OpenAPI polish, API-12 request IDs.

---

## Quick wins (≤ half a day each)

- Add `DB-1` FK indexes in one migration.
- `BOOK-1`: validate inventory decreases against current holds.
- `SEC-5`: switch booking lookup to `POST` (or header) to stop PII in URLs.
- `BOOK-15`: single `today_in_tz()` helper.
- `API-11`: readiness check with `SELECT 1`.
- `API-9`: catch-all JSON error handler.
- `TEST-1`: run `alembic upgrade head` + `alembic check` in CI.
- `OPS-11`: update `.env.example` (port `5434`, `TEST_DATABASE_URL`).
- `DOC-1`: drop the `BUILD_PLAN.md` reference.
- `BOOK-2`: reject packages containing inactive equipment.

---

## Appendix A — Endpoint inventory

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/health` | — | Static `ok`, no DB check |
| GET | `/api/v1/equipment` | — | No pagination/search |
| GET | `/api/v1/equipment/{slug}` | — | |
| GET | `/api/v1/equipment/{id}/availability` | — | Max 120-day range |
| GET | `/api/v1/packages` | — | No pagination |
| GET | `/api/v1/offers` | — | Uses server-local date |
| GET | `/api/v1/transport` | — | No pagination |
| GET | `/api/v1/settings/public` | — | Whitelisted keys |
| POST | `/api/v1/bookings` | — | No idempotency |
| GET | `/api/v1/bookings/{reference}?phone=` | — | PII in query string |
| POST | `/api/v1/admin/auth/login` | — | No throttling |
| POST | `/api/v1/admin/auth/logout` | cookie | No server-side revocation |
| GET | `/api/v1/admin/me` | cookie | |
| GET | `/api/v1/admin/bookings` | cookie | No pagination, N+1 |
| POST | `/api/v1/admin/bookings/{id}/approve` | cookie | No CSRF; allows lapsed hold |
| POST | `/api/v1/admin/bookings/{id}/reject` | cookie | No CSRF |
| POST | `/api/v1/admin/bookings/{id}/cancel` | cookie | No CSRF |
| GET | `/api/v1/admin/equipment` | cookie | No pagination |
| POST | `/api/v1/admin/equipment` | cookie | No CSRF |
| PUT | `/api/v1/admin/equipment/{id}` | cookie | Partial-update semantics |
| PUT | `/api/v1/admin/inventory/{id}` | cookie | Can shrink below holds |

**Missing (from README roadmap):** package/offer/transport/settings admin CRUD, payments, notifications, uploads, admin booking detail/history.

## Appendix B — Verified database facts

- 24 indexes exist; **9 FK columns are unindexed** (listed in DB-1).
- `alembic current` → `10032a17e145 (head)`; `alembic check` → no new operations.
- `pytest` → **31 passed** (includes 2 real concurrency tests).
- Money/ids: UUID v4 PKs; `Numeric` money serialised as strings.

---

*Prepared from a full source review on 2026-11-25. Severity is a judgement based on impact × likelihood in a production deployment; adjust to your risk appetite.*
