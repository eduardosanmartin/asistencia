# Exploration: attendance-core

Greenfield attendance control web app (control de asistencia) for work shifts and turns. The user wants the SAME tech stack as the firmaDocs reference project. This exploration researches firmaDocs conventions as replication source, analyzes the attendance domain, compares approaches for the QR/kiosk/audit/shift design, and recommends a first slice.

## Current State

- `/Users/dirdti/ai-stack/dev-ia/projects/asistencia` is GREENFIELD: no application code. Only `openspec/config.yaml` (schema: spec-driven, stack Python 3.12 + Flask + Peewee + PostgreSQL + pytest, strict TDD) and `.atl/skill-registry.md` exist. `openspec/specs/` has FOUR EMPTY capability dirs from sdd-init: `auth-shift`, `shift-templates`, `turn-assignment`, `traceability`.
- No engineering decisions beyond the stack are locked. Requirements S1–S5 (QR flow, kiosk fallback, shifts/turns, hours calculation, 3 roles) are authoritative from the product question round.
- Reference project `firmaDocs` is a mature Flask app with exactly the patterns to replicate (evidence below).

## Stack and Conventions to Replicate (firmaDocs evidence)

### App factory — `app/__init__.py`
- `create_app(testing: bool = False) -> Flask`; `load_dotenv()` BEFORE importing `Config` (`app/__init__.py:8`).
- `app.config.from_object(Config)`; `DEV_MODE = os.getenv("FLASK_ENV") == "development"`.
- `CSRFProtect(app)` from flask-wtf (`app/__init__.py:138-140`).
- DB selection by mode: `make_test_db()` (SQLite `:memory:`), `make_dev_db()` (SQLite `dev.db`), `make_production_db(config)` (PostgreSQL) — `app/__init__.py:146-155`.
- `db_proxy.initialize(database)`; `init_db()` inside `app_context()`; super-admin seed via env (`_seed_superadmin`, `app/__init__.py:45-113`).
- Blueprints registered per feature module: `auth_bp`, `admin_bp` (url_prefix `/admin`), etc. (`app/__init__.py:167-178`).
- Root routes in separate `app/routes.py` via `register_routes(app)`.
- Background scheduler only when NOT testing and NOT dev (`app/__init__.py:189-193`).

### Config — `app/config.py`
- Class `Config` with class attributes read from env with safe defaults; `BASE_DIR` computed from `__file__`; path resolution helper `_resolve_upload_folder()`; `WTF_CSRF_TIME_LIMIT = None`.

