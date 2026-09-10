# Design: attendance-core

## Technical Approach

Single Flask app (app factory, Peewee db-proxy, session auth + bcrypt) replicating firmaDocs conventions verbatim. Core domain: 5 models (User, ShiftTemplate, SharedShift, AttendanceEvent, SystemConfig), 4 blueprints (auth, admin, terminal, signing/marcar), 3 services (security, token, attendance + hours). QR via JWT-signed URL; kiosk via credential login; both funnel through one `attendance.py` validation authority. Unified `AttendanceEvent` table is the audit log.

## Architecture Overview

```
app/
├── __init__.py          # create_app factory, CSRF, db_proxy init, blueprint registration
├── config.py            # Config class (env vars, JWT_SECRET, APP_BASE_URL)
├── models.py            # User, ShiftTemplate, AssignedShift, AttendanceEvent, SystemConfig + db factories
├── routes.py            # Root routes (register_routes)
├── blueprints/
│   ├── auth.py          # login_required, role_required, must_change_password, login/logout/change-password
│   ├── admin.py         # User CRUD, ShiftTemplate CRUD, AssignedShift CRUD, SystemConfig (url_prefix /admin)
│   ├── terminal.py      # QR display (staff terminal) + kiosk page (same blueprint, two views)
│   └── marcar.py        # Phone landing GET /marcar/<jwt>, confirm POST /marcar/<jwt>
├── services/
│   ├── security.py      # hash_password, verify_password, generate_temp_password
│   ├── token.py         # generate_qr_token, verify_qr_token (pyjwt HS256)
│   ├── attendance.py    # SINGLE validation authority: validate_qr(), validate_kiosk()
│   └── hours.py         # compute_hours(user, period), greedy pairing, lateness
├── templates/           # Jinja2 per blueprint
├── static/
migrate.py               # psycopg2 schema runner + migrations/
tests/
├── conftest.py          # app, client, role fixtures (auth_admin, auth_supervisor, auth_funcionario, auth_supervisor_func)
├── test_auth.py         # Session login, role_required, must_change_password
├── test_admin.py        # User/ShiftTemplate/AssignedShift CRUD, SystemConfig
├── test_terminal.py     # QR generation, kiosk toggle
├── test_marcar.py       # QR confirm flow, reuse, expiry, wrong person
├── test_attendance.py   # validate_qr/validate_kiosk unit tests
├── test_hours.py        # Greedy pairing, lateness, incoherent flag
```

## Data Model

| Entity | Key Fields | Relationships |
|--------|-----------|---------------|
| **User** | id, username (unique), email, name, role (comma-sep CharField), password_hash (nullable→credential-less), must_change_password, is_active, supervisor_id (self-FK nullable) | supervisor_id → User.id; backref `team` |
| **ShiftTemplate** | id, name (unique), start_time, end_time, weekday_mask (7-char), is_active | Independent |
| **AssignedShift** | id, user FK, template FK, is_active | user→User, template→ShiftTemplate |
| **AttendanceEvent** | id, user FK (nullable), event_type (entry/exit), source (qr/kiosk), outcome (OK_*/INVALIDO_*), timestamp (UTC), token_hash (nullable, plain unique index), shift FK (nullable), is_extra, delay_minutes | user→User (nullable), shift→AssignedShift (nullable) |
| **SystemConfig** | key (PK), value (CharField) | Standalone KV |

Indexes: `idx_events_user_ts` on (user_id, timestamp), `idx_events_outcome` on (outcome), `idx_events_token_hash` on (token_hash) — plain unique, compatible SQLite+Postgres. `idx_users_supervisor` on (supervisor_id).

**Unified audit log rationale**: Single `AttendanceEvent` table where EVERY attempt (valid `OK_*`, invalid `INVALIDO_*`) writes one row. Nullable user FK allows logging unknown-person attempts. Outcome prefix separates valid from invalid without a status column. This eliminates dual-write, simplifies FK graph, and provides a single ordered timeline.

## Sequence Diagrams

### QR Flow (Terminal → Phone → Validation)

