# Spec Delta

## Purpose

Ingests textbook PDFs into structured, concept-tagged chunks with dependency hierarchies and retrieves grounded passages with page and section citations.

## ADDED Requirements

### Requirement: Textbook PDF ingestion preserves structure and metadata
The system SHALL accept uploaded PDF files, extract text per page with heading and section detection, and store chunks tagged with `book_id`, `chapter`, `section`, `page_number`, `concept_id`, and `content_type` (`definition`, `theorem`, `worked_example`, `exercise`, or `narrative`).

#### Scenario: Valid textbook PDF uploaded
- **WHEN** an authenticated user uploads a readable `.pdf` textbook file
- **THEN** the system parses its pages into structured chunks, indexes them for retrieval, and returns the document summary with extracted concept count

#### Scenario: Non-PDF or unreadable file rejected
- **WHEN** a user uploads a non-PDF file or a PDF containing no extractable text
- **THEN** the system rejects the upload with a `400` validation error and removes any temporary file

### Requirement: Concept dependency graph links textbook topics
The system SHALL extract an ordered concept hierarchy from textbook headings and sections, linking prerequisite concepts via `parent_concept_id` so tutoring can step back when learners are stuck.

#### Scenario: Hierarchical concepts extracted on upload
- **WHEN** a textbook PDF containing chapters and subsections is ingested
- **THEN** the system persists concept nodes with chapter order and prerequisite parent links retrievable for that book

### Requirement: Targeted hybrid retrieval respects pedagogical intent
The system SHALL retrieve top matching chunks for a query using hybrid lexical/semantic scoring and filter by `content_type` so tutoring prefers definitions/theorems while practice generation references worked examples/exercises.

#### Scenario: Tutoring retrieval avoids leaking worked solutions
- **WHEN** the tutoring engine requests textbook context for a concept query
- **THEN** the retriever prioritizes `definition`, `theorem`, and `narrative` chunks and returns citations containing book title, section, and page number

#### Scenario: Document listing and viewing
- **WHEN** an authenticated user requests uploaded textbooks or a specific textbook's sections
- **THEN** the system returns the uploaded textbook metadata, concept tree, and source PDF URL for split-screen viewing