### Models — `app/models.py`
- `db_proxy = DatabaseProxy()` + `class BaseModel(Model)` with `Meta.database = db_proxy`.
- `init_db()` calls `db_proxy.create_tables([...], safe=True)` then ad-hoc migration helpers (e.g., `_ensure_document_signed_hash_column`) that run idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` via raw SQL, tolerant of failure.
- `make_production_db(config)` / `make_test_db()` / `make_dev_db()` factories.
- **Roles**: `User.role` is a comma-separated `CharField` with `get_roles()`, `has_role()`, `has_any_role()`, `add_role()`, `remove_role()`, `set_roles()` (`app/models.py:59-102`). This maps EXACTLY to S5 (supervisor may ALSO have funcionario role).
- Unique per-instance hash pattern: `signature_hash = SHA256(signer_id + pdf_hash + timestamp + server_secret)` (`app/blueprints/signing.py:153-163`) — the exact precedent for per-signing-instance QR uniqueness.

### Auth — `app/blueprints/auth.py`
- Session-based: `session["user_id"|"tenant_id"|"email"|"name"|"role"]` in `_establish_session`.
- Decorators `login_required` and `role_required(*roles)` (composes `login_required` + `has_any_role`).
- bcrypt via `app/services/security.py` (`hash_password`/`verify_password` — `verify_password` never raises, generic login error).
- `must_change_password` gate; `/dev-login` only when `DEV_MODE`.
- `User.password_hash` nullable → credential-less users (firmaDocs signers). Attendance kiosk needs credentials for ALL workers, so this becomes mandatory for `funcionario` users.

### JWT tokens — `app/services/token.py`
- `generate_signing_token` / `verify_signing_token`: pyjwt HS256, payload with `exp`/`iat`, secret from `JWT_SECRET` falling back to `SECRET_KEY`. Direct precedent for the cryptographically signed QR payload (S1).

### QR generation — `qrcode[pil]` already in firmaDocs requirements
- `_generate_qr_image(url)` in `app/services/traceability.py:98-122` — PNG generation pattern.
- QR content precedent: QR encodes a URL (`{APP_BASE_URL}/verify/{hash}`), not raw data.

### Migration runner — `migrate.py`
- psycopg2, numbered SQL files `migrations/001..NNN_*.sql`, `schema_migrations` tracking table, autocommit, `_ensure_base_schema()` (calls `init_db()` for base tables), `ADD COLUMN IF NOT EXISTS`.
- docker-compose web service runs `sh -c "python migrate.py && exec gunicorn -w 1 -b 0.0.0.0:8000 'app:create_app()'"` (single worker so background jobs don't duplicate).

### Testing — `tests/`
- `conftest.py`: `app` fixture = `create_app(testing=True)` + `SECRET_KEY`/`WTF_CSRF_ENABLED=False`; `client` fixture; session-auth fixtures per role (`auth_admin`, `auth_creator`, `auth_signer`, `auth_creator_signer`) that plant `sess["user_id"...]` directly; `sample_user_password = "test1234"`.
- Feature test files per blueprint (`test_auth.py`, `test_admin.py`, ...) + `tests/test_services/`.
- Test command: `python -m pytest` (already in asistencia `openspec/config.yaml`: `tdd: true`, strict TDD).

### Deployment
- `Dockerfile`: `python:3.12-slim`, gunicorn `app:create_app()`, EXPOSE 8000. `docker-compose.yml`: postgres:16-alpine (healthcheck) + web + caddy; Caddyfile reverse proxy with automatic TLS; `.env`/`.env.example` (python-dotenv); `APP_BASE_URL` config for QR URLs.

### OpenSpec conventions (firmaDocs archives)
- `proposal.md` with Intent / Scope (In/Out) / Capabilities (New/Modified) / Approach / delivery strategy (chained PR table for >400 lines) / Affected Areas / Risks table / Rollback Plan / Dependencies / Success Criteria.
- Delta specs with `## ADDED Requirements`, RFC 2119 keywords, Given/When/Then scenarios (firmaDocs `2026-08-11-auth-model-local/specs/`).

## Domain Analysis

### Entities (recommended first-slice set)
- **User** (worker): name, username, email, password_hash (bcrypt, REQUIRED for kiosk), `role` (comma-separated: `admin` | `supervisor` | `supervisor,funcionario` | `funcionario`), `is_active`, `must_change_password`. Optional `supervisor_id` self-FK or area field for team management (S5 "Supervisor manages their team") — OPEN POINT.
- **ShiftTemplate** (reusable plantilla): name, start_time, end_time, weekday mask or single day, active flag. Admin CRUD.
- **AssignedShift** (horario asignado por usuario): user FK + template FK (or explicit times) + weekday/date applicability. A worker can have several (different turns, days).
- **AttendanceEvent** (audit log — unified): user FK (nullable — invalid attempts may not resolve a person), event_type (`entry`/`exit`), source (`qr`/`kiosk`), outcome string with prefix `OK` or `INVALIDO_<reason>`, timestamp, token_hash (unique), shift FK (nullable → extra), extra flag, lateness info (delay_minutes, late_rule_applied), IP/user-agent for kiosk.
- **SystemConfig** (key/value): `kiosk_enabled` (S2 toggle), `late_grace_minutes` (S4, default 5), `qr_validity_seconds` (S1, default 30–60), `timezone` (open point).
- **QrTokenIssue** (optional, ephemeral): issued token_hash/jti, user, event_type, shift expectancy, issued_at, expires_at, consumed_at. Alternative: skip table and encode everything in the JWT + detect reuse via AttendanceEvent.token_hash unique index.