```
Terminal                attendance.py           token.py           AttendanceEvent DB
  │                          │                      │                    │
  ├─ select(user, type) ────►│                      │                    │
  │                          ├─ get_last_event() ──────────────────────►│
  │                          │◄─ last_event ────────────────────────────┤
  │◄─ warning? (if match) ──┤                      │                    │
  │  [Cancel] → ABORT        │                      │                    │
  │  [Continue] → proceed    │                      │                    │
  ├─ issue_qr(user,type) ──►│                      │                    │
  │                          ├─ generate_qr_token ─►│                    │
  │                          │◄─ jwt_token ─────────┤                    │
  │◄─ QR image (jwt URL) ───┤                      │                    │
  │                          │                      │                    │
Phone scans → GET /marcar/<jwt>                     │                    │
  │  Landing: event + "Confirmar"                   │                    │
  ├─ POST /marcar/<jwt> ────►│                      │                    │
  │                          ├─ verify_qr_token ───►│                    │
  │                          │◄─ payload ───────────┤                    │
  │                          ├─ check token_hash not exists ────────────►│
  │                          ├─ resolve shift (assigned? extra?)         │
  │                          ├─ compute delay_minutes                   │
  │                          ├─ INSERT AttendanceEvent ──────────────────►│
  │◄─ result page (OK/ERR) ─┤                      │                    │
```

### Kiosk Flow

```
Kiosk Page              attendance.py           AttendanceEvent DB
  │                          │                        │
  ├─ login(user, pass) ─────►│                        │
  ├─ select(type) ──────────►│                        │
  │                          ├─ get_last_event() ────►│
  │◄─ warning? ─────────────┤                        │
  │  [Continue] ────────────►│                        │
  │                          ├─ resolve shift          │
  │                          ├─ INSERT AttendanceEvent ►│
  │◄─ result ───────────────┤                        │
```

### QR Reuse / Double-Submit

```
Phone                   attendance.py           AttendanceEvent DB
  ├─ POST /marcar/<jwt> ──►│                        │
  │                         ├─ check token_hash ────►│
  │                         │  EXISTS → write INVALIDO_reuso ───────►│
  │◄─ "Ya registró su marca"┤                        │
  │  [2nd tap] POST again ──►│                        │
  │                         ├─ token_hash still exists              │
  │◄─ same result (UI detect)┤                     │
```

### Hours Summary Computation

```
hours.py                AttendanceEvent DB
  │                           │
  ├─ query user events range ─►│
  │◄─ chronological rows ─────┤
  ├─ greedy pairing:           │
  │   entry→exit, entry→exit   │
  │   unmatched entry = open   │
  │   unmatched exit = orphan  │
  │   two exits in row = FLAG  │
  ├─ sum delays per entry      │
  ├─ daily/weekly/monthly ────►result
```

## Architecture Decisions (ADRs)

