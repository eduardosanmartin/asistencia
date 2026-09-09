# Proposal: attendance-core

## Intent

Build the core attendance control system for a single-organization greenfield app. Workers sign entry/exit via QR scan (primary) or kiosk (fallback). The system tracks assigned shifts, computes lateness against configurable rules, and maintains a complete audit trail. Replicates firmaDocs conventions verbatim (Flask app factory, Peewee db-proxy, session auth, pytest).

## Scope

### In Scope
- App factory, config, DB proxy, Peewee models (User, ShiftTemplate, AssignedShift, AttendanceEvent, SystemConfig)
- Session auth with bcrypt, 3-role system (Administrador, Supervisor, Funcionario), `must_change_password` gate
- Shift template CRUD + per-user assignment
- QR flow: terminal checks worker's last event → if selected type matches last (incoherent), shows warning with Cancel/Continue → worker proceeds or cancels → JWT-signed ephemeral URL bound to chosen event type → phone landing shows chosen event + explicit confirm button → single-use enforcement via `token_hash` unique index
- Kiosk flow: admin-toggleable via SystemConfig, same pre-sign incoherence warning → worker selects entry/exit, sees warning if needed, then confirms with username/password
- Unified `AttendanceEvent` audit log (`OK`/`INVALIDO_*` outcome prefixes); incoherent sequences (two exits in a row, exit without prior entry) are still recorded and audited as valid (worker chose it), flagged in hours summary for supervisor review — the pre-generation warning is a mitigation, not a block
- Lateness rule (configurable grace minutes) + on-the-fly hour computation using chronological greedy pairing for entry/exit matching
- `SystemConfig` key/value store for kiosk toggle, grace, QR validity, timezone

### Out of Scope
- Automatic overtime pay calculation
- Lunch/meal break handling
- Multi-tenancy
- Notifications (email/SMS)
- Payroll export

## Capabilities

### New Capabilities
- `auth-shift`: Session auth, bcrypt, 3-role system, `must_change_password`, login, role decorators
- `shift-templates`: ShiftTemplate CRUD (Admin), reusable plantilla with name/start/end/weekday mask
- `turn-assignment`: AssignedShift (user + template + weekday), Supervisor team via `User.supervisor_id` self-FK
- `traceability`: AttendanceEvent audit log, QR token issue/validate, kiosk sign, lateness rule, hour computation, SystemConfig

### Modified Capabilities
None (greenfield — all capabilities are new)

## Approach

JWT URL QR (`/marcar/<jwt>`), explicit worker selection of entry/exit at terminal (QR) and kiosk — no automatic alternation inference. Before generating QR or confirming kiosk sign, the system checks the worker's last recorded event and shows a Cancel/Continue warning if the selected type matches the last event (pre-generation incoherence warning). Unified `AttendanceEvent` as audit log with `token_hash` unique index for single-use, `SystemConfig` key/value for all admin toggles/rules, nullable `shift` FK + `is_extra` for extras, on-the-fly hours service with chronological greedy pairing. Incoherent sequences are recorded and audited as valid events, flagged in the hours summary. Chained-PR delivery (5 slices, each under 400 lines).

## Decisions (Locked)

| # | Point | Decision | Rationale |
|---|-------|----------|-----------|
| 1 | Expected-event rule | Worker explicitly selects Ingreso/Salida at terminal (QR) or kiosk. No alternation inference. Pre-generation warning if selected type matches last event (Cancel/Continue). JWT payload binds chosen event type. Incoherent sequences recorded + audited, flagged in hours summary. | Worker intent is source of truth; pre-warning mitigates accidental incoherence; audit completeness preserved |
| 2 | Supervisor team | `User.supervisor_id` self-FK | Minimal viable, no extra tables |
| 3 | Extra-shift pairing | Chronological greedy pairing for hour computation | No assigned window; temporal is only option |
| 4 | Timezone | UTC storage; `SystemConfig.timezone` for org tz; shifts interpreted in org tz | Server-authoritative, no client clock dependency |
| 5 | Single-tenant | Confirmed — no Tenant table, one organization | Requirements show no multi-org need |
| 6 | `must_change_password` | Keep firmaDocs pattern (temp password → forced change on first login) | Proven, security best practice |
| 7 | QR landing UX | Explicit confirm button. Double-submit → `INVALIDO_reuso` + UI shows "already signed" (not error) | Prevents accidental double-sign, clear feedback |

