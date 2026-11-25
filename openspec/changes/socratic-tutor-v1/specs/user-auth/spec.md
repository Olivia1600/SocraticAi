# Spec Delta

## Purpose

Manages student and teacher registration, credential verification, session authentication, and role-based access control across classes.

## ADDED Requirements

### Requirement: User registration creates role-scoped accounts
The system SHALL allow a user to register with a unique name, password of at least 6 characters, role (`student` or `teacher`), and class name, storing only a cryptographic password hash.

#### Scenario: Successful student registration
- **WHEN** a visitor submits a unique name, valid password, `student` role, and class name
- **THEN** the system creates the user record with a hashed password, starts an authenticated session, and returns the sanitized user profile

#### Scenario: Duplicate account name rejected
- **WHEN** a visitor attempts to register with a name that already exists case-insensitively
- **THEN** the system rejects registration with a `409` conflict error and creates no duplicate account

### Requirement: Password login authenticates existing users
The system SHALL verify submitted credentials against the stored password hash and establish an HTTP-only session cookie on success.

#### Scenario: Valid login credentials
- **WHEN** a registered user submits their matching name and password
- **THEN** the system sets the session cookie and returns the authenticated user profile

#### Scenario: Invalid login credentials
- **WHEN** a user submits an unknown name or incorrect password
- **THEN** the system returns a `401` unauthorized error without revealing which field failed

### Requirement: Session inspection and logout manage active sessions
The system SHALL report current authentication state for the active session cookie and invalidate the session on logout.

#### Scenario: Active session check
- **WHEN** an authenticated client requests current session status
- **THEN** the system returns `authenticated: true` with the user's `id`, `name`, `role`, and `class_name`

#### Scenario: Logout clears session
- **WHEN** an authenticated user invokes logout
- **THEN** the system clears the session cookie and subsequent session checks report `authenticated: false`

### Requirement: Teacher endpoints enforce role authorization
The system SHALL restrict teacher analytics and assignment endpoints to authenticated users whose role is `teacher`.

#### Scenario: Student accesses teacher endpoint
- **WHEN** an authenticated `student` requests teacher class analytics
- **THEN** the system denies access with a `403` forbidden error
