# Spec Delta

## Purpose

Tracks per-student, per-concept Elo mastery ratings and generates Zone-of-Proximal-Development practice problems with hidden solution steps and misconception-aware feedback.

## ADDED Requirements

### Requirement: Per-concept Elo mastery tracks learner proficiency
The system SHALL maintain an Elo rating (initialized at `1000`), `attempts_count`, `hints_used_avg`, and derived `status` (`not_started`, `struggling`, `developing`, `mastered`) for each `(student_id, concept_id)` pair.

#### Scenario: Elo increases more for unassisted solutions
- **WHEN** a student solves a practice problem without hints versus after 2 or more Socratic hints
- **THEN** the unassisted solution increases `elo_score` by a larger delta (`+25` to `+40`) than the hinted solution (`+5` to `+10`)

#### Scenario: Incorrect attempt decreases Elo
- **WHEN** a student submits an incorrect practice answer or gives up
- **THEN** the system decreases `elo_score` (`-15` to `-25`, floored at `600`) and updates the derived concept status

### Requirement: Adaptive practice calibrates to Zone of Proximal Development
The system SHALL generate structured practice problems calibrated near `elo_score + 50` using textbook `worked_example` and `exercise` chunks, storing `hidden_solution_steps` and `common_misconceptions` server-side without exposing solution steps to the student.

#### Scenario: Practice question generated for student
- **WHEN** a student requests a practice question for a textbook or concept
- **THEN** the system returns `question_id`, `concept`, `difficulty_score`, and `question_text`, omitting `hidden_solution_steps` from the API response

### Requirement: Practice evaluation checks reasoning and logs misconceptions
The system SHALL evaluate submitted practice answers against `hidden_solution_steps` and textbook context, returning Socratic feedback and logging any detected `misconception_tags`.

#### Scenario: Misconception logged on flawed practice answer
- **WHEN** a student submits an answer matching a common misconception or missing core reasoning steps
- **THEN** the system logs the `misconception_tags`, updates the student's Elo record, and returns targeted guiding feedback
