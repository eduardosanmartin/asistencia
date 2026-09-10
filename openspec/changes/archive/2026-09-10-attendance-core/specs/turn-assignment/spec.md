# Specification: turn-assignment

## Purpose

Per-user shift assignments linking workers to templates on specific weekdays. Supervisor team management via `User.supervisor_id` self-FK. Administrador manages all assignments; Supervisor manages team only.

## Requirements

### Requirement: AssignedShift Assignment

The system MUST allow Administrador to assign a shift template to a user for specific weekdays. Each AssignedShift MUST reference one User and one ShiftTemplate. A user MAY have multiple assignments for different templates or overlapping days (e.g. morning and afternoon on the same day).

#### Scenario: Assign shift to user

- GIVEN Administrador is logged in
- WHEN the Admin assigns "Turno Mañana" to user "Carlos" for Mon, Tue, Wed, Thu, Fri
- THEN an AssignedShift record is created linking Carlos to the template

#### Scenario: User with multiple shifts on same day

- GIVEN user "Carlos" has "Turno Mañana" (06:00–14:00) assigned for Mon–Fri
- WHEN the Admin assigns "Turno Tarde" (14:00–22:00) to Carlos for Mon–Fri
- THEN both assignments coexist and the system resolves the active shift at signing time

### Requirement: Supervisor Team Scope

The system MUST use `User.supervisor_id` (self-FK) to define team membership. A Supervisor MUST only see and manage attendance events and assignments for users where `user.supervisor_id == supervisor.id`.

#### Scenario: Supervisor sees own team

- GIVEN Supervisor "Ana" has `supervisor_id` pointing to itself (or is the team root)
- WHEN Supervisor "Ana" views team attendance
- THEN only events for users where `supervisor_id = Ana.id` are shown

#### Scenario: Supervisor cannot manage other teams

- GIVEN Supervisor "Ana" manages users Carlos and Luis
- WHEN Supervisor "Ana" attempts to view attendance for user "Elena" (supervisor_id = Bob)
- THEN the system returns 403 Forbidden

#### Scenario: Supervisor also funcionario signs attendance

- GIVEN a user with roles `supervisor,funcionario`
- WHEN the user signs their own attendance
- THEN the event is recorded under their identity with no team-scope restriction

### Requirement: Assignment Deletion

The system MUST allow Administrador to remove an AssignedShift. Deleting an assignment MUST NOT affect previously recorded attendance events.

#### Scenario: Remove assignment

- GIVEN user "Carlos" has "Turno Mañana" assigned for Mon–Fri
- WHEN the Admin deletes the assignment
- THEN the assignment is removed and Carlos has no shift for those weekdays
- AND Carlos's past attendance events remain unchanged
