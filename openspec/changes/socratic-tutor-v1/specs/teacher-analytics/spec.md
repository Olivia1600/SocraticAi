# Spec Delta

## Purpose

Aggregates student Elo mastery and tutoring session misconception logs into a teacher class-level dashboard with heatmaps, alerts, and targeted practice assignments.

## ADDED Requirements

### Requirement: Class mastery heatmap summarizes Student x Concept proficiency
The system SHALL provide teachers with a matrix of all students in their `class_name` across tracked textbook concepts, including each cell's `elo_score`, `status`, and color band (`struggling` < 900, `developing` 900-1150, `mastered` > 1150).

#### Scenario: Teacher views class mastery heatmap
- **WHEN** an authenticated teacher opens the Teacher Dashboard for their class
- **THEN** the system returns every student in that class alongside per-concept Elo scores, status bands, and class averages

### Requirement: Misconception clusters highlight class-wide gaps
The system SHALL aggregate `misconception_tags` across tutoring and practice sessions for students in the teacher's class, ranked by frequency and percentage of affected students.

#### Scenario: Top misconceptions displayed
- **WHEN** students in a class accumulate misconception tags during tutoring or practice
- **THEN** the teacher analytics endpoint returns ranked misconception clusters with concept title, tag, and affected student count

### Requirement: Intervention alerts flag struggling learners
The system SHALL generate intervention alerts for students whose `hints_used_avg` exceeds `2.5` or whose `elo_score` drops across two consecutive completed sessions on a concept.

#### Scenario: Student triggers intervention alert
- **WHEN** a student experiences consecutive Elo drops or excessive hint usage in the teacher's class
- **THEN** the teacher dashboard includes an intervention alert identifying the student, concept, and trigger reason

### Requirement: Teachers can push curated practice assignments
The system SHALL allow a teacher to select a weak concept and push a generated practice assignment to targeted students or the entire class, making assigned items visible in those students' workspaces.

#### Scenario: Teacher pushes assignment to struggling students
- **WHEN** a teacher submits a curated practice assignment for a concept and target student list
- **THEN** the system creates practice assignment records for each targeted student and lists them as pending practice in those students' accounts