## Review Workload Forecast

| Slice | Scope | Est. Lines | Budget Risk |
|-------|-------|-----------|-------------|
| PR 1: Foundation | App factory, config, models, auth, seed, tests | ~350 | Low |
| PR 2: Shifts | ShiftTemplate + AssignedShift CRUD + tests | ~250 | Low |
| PR 3: QR flow | Token service, attendance service, terminal, landing | ~380 | Low |
| PR 4: Kiosk | Toggle, kiosk page, auth path | ~180 | Low |
| PR 5: Hours | Lateness rule, hour computation, reports | ~200 | Low |

**Decision needed before apply**: No (each slice under 400)
**Chained PRs recommended**: Yes (5 slices, ~1360 total)
**400-line budget risk**: Low

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `app/__init__.py` | New | App factory, CSRF, db proxy, blueprints |
| `app/config.py` | New | Env config, JWT_SECRET, APP_BASE_URL, timezone |
| `app/models.py` | New | User, ShiftTemplate, AssignedShift, AttendanceEvent, SystemConfig |
| `app/services/security.py` | New | bcrypt hash/verify |
| `app/services/token.py` | New | QR JWT issue/verify |
| `app/services/attendance.py` | New | Validation authority, reuse detection, shift resolution |
| `app/services/hours.py` | New | Lateness + totals computation |
| `app/blueprints/auth.py` | New | Session login, role_required |
| `app/blueprints/admin.py` | New | User/template/config CRUD, kiosk toggle |
| `app/blueprints/terminal.py` | New | QR display + kiosk sign |
| `app/blueprints/signing.py` | New | Phone landing/confirm for QR |
| `migrate.py` + `migrations/` | New | Schema runner + SQL migrations |
| `tests/` | New | conftest, role fixtures, feature tests per slice |
| `requirements.txt` | New | Flask, Peewee, psycopg2, bcrypt, pyjwt, qrcode, etc. |
| Docker/Caddy | New | Dockerfile, docker-compose.yml, Caddyfile, .env.example |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Deliberate wrong event selection (worker picks exit when entering) | Med | Audit trail captures intent + timestamp; supervisor review catches abuse; out of scope to auto-detect |
| Incoherent sequences (two exits, exit without prior entry) | Med | Pre-generation warning (Cancel/Continue) at selection time; still recorded and audited; flagged in hours summary; chronological greedy pairing still works |
| QR double-submit UX confusion | Med | Confirm button + reuse detection + "already signed" UI |
| Clock skew on QR expiry | Low | Server-authoritative timestamps, nbf+exp, skew tolerance |
| SQLite/Postgres unique index divergence | Med | App-level check-then-insert; compatible plain unique index |
| Scope creep beyond 5 slices | Med | Explicit out-of-scope list; each slice independently verifiable |

## Rollback Plan

Greenfield: `git revert` the merged chain. No production data. Each PR independently revertable.

## Dependencies

- firmaDocs reference project (conventions source)
- pyjwt, qrcode[pil], bcrypt (Python packages)
- PostgreSQL 16 (production), SQLite (tests)

## Success Criteria

- [ ] `python -m pytest` passes with all role fixtures and feature tests
- [ ] QR: terminal selects Ingreso/Salida → JWT binds chosen type → scan → confirm → audit OK; reuse → `INVALIDO_reuso`
- [ ] Kiosk: toggle on/off via SystemConfig; worker selects entry/exit + confirms with credentials
- [ ] Incoherent sequence warning: worker selects same type as last event → warning shown with Cancel/Continue; Cancel aborts, Continue proceeds
- [ ] Incoherent sequence: two exits in a row → both recorded, hours summary flags the anomaly
- [ ] Shift assignment: on-time → no lateness; late > grace → delay_minutes recorded
- [ ] Extra shift: sign outside assigned window → `OK_extra` with null shift FK
- [ ] All invalid attempts logged with `INVALIDO_*` prefix in AttendanceEvent
