# Specification: traceability

## Purpose

Unified attendance event recording via QR and kiosk, audit log with OK/INVALIDO prefixes, extra-shift detection, lateness, hours summary, SystemConfig.

## Requirements

### Requirement: QR Attendance Flow

Terminal selects worker + event type. If type matches last event, Cancel/Continue warning shown (does NOT block). JWT-signed URL generated bound to person+event+shift+window, expiring within `qr_validity_seconds`. Phone landing shows event with confirm button.

| Scenario | Given | When | Then |
|----------|-------|------|------|
| Happy entry | Last event was Salida | Terminal selects Ingreso | JWT URL, phone shows confirm |
| Incoherent | Last event was Ingreso | Terminal selects Ingreso | Warning; QR not yet generated |
| Cancel | Warning shown | Click Cancel | Flow aborted |
| Continue | Warning shown | Click Continue | QR generated |
| Reuse | JWT already used | Re-submit | `INVALIDO_reuso` |
| Expired | JWT past validity | Submit | `INVALIDO_qr_expirado` |
| Wrong person | JWT bound to Carlos | Elena scans | `INVALIDO_persona_incorrecta` |
| Out-of-window | JWT in shift window | Submit outside window | `INVALIDO_fuera_de_horario` |

### Requirement: Kiosk Attendance Flow

Username/password + button. Same incoherent warning. Togglable via `SystemConfig.kiosk_enabled`.

| Scenario | Given | When | Then |
|----------|-------|------|------|
| Happy | kiosk_enabled=true | Carlos logs in, selects Ingreso | `OK`, source=kiosk |
| Disabled | kiosk_enabled=false | Worker accesses kiosk | `INVALIDO_kiosk_disabled` |
| Incoherent | Last was Ingreso | Select Ingreso at kiosk | Warning shown |

### Requirement: Extra Shift Detection

Sign outside assigned window → `shift=NULL`, `is_extra=True`, `OK_extra`. No config needed.

| Scenario | Given | When | Then |
|----------|-------|------|------|
| Outside shift | Shift 06:00–14:00 | Sign at 16:00 | `OK_extra`, shift=NULL |
| No assignments | No AssignedShift records | Sign | `OK_extra`, shift=NULL |

### Requirement: Lateness Rule

`delay_minutes <= late_grace_minutes` (default 5) → no deduction. `> grace` → stored for deduction.

| Scenario | Given | When | Then |
|----------|-------|------|------|
| On-time | Grace=5 | Enter 05:58 (shift 06:00) | delay=0 |
| Within grace | Grace=5 | Enter 06:04 | delay=4, no deduction |
| Beyond grace | Grace=5 | Enter 06:06 | delay=6, flagged |

### Requirement: Unified Audit Log

EVERY attempt recorded. Valid: `OK`/`OK_extra`. Invalid: `INVALIDO_*`. Source of truth. Incoherent sequences flagged in hours summary.

| Scenario | Given | When | Then |
|----------|-------|------|------|
| Valid | Entry via QR | Validated | Row `OK`, source=qr |
| Invalid | Expired QR | Processed | Row `INVALIDO_qr_expirado` |
| Incoherent | Two consecutive exits | Hours summary | Flagged for review |

### Requirement: Hours Summary

Chronological greedy pairing of entry/exit. Daily/weekly/monthly subtotals. Lateness per entry.

### Requirement: SystemConfig

Key/value: `kiosk_enabled`, `late_grace_minutes` (default 5), `qr_validity_seconds` (default 45), `timezone` (default "UTC"). Runtime updates by Admin.

| Scenario | Given | When | Then |
|----------|-------|------|------|
| Update grace | grace=5 | Admin sets 10 | Uses 10-min grace |
| Toggle kiosk | kiosk_enabled=true | Admin sets false | Kiosk refuses immediately |
