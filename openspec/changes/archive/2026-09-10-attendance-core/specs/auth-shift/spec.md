# Specification: auth-shift

## Purpose

Session-based authentication with bcrypt password hashing, a 3-role system (Administrador, Supervisor, Funcionario), and `must_change_password` gate. Replicates firmaDocs conventions.

## Requirements

### Requirement: Session Login

The system MUST authenticate users via username and password using bcrypt verification. On success the system MUST establish a server-side session containing `user_id`, `name`, and `role`. The system MUST NOT reveal whether the username or password was incorrect on failure.

#### Scenario: Successful login

- GIVEN a user with valid credentials exists
- WHEN the user submits username and password
- THEN a session is created and the user is redirected to the home page

#### Scenario: Invalid credentials

- GIVEN a user exists with username "juan"
- WHEN an incorrect password is submitted
- THEN the system displays "Credenciales inválidas" and no session is created

#### Scenario: must_change_password gate

- GIVEN a user with `must_change_password=true` logs in successfully
- WHEN the system establishes the session
- THEN the user is redirected to `/cambiar-password` and cannot access other routes until the password is changed

### Requirement: Role System

The system MUST support three roles: `administrador`, `supervisor`, `funcionario`. A user's `role` field is a comma-separated string allowing multiple roles. A Supervisor MAY also hold the `funcionario` role (e.g. `supervisor,funcionario`).

#### Scenario: Admin has full access

- GIVEN a user with role `administrador`
- WHEN the user requests any admin-only resource
- THEN access is granted

#### Scenario: Supervisor with funcionario role signs attendance

- GIVEN a user with roles `supervisor,funcionario`
- WHEN the user signs their own attendance via QR or kiosk
- THEN the system records the event under that user's identity

#### Scenario: Funcionario cannot manage users

- GIVEN a user with role `funcionario`
- WHEN the user requests `/admin/usuarios`
- THEN the system returns 403 Forbidden

### Requirement: Role-Based Access Control

The system MUST provide a `role_required(*roles)` decorator that composes `login_required` with role verification. Routes decorated with `role_required("administrador")` MUST be inaccessible to non-admin users.

#### Scenario: Authorized role accesses protected route

- GIVEN a user with role `supervisor` is logged in
- WHEN the user accesses a route decorated with `role_required("supervisor", "administrador")`
- THEN access is granted

#### Scenario: Unauthorized role is denied

- GIVEN a user with role `funcionario` is logged in
- WHEN the user accesses a route decorated with `role_required("administrador")`
- THEN the system returns 403 Forbidden

### Requirement: Session Logout

The system MUST destroy the session on logout and redirect to `/login`. Protected routes MUST redirect to `/login` when no session exists.

#### Scenario: Logout destroys session

- GIVEN a user is logged in
- WHEN the user clicks "Cerrar sesión"
- THEN the session is destroyed and the user is redirected to `/login`

#### Scenario: Access without session redirects to login

- GIVEN no session exists
- WHEN a user accesses a protected route
- THEN the system redirects to `/login` with message "Debe iniciar sesión"
