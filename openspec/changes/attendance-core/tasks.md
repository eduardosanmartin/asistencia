# Tasks: attendance-core

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1360 total (5 slices) |
| 400-line budget risk | Low (each slice ≤380 lines) |
| Chained PRs recommended | Yes |
| Suggested split | PR 1: Foundation (~350) → PR 2: Shifts (~250) → PR 3: QR Flow (~380) → PR 4: Kiosk (~180) → PR 5: Hours (~200) |
| Delivery strategy | ask-always |
| Chain strategy | pending (user must choose) |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | App factory, config, models, auth, deploy scaffolding | PR 1 | Base branch; tests+docs included |
| 2 | ShiftTemplate + AssignedShift CRUD + SystemConfig | PR 2 | Depends on PR 1 models; tests included |
| 3 | Token service, attendance authority, terminal, marcar | PR 3 | Depends on PR 1; tests included |
| 4 | Kiosk toggle, kiosk sign-in views | PR 4 | Depends on PR 3 attendance service |
| 5 | Lateness rule, greedy pairing, hours computation | PR 5 | Depends on PR 3 AttendanceEvent schema |

## Phase 1: Foundation / Infrastructure (→ PR 1: ~350 lines)

- [x] 1.1 Create `app/config.py` with `Config` class (SECRET_KEY, JWT_SECRET, APP_BASE_URL, DB creds, env defaults) — spec: auth-shift; test: `tests/conftest.py` loads config
- [x] 1.2 Create `app/models.py` with 5 Peewee models (User with comma-separated roles + self-FK supervisor_id, ShiftTemplate, AssignedShift, AttendanceEvent with token_hash unique index, SystemConfig KV), `db_proxy`, `init_db()`, `make_*_db()` factories — spec: all 4; test: `tests/conftest.py` `sample_user_password` fixture
- [x] 1.3 Create `app/__init__.py` `create_app(testing)` factory: CSRFProtect, db_proxy init, blueprint registration, `_seed_superadmin()` from env, `register_routes(app)` — spec: auth-shift; test: `tests/conftest.py` `app` + `client` fixtures
- [x] 1.4 Create `app/services/security.py` — `hash_password()`, `verify_password()` (never raises), `generate_temp_password()` — spec: auth-shift Scenarios 1-3; test: `tests/test_auth.py` unit tests
- [x] 1.5 Create `app/blueprints/auth.py` — `login_required`, `role_required(*roles)`, `login`/`logout`/`change-password` routes, `must_change_password` gate, `session_transaction()` role fixtures (`auth_admin`, `auth_supervisor`, `auth_funcionario`, `auth_supervisor_func`) — spec: auth-shift all 4 reqs; test: `tests/test_auth.py` feature tests
- [x] 1.6 Create `app/routes.py` root redirect routes + `register_routes(app)` — spec: auth-shift; test: `tests/test_auth.py` redirect test
- [x] 1.7 Create `migrate.py` (psycopg2 schema runner, `migrations/001_initial.sql`, `schema_migrations` tracking) and `requirements.txt` (flask, peewee, psycopg2-binary, bcrypt, pyjwt, qrcode[pil], flask-wtf, pytest, pytest-cov, python-dotenv, gunicorn) — spec: design file table; test: `python -m pytest` collection
- [x] 1.8 Create deployment scaffolding: `Dockerfile` (python:3.12-slim, gunicorn), `docker-compose.yml` (postgres:16-alpine + web + caddy), `Caddyfile` (auto TLS), `.env.example` — spec: design file table; test: `docker-compose config` validates
- [x] 1.9 Create `tests/conftest.py` with app/client fixtures, `sample_user_password`, all 4 role fixtures via `session_transaction()` — spec: auth-shift; test: `python -m pytest tests/test_auth.py` passes

## Phase 2: Shift Templates & Assignment (→ PR 2: ~250 lines)

- [x] 2.1 Create `app/blueprints/admin.py` ShiftTemplate CRUD: create (unique name validation), list, update, delete (reject if active AssignedShift), `is_active` toggle, same-start-end rejection, overnight shift support — spec: shift-templates all 3 reqs; test: `tests/test_admin.py` template tests
- [x] 2.2 Create `app/blueprints/admin.py` AssignedShift CRUD: assign template to user for weekdays, multiple shifts same day supported, delete assignment (preserves past events), Supervisor team-scoped queries via `User.supervisor_id` self-FK — spec: turn-assignment all 3 reqs; test: `tests/test_admin.py` assignment tests
- [x] 2.3 Create `app/blueprints/admin.py` SystemConfig management: key/value CRUD for `kiosk_enabled`, `late_grace_minutes`, `qr_validity_seconds`, `timezone`; runtime updates; admin-only access — spec: traceability SystemConfig req; test: `tests/test_admin.py` config tests
- [x] 2.4 Add `tests/test_admin.py` covering: template CRUD + duplicate name + delete-with-assignments, assignment CRUD + multi-shift + supervisor scope, SystemConfig CRUD + update grace + toggle kiosk — spec: all 43 scenarios in shift-templates/turn-assignment/traceability

