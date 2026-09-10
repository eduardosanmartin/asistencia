# Specification: shift-templates

## Purpose

Reusable shift template definitions (plantillas) managed by Administrador. Templates define a shift pattern with name, start/end times, and weekday applicability.

## Requirements

### Requirement: ShiftTemplate CRUD

The system MUST allow Administrador users to create, read, update, and delete shift templates. Each template MUST have: `name` (unique string), `start_time` (HH:MM), `end_time` (HH:MM), `weekday_mask` (7-char bitmask or equivalent indicating applicable days), and `is_active` (boolean, default true).

#### Scenario: Create template

- GIVEN an Administrador is logged in
- WHEN the Admin submits a template with name="Turno Mañana", start="06:00", end="14:00", weekdays=Mon–Fri
- THEN the template is persisted and appears in the template list

#### Scenario: Create template with duplicate name

- GIVEN a template named "Turno Mañana" already exists
- WHEN the Admin submits another template with the same name
- THEN the system rejects with error "Nombre de plantilla duplicado"

#### Scenario: Delete template with active assignments

- GIVEN template "Turno Mañana" has active AssignedShift records
- WHEN the Admin deletes the template
- THEN the system rejects with error "No se puede eliminar: tiene asignaciones activas"

### Requirement: Template Time Validation

The system MUST reject templates where `start_time` equals `end_time` or where the time range is invalid (e.g. start 14:00 end 06:00 is allowed for overnight shifts, but start 06:00 end 06:00 is not).

#### Scenario: Same start and end time rejected

- GIVEN start_time="08:00" and end_time="08:00"
- WHEN the Admin submits the template
- THEN the system rejects with error "Horario inválido"

#### Scenario: Overnight shift is valid

- GIVEN start_time="22:00" and end_time="06:00"
- WHEN the Admin submits the template
- THEN the template is created successfully

### Requirement: Template Activation Toggle

The system MUST allow Administrador to toggle `is_active` on a template. Inactive templates MUST NOT appear in the assignment dropdown for new assignments but MUST NOT affect existing assignments.

#### Scenario: Deactivate template

- GIVEN an active template "Turno Noche"
- WHEN the Admin sets `is_active=false`
- THEN the template no longer appears in the new-assignment form
- AND existing assignments referencing it remain valid
