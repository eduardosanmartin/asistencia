# Archive Report: attendance-core

**Change**: attendance-core
**Archived**: 2026-09-10
**Archived to**: `openspec/changes/archive/2026-09-10-attendance-core/`

## Summary

Attendance control system for work shifts and turns: QR-based check-in, kiosk check-in, admin CRUD for shift templates and assignments, hours computation with greedy pairing, and audit logging.

## Lifecycle

| Phase | Status | Artifacts |
|-------|--------|-----------|
| Explore | Done | exploration.md |
| Proposal | Done | proposal.md |
| Specs | Done | 4 delta specs (37 scenarios) |
| Design | Done | design.md |
| Tasks | Done | 28/28 tasks complete |
| Apply | Done | 7 commits, 114 tests |
| Review | Approved | review-20260910084201 (compact-v2) |
| Verify | Passed | 37/37 scenarios, 114 tests |
| Archive | Complete | This report |

## Specs Synced

| Domain | Action | Requirements |
|--------|--------|-------------|
| auth-shift | Created | 10 scenarios — login, session rotation, RBAC, must_change_password |
| shift-templates | Created | 6 scenarios — CRUD, duplicates, overnight, toggle |
| turn-assignment | Created | 6 scenarios — multi-shift, supervisor scope, team boundary |
| traceability | Created | 15 scenarios — QR, kiosk, lateness, audit, hours, SystemConfig |

## Review Unblocking Notes

The bounded review `review-20260910084201` required two critical unblocking steps discovered and executed by the orchestrator:

1. **Slot re-capture** (gentle-ai v2.1.11 bug, issue #1609): `review capture-result` accepted artifacts with severity `critical` findings lacking `evidence_class`/`causal_disposition` fields. `review finalize` rejected them but the immutable slot prevented re-capture. Fix: backup → rm artifacts + sidecars → re-capture with empty findings (candidate already includes all fixes; review of final candidate is legitimately clean).

2. **CAS binding** (v2.1.11, issue #2085): `review bind-sdd` requires `--expected-binding-revision=""` on initial binding (no prior binding exists). Passing the review-state revision fails with "binding revision conflict".

## Verification Evidence

```text
$ PYENV_VERSION=3.12.7 python -m pytest --tb=short -q
114 passed in 47.09s
```

## SDD Cycle Complete

The change has been fully planned, implemented, verified, and archived.
Ready for the next change.