## Phase 3: QR Flow & Attendance Authority (→ PR 3: ~380 lines)

- [x] 3.1 Create `app/services/token.py` — `generate_qr_token(user_id, event_type, shift_id, window_secs)` (JWT HS256 with nbf/exp/jti), `verify_qr_token(token)` → payload or None — spec: traceability QR; test: `tests/test_attendance.py` token unit tests
- [x] 3.2 Create `app/services/attendance.py` — single validation authority: `validate_qr(jwt_token)`, `validate_kiosk(user_id, event_type)`, `get_last_event(user_id)` (determines expected event type), `resolve_shift(user_id)` (assigned vs extra), `compute_delay_minutes()`, `check_token_hash_reuse()` → `INVALIDO_reuso`, QR expiry → `INVALIDO_qr_expirado`, wrong person → `INVALIDO_persona_incorrecta`, out-of-window → `INVALIDO_fuera_de_horario`, INSERT AttendanceEvent for every attempt — spec: traceability all 7 reqs; test: `tests/test_attendance.py` integration tests
- [x] 3.3 Create `app/blueprints/terminal.py` — staff terminal view: worker search + event type selector, incoherent warning (Cancel/Continue), QR image generation via `qrcode[pil]` encoding JWT URL — spec: traceability QR scenarios 1-4; test: `tests/test_terminal.py` QR generation tests
- [x] 3.4 Create `app/blueprints/marcar.py` — `GET /marcar/<jwt>` landing (shows person + event + Confirm button), `POST /marcar/<jwt>` confirm (verify token, validate, write event, return OK/ERR) — spec: traceability QR scenarios 5-8; test: `tests/test_marcar.py` confirm/reuse/expiry/wrong-person tests
- [x] 3.5 Add `tests/test_terminal.py` for QR generation, event type selection, incoherent warning display — spec: traceability QR; test: `python -m pytest tests/test_terminal.py`
- [x] 3.6 Add `tests/test_marcar.py` for happy path, reuse (`INVALIDO_reuso`), expired (`INVALIDO_qr_expirado`), wrong person (`INVALIDO_persona_incorrecta`), out-of-window (`INVALIDO_fuera_de_horario`) — spec: traceability QR scenarios 5-8; test: `python -m pytest tests/test_marcar.py`

## Phase 4: Kiosk Flow (→ PR 4: ~180 lines)

- [x] 4.1 Extend `app/blueprints/terminal.py` with kiosk sign-in page: username/password form, event type selector, incoherent warning (same pre-generation check), credential-based auth via bcrypt — spec: traceability Kiosk req; test: `tests/test_terminal.py` kiosk login tests
- [x] 4.2 Extend `app/blueprints/admin.py` with `SystemConfig.kiosk_enabled` toggle: admin switches kiosk on/off, kiosk page checks toggle and returns `INVALIDO_kiosk_disabled` when off — spec: traceability Kiosk scenarios 1-3; test: `tests/test_terminal.py` kiosk-disabled test
- [x] 4.3 Add `tests/test_terminal.py` kiosk tests: happy sign-in (source=kiosk), disabled kiosk (`INVALIDO_kiosk_disabled`), incoherent warning at kiosk — spec: traceability Kiosk; test: `python -m pytest tests/test_terminal.py -k kiosk`

## Phase 5: Hours & Lateness Computation (→ PR 5: ~200 lines)

- [x] 5.1 Create `app/services/hours.py` — `compute_hours(user_id, start_date, end_date)`: chronological greedy pairing (entry→exit), unmatched entry = open, unmatched exit = orphan, two-consecutive-exits flag, `delay_minutes` per entry, daily/weekly/monthly subtotals — spec: traceability Hours + Lateness reqs; test: `tests/test_hours.py` unit tests
- [x] 5.2 Add `tests/test_hours.py` covering: greedy pairing (entry-exit chains), on-time (delay=0), within grace (delay≤grace, no deduction), beyond grace (delay>grace, flagged), incoherent sequence flagging (two exits), extra shift detection (`OK_extra`, shift=NULL) — spec: traceability Hours/Lateness/Extra Shift; test: `python -m pytest tests/test_hours.py`

## Phase 6: Testing & Verification (→ Cross-slice)

- [x] 6.1 Run full `python -m pytest` — verify all 43 spec scenarios pass across `tests/test_auth.py`, `tests/test_admin.py`, `tests/test_terminal.py`, `tests/test_marcar.py`, `tests/test_attendance.py`, `tests/test_hours.py`
- [x] 6.2 Verify TDD strictness: all tests pass with `python -m pytest`, no skipped tests, conftest fixtures work for all 4 roles
- [x] 6.3 Final `docker-compose config` validation and `Dockerfile` build check

## Phase 7: Cleanup / Documentation (→ Cross-slice)

- [x] 7.1 Update inline comments in all service modules to document the single-authority pattern and `INVALIDO_*` outcome prefixes
- [x] 7.2 Remove any temporary debug code or placeholder stubs from `app/blueprints/` and `app/services/`