### QR flow (S1)
1. Terminal (authenticated staff view, e.g., a `terminal` blueprint or admin module) selects a worker → service `issue_qr_token(user, event_type)`.
2. Expected event determined by last event: no last event or last=exit → `entry`; last=entry → `exit`.
3. JWT payload: `user_id`, `event_type`, `shift_id` (expected assigned shift at now, or null → extra), `nbf`, `exp` (now + `qr_validity_seconds`), `jti`/nonce. Signed HS256 with `JWT_SECRET`.
4. QR encodes URL `{APP_BASE_URL}/marcar/{jwt}` rendered with `qrcode[pil]`.
5. Phone scan → landing page shows person + expected event + "Confirmar" → POST `/marcar/{jwt}`.
6. Validation service (single authority): decode JWT → reject malformed/expired/not-yet-valid; check reuse (token_hash already in AttendanceEvent → `INVALIDO_reuso`); check person matches; check event_type matches expected; resolve shift: assigned shift active at now → bind it, else extra (`OK_extra` path); apply late rule for `entry` (S4); write AttendanceEvent with `OK` / `INVALIDO_<reason>`; return result page.
7. EVERY attempt (valid and invalid) writes an audit row — the unified AttendanceEvent table IS the audit log.

### Kiosk flow (S2)
1. Admin toggles `kiosk_enabled` in SystemConfig (runtime, no redeploy).
2. Terminal kiosk page (login form with username/password + button, or a "sign now" button after login).
3. Auth against User credentials (bcrypt) → same validation service with `source=kiosk` (no token checks) → AuditEvent `OK`/`INVALIDO_*`. Toggle off → kiosk page refuses with audit `INVALIDO_kiosk_disabled` (optional).

### Extra shifts (S3)
- Sign outside any assigned shift's window is NOT an error: validation resolves "no assigned shift active at now" → record event with `shift=NULL` + `is_extra=True`, outcome `OK_extra`. Entry+exit pairing for extra shifts is an hour-computation concern (S4/out of scope detail).

### Hours/lateness (S4)
- Rule: on `entry` with bound assigned shift, compare actual time vs shift.start: delay ≤ `late_grace_minutes` → no deduction (store `delay_minutes`); delay > grace → store `delay_minutes` for deduction. Rules live in SystemConfig. Hour totals + payroll export deferred (see scope notes).

## Approaches

1. **QR payload: JWT URL vs raw token vs opaque server-side ID**
   - JWT URL (`/marcar/<jwt>`): self-contained, cryptographically signed, reuses firmaDocs `token.py` pattern + pyjwt already in stack, no DB lookup to decode. Pros: proven, no extra table to read for capacity check, exp/nbf native. Cons: token length (~200+ chars) makes QR denser (still fine at version <10). Effort: Low.
   - Raw compact token: same payload without transport — cons: needs custom wire format + rendering; no browser flow. Effort: Med.
   - Opaque ID (server row reference): Pros: shortest QR. Cons: extra table + lookup per scan, more moving parts. Effort: Med.
   - **RECOMMENDED**: JWT URL. Direct firmaDocs precedent (`token.py`, QR-encodes-URL pattern).

2. **Single-use enforcement**
   - A) Unique `token_hash` on AttendanceEvent (all rows) — reuse detected by one indexed lookup; works because EVERY attempt is audited. Pros: single table, atomic via unique constraint, no cleanup job. Cons: token_hash must be captured on invalid rows too (it is — same audit write). Effort: Low.
   - B) Separate QrToken table with issued/consumed state. Pros: explicit state machine. Cons: extra table, compaction/expiry cleanup needed, second write path. Effort: Med.
   - **RECOMMENDED**: A with a partial unique index on non-null token_hash (Postgres) — single source of truth is the audit log.