| # | Decision | Option A | Option B | Tradeoff | Choice |
|---|----------|----------|----------|----------|--------|
| 1 | Audit log | Unified AttendanceEvent | Separate audit_log + events | Dual-write vs simplicity | **A** — single timeline, simpler FK graph |
| 2 | QR payload | JWT URL (`/marcar/<jwt>`) | Opaque server-side ID | Self-contained vs shortest QR | **JWT URL** — firmaDocs precedent, no DB lookup to decode |
| 3 | SystemConfig | Key/value table | Typed singleton row | Every new setting = migration vs runtime flexibility | **KV** — trivially extensible |
| 4 | Extra shifts | Nullable shift FK + is_extra bool | Separate extra_shift table | Duplicated data vs single table | **Nullable FK** — validation-time decision |
| 5 | Hours | On-the-fly computation | Materialized totals | No cache invalidation vs fast reads | **On-the-fly** — single source of truth, revisit when perf demands |
| 6 | Single-use | App check-then-insert + plain unique index | Partial unique index (Postgres-only) | SQLite/Postgres parity vs DB-native enforcement | **App-level** — plain unique index works on both |

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `app/__init__.py` | Create | App factory: create_app(testing), CSRFProtect, db_proxy init, blueprint registration, seed admin |
| `app/config.py` | Create | Config class: SECRET_KEY, JWT_SECRET, APP_BASE_URL, DB creds |
| `app/models.py` | Create | 5 models + db_proxy + init_db() + make_*_db factories + idempotent ALTER migrations |
| `app/routes.py` | Create | Root redirect routes |
| `app/services/security.py` | Create | hash_password, verify_password, generate_temp_password |
| `app/services/token.py` | Create | generate_qr_token(user_id, event_type, shift_id, window), verify_qr_token(jwt) |
| `app/services/attendance.py` | Create | validate_qr(jwt), validate_kiosk(user_id, event_type), get_last_event(), resolve_shift() |
| `app/services/hours.py` | Create | compute_hours(user_id, start, end), greedy pairing, lateness totals |
| `app/blueprints/auth.py` | Create | login_required, role_required, login/logout/change-password routes |
| `app/blueprints/admin.py` | Create | User CRUD, ShiftTemplate CRUD, AssignedShift CRUD, SystemConfig management |
| `app/blueprints/terminal.py` | Create | Staff QR display page, kiosk login+sign page |
| `app/blueprints/marcar.py` | Create | GET /marcar/<jwt> (landing), POST /marcar/<jwt> (confirm) |
| `migrate.py` | Create | psycopg2 schema runner, numbered SQL files, schema_migrations tracking |
| `tests/conftest.py` | Create | app, client, sample_user_password, auth_admin, auth_supervisor, auth_funcionario, auth_supervisor_func fixtures |
| `tests/test_auth.py` | Create | Session login, role_required, must_change_password, logout |
| `tests/test_admin.py` | Create | User/ShiftTemplate/AssignedShift CRUD, SystemConfig |
| `tests/test_terminal.py` | Create | QR generation, kiosk toggle |
| `tests/test_marcar.py` | Create | QR confirm, reuse, expiry, wrong person |
| `tests/test_attendance.py` | Create | validate_qr/validate_kiosk unit tests |
| `tests/test_hours.py` | Create | Greedy pairing, lateness, incoherent flag |
| `requirements.txt` | Create | flask, peewee, psycopg2-binary, gunicorn, python-dotenv, bcrypt, pyjwt, qrcode[pil], flask-wtf, pytest, pytest-cov |
| `Dockerfile` | Create | python:3.12-slim, gunicorn |
| `docker-compose.yml` | Create | postgres:16-alpine + web + caddy |
| `Caddyfile` | Create | Reverse proxy with auto TLS |
| `.env.example` | Create | All env vars documented |

## Interfaces / Contracts

```python
# services/attendance.py — single validation authority
def validate_qr(jwt_token: str) -> dict:
    """Returns {"ok": True, "event": AttendanceEvent} or {"ok": False, "error": str, "event": AttendanceEvent}"""

def validate_kiosk(user_id: int, event_type: str) -> dict:
    """Same return shape. Source='kiosk'."""

# services/token.py
def generate_qr_token(user_id: int, event_type: str, shift_id: int|None, window_secs: int) -> str:
    """Returns signed JWT string."""

def verify_qr_token(token: str) -> dict|None:
    """Returns payload dict or None if invalid/expired."""

# services/hours.py
def compute_hours(user_id: int, start_date, end_date) -> dict:
    """Returns {"days": [...], "weekly_total": float, "monthly_total": float, "flags": [...]}"""
```

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | token.py (issue/verify), security.py (hash/verify), hours.py (pairing, lateness) | Direct function calls, no HTTP |
| Integration | attendance.py validate_qr/validate_kiosk with DB (reuse, shift resolution, audit write) | pytest fixtures with SQLite :memory: |
| Feature | Blueprint routes: login, QR flow end-to-end, kiosk flow, admin CRUD, marcar confirm | Flask test_client, session fixtures per role |
| E2E | Full QR scan → confirm → audit row, kiosk login → sign → audit row | Feature tests (Flask test_client simulates HTTP) |

**Fixtures**: `auth_admin` (role=administrador), `auth_supervisor` (role=supervisor), `auth_funcionario` (role=funcionario), `auth_supervisor_func` (role=supervisor,funcionario). All plant session via `session_transaction()` per firmaDocs pattern.

## Migration / Rollout

No migration required for initial greenfield. Schema created via `init_db()` (Peewee `create_tables(safe=True)`) on first boot. `migrate.py` available for future PostgreSQL ALTER migrations. Seed admin via env vars (`SUPERADMIN_EMAIL`, `SUPERADMIN_PASSWORD`).

## Contradictions Found

None. All four delta specs align with the locked approach. The traceability spec's "out-of-window" scenario (`INVALIDO_fuera_de_horario`) maps to the attendance service's shift window check — JWT contains shift_id but validation happens server-side against current time vs assigned shift window, which is consistent.

## Open Questions

- [ ] None — all decisions locked in proposal, exploration grounded in firmaDocs evidence.
