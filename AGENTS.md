
# Project Instructions: Healthcare Research Intelligence Platform

## 1. Project Goal
Build a portfolio-quality AI research assistant that searches scientific
literature, retrieves supporting evidence, generates cited answers, compares
studies, and communicates uncertainty.

This is a research-support prototype, not a clinical diagnosis or treatment
system.

## 2. Working Style
- Work in small, verifiable milestones.
- Before implementation, explain the proposed approach and files to change.
- Do not implement future milestones without explicit approval.
- Prefer simple, modular, maintainable solutions over unnecessary complexity.
- Explain important design decisions in beginner-friendly language.
- Do not overwrite existing user work without permission.
- After each milestone, run relevant tests and report the exact results.
- If a requirement is unclear, ask before making major assumptions.

## 3. Technical Standards
- Use Python with type hints where practical.
- Follow PEP 8 and use clear, descriptive names.
- Separate ingestion, retrieval, generation, and evaluation logic.
- Keep configuration separate from application logic.
- Use pathlib for filesystem paths where practical.
- Add meaningful error handling and logging.
- Write automated tests for important functionality.
- Pin dependencies once the initial stack is selected.
- Avoid unnecessary abstractions and dependencies.

## 4. Security and Privacy
- Never hardcode API keys, passwords, or tokens.
- Load secrets from environment variables.
- Never commit .env files or credentials.
- Do not use private patient records or identifiable health data.
- Use public research material in accordance with applicable API rules,
  licences, copyright, and terms of service.
- Store article text only as allowed by docs/CONTENT_POLICY.md: full text of
  PMC Open Access articles under an allowed licence (default CC0 and CC BY),
  never PubMed abstracts, never retracted articles. Changing that policy
  needs the project owner's explicit approval.
- Do not claim clinical validation or medical reliability without evidence.

## 5. Evidence and AI Reliability
- Answers must be grounded in retrieved source passages.
- Citations must identify actual retrieved sources and must not be fabricated.
- Distinguish source findings from interpretation.
- Report conflicting findings and limitations when relevant.
- Abstain or explain insufficient evidence when support is inadequate.
- Evaluate retrieval and citation grounding rather than relying only on
  subjective demonstrations.

## 6. Architecture
- src/ingestion: fetch, parse, clean, and chunk permitted research content.
- src/retrieval: retrieve relevant evidence using search components.
- src/generation: generate evidence-grounded responses.
- src/evaluation: evaluate retrieval and answer quality.
- tests: automated tests.
- data/raw and data/processed: local data only; do not commit downloaded
  datasets unless their inclusion is permitted and necessary.
- docs: architecture, design decisions, and evaluation documentation.

## 7. Development Workflow
For each approved milestone:
1. State the goal and acceptance criteria.
2. Propose the smallest useful implementation.
3. Implement only the approved scope.
4. Run relevant tests and checks.
5. Summarise changed files, test results, limitations, and next steps.
6. Wait for approval before starting the next milestone.

## 8. Git
- Do not run destructive Git commands.
- Do not push, publish, or create commits without explicit permission.
- Keep this repository separate from the ai-learning repository.