3. **Audit log storage**
   - A) Unified `AttendanceEvent` table = audit log (outcome prefix + nullable user FK). Pros: every event in ONE ordered timeline, simplest FK graph, one service. Cons: invalid rows share table with valid ones (mitigated by outcome prefix + status filter). Effort: Low.
   - B) Separate `audit_log` + `attendance_events` tables. Pros: cleaner separation of concerns. Cons: dual writes, join for timeline, more tables for a greenfield first slice. Effort: Med.
   - **RECOMMENDED**: A. The S1 requirement describes a single log with prefixed entries — model it directly.

4. **Kiosk toggle + business rules storage**
   - A) `SystemConfig` key/value table (`kiosk_enabled`, `late_grace_minutes`, `qr_validity_seconds`, `timezone`). Pros: one mechanism for ALL admin-configurable settings (S2 + S4), runtime changes without redeploy, trivially testable. Effort: Low.
   - B) Dedicated singleton `SystemSettings` row. Pros: typed columns. Cons: every new setting is a migration; key/value scales better for rule growth. Effort: Low.
   - C) Env var only. Cons: violates "feature toggle controlled by administrator at runtime"; reject.
   - **RECOMMENDED**: A.

5. **Extra shifts vs assigned shifts**
   - A) `shift` FK nullable on AttendanceEvent + `is_extra` boolean set at validation. Pros: single table, explicit, no duplicate model; extra-ness is a validation-time decision. Effort: Low.
   - B) Separate `extra_shift` table. Cons: duplicates event data, complicates timeline. Effort: Med.
   - **RECOMMENDED**: A.

6. **Hours computation**
   - A) Compute on the fly (service `hours.py`): totals + lateness per user/period from AttendanceEvent rows; store only raw events + derived `delay_minutes`. Pros: no denormalization, single source of truth, fits "configurable business rules" (rules read from SystemConfig). Effort: Med.
   - B) Materialized totals per user/day updated on sign. Pros: fast reporting. Cons: cache invalidation, dual writes, risk of drift. Effort: Med-High.
   - **RECOMMENDED**: A for the first slice (payroll export deferred; revisit when perf demands).

## Recommendation

Replicate firmaDocs conventions verbatim where they fit (app factory, db proxy + make_*_db factories, session auth + `role_required`, comma-separated roles, bcrypt security service, pyjwt token service, `qrcode[pil]` URL-encoding, migrate.py runner, pytest conftest fixtures, Docker/Caddy/gunicorn shape). Then:

- Single unified `AttendanceEvent` table as the audit log (outcome `OK_*`/`INVALIDO_*` prefixes, nullable user FK, unique `token_hash` → single-use); `SystemConfig` key/value table for `kiosk_enabled`, `late_grace_minutes`, `qr_validity_seconds`, `timezone`; one `attendance.py` validation service as the single authority for QR + kiosk paths; JWT URL QRs via `token.py`-style service; nullable `shift` FK + `is_extra` for extra shifts; on-the-fly hour/lateness computation.
- First slice `attendance-core` delivery split into chained PRs (400-line guard): (1) foundation — app factory, config, models (User/roles/SystemConfig), auth, seed admin, tests; (2) shifts — ShiftTemplate + AssignedShift CRUD; (3) QR flow — token issue/validate service + audit + terminal view + phone landing; (4) kiosk toggle + kiosk sign; (5) lateness rule + hour computation. Each slice is independently verifiable with pytest.

## Affected Areas (targets for the proposal, all to be created)

