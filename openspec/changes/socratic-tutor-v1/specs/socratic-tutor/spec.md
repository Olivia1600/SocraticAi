# Spec Delta

## Purpose

Provides a stateful multi-turn Socratic dialogue engine that diagnoses learner reasoning, plans scaffolding moves, steps back to prerequisites when stuck, and blocks direct answer leakage.

## ADDED Requirements

### Requirement: Socratic turn diagnoses learner state before responding
The system SHALL classify each learner message as `correct`, `partial`, `misconception`, `stuck`, or `off_topic` relative to the active concept and retrieved textbook passages, recording the turn and scaffolding level (`1` to `4`).

#### Scenario: Learner exhibits misconception
- **WHEN** a learner submits an explanation contradicting the retrieved textbook definition or theorem
- **THEN** the diagnosis classifies the turn as `misconception`, tags the misconception category, and records the tutoring turn

### Requirement: Pedagogical planner selects an appropriate Socratic move
The system SHALL choose a pedagogical move (`probe`, `hint`, `counter_example`, or `prerequisite_step_back`) based on the turn diagnosis, scaffolding level, and learner Elo mastery.

#### Scenario: Repeatedly stuck learner triggers prerequisite step-back
- **WHEN** a learner is diagnosed as `stuck` for 3 or more consecutive turns on a concept that has a `parent_concept_id`
- **THEN** the planner transitions focus to the prerequisite parent concept and asks a foundational guiding question

### Requirement: Response guard blocks direct answer leakage
The system SHALL evaluate every drafted tutor response before returning it and rewrite or reject any draft that directly reveals the final answer or complete worked solution to the learner's prompt.

#### Scenario: Learner explicitly asks for the direct answer
- **WHEN** a learner asks "just give me the answer" or submits a direct problem-solving prompt
- **THEN** the response guard ensures the returned reply contains a guiding Socratic question and cited textbook clue rather than the direct solution

### Requirement: Split-screen tutoring session persists conversation history
The system SHALL persist tutoring messages per `(student, textbook, concept)` session and expose conversation history so learners can resume across refreshes.

#### Scenario: Learner reloads tutoring workspace
- **WHEN** an authenticated student reopens the tutoring view for a textbook
- **THEN** the system loads prior session messages alongside the cited textbook passages and current scaffolding level