- `app/__init__.py` — app factory (create_app, CSRF, db proxy init, blueprints)
- `app/config.py` — env config, JWT_SECRET, APP_BASE_URL, timezone
- `app/models.py` — User, ShiftTemplate, AssignedShift, AttendanceEvent, SystemConfig; db factories; init_db
- `app/services/security.py` — bcrypt (replicate pattern)
- `app/services/token.py` — QR JWT issue/verify
- `app/services/attendance.py` — validation authority, reuse detection, shift resolution, late rule, audit writes
- `app/services/hours.py` — lateness + totals computation (slice 5)
- `app/blueprints/auth.py` — session auth, login_required/role_required
- `app/blueprints/admin.py` — user/template/config/role management, kiosk toggle
- `app/blueprints/terminal.py` — QR display + kiosk sign (same terminal, two modes)
- `app/blueprints/signing.py` (or `marcar.py`) — phone landing/confirm for QR
- `migrate.py` + `migrations/001_*.sql` — schema runner per firmaDocs
- `tests/conftest.py` + role fixtures; feature test files per slice
- `requirements.txt` — flask, peewee, psycopg2-binary, gunicorn, python-dotenv, bcrypt, pyjwt, qrcode[pil], flask-wtf, pytest, pytest-cov (subset of firmaDocs; pypdf/reportlab/apscheduler/requests NOT needed for first slice)
- `Dockerfile`, `docker-compose.yml`, `Caddyfile`, `.env.example` — deployment shape; `openspec/specs/{auth-shift,shift-templates,turn-assignment,traceability}/` — delta specs to fill (existing empty capability dirs)

## Risks

- **QR scan UX / double-submit**: GET-mutation vs confirm-POST; a page refresh or double tap could double-attempt. Mitigation: QR → landing page → single POST confirm; token hash unique index makes the second attempt an audited `INVALIDO_reuso` (safe but confusing UX — the UI must detect "already used" and show the original result, not an error).
- **Clock skew on 30–60s expiry**: server-authoritative timestamps; store UTC; use `nbf`+`exp` with small skew tolerance; document that terminal and phone clocks are irrelevant (validation is server-side).
- **Terminal identity & expected-event derivation**: expected event = "last event + 1" can be wrong on out-of-order or voided events (e.g., worker enters twice). Needs an explicit rule: derive from latest entry/exit per user (document in spec); a second `entry` when last was `entry` should be rejected or become an extra — DECISION REQUIRED.
- **Supervisor team scope (S5)**: "manages their team" needs a concrete model (self-FK supervisor or area grouping). Undefined → proposal must decide minimal viable: supervisor FK on User, supervisor sees only own team's events.
- **Timezone**: shifts are local-time concepts; storage must be UTC with a configured timezone (SystemConfig). Skipping this yields wrong lateness at boundaries — flag for spec.
- **Expired/revoked tokens & replay at scale**: unique index on token_hash grows with audit log — acceptable at attendance volumes; revisit only if millions of rows/day.
- **Scope creep in slice 1**: payroll exports, overtime, lunch breaks, multi-tenant, notifications — explicitly OUT of first slice to keep PRs reviewable.
- **`sqlite3`/Postgres divergence**: partial unique indexes differ between SQLite and PostgreSQL — tests run on SQLite `:memory:`; keep reuse detection in application logic (check-then-insert) or use a plain unique index compatible with both, since the check is also the audit write.

## Open Points for the Proposal

1. Expected-event derivation rule (entry/exit alternation edge cases — see Risks).
2. Team model for Supervisor (User.supervisor FK vs area field vs group table).
3. Extra-shift pairing: are extra entry+exit events paired into a "turno adicional" record, or only counted as isolated events? (Hour totals design)
4. Timezone strategy (single org timezone in SystemConfig; per-user later).
5. Confirm single-tenant (no Tenant table) — requirements show no multi-org need.
6. Whether kiosk users need `must_change_password` on first login (firmaDocs pattern) — reuse.
7. Phone landing page: require confirm button vs auto-submit on load (affects UX + CSRF handling of a token-bearing URL).

## Ready for Proposal

Yes. All S1–S5 requirements are grounded in real firmaDocs patterns with concrete evidence, and the approach forks are resolved with recommendations. The proposal phase (sdd-propose) should lock in the Open Points above, then sdd-spec can write delta specs for the four existing empty capability dirs.