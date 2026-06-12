# AGENTS

## Project Notes
- This repository uses a local virtual environment at `venv/`.
- Coverage is installed in the venv.

## Tests
- Run full test suite:
  - `venv/bin/python document_refinery/manage.py test`

## Coverage
- Run coverage for full suite:
  - `venv/bin/python -m coverage run document_refinery/manage.py test`
  - `venv/bin/python -m coverage report -m`

## Last Coverage Run (local)
- Command: `venv/bin/python -m coverage run document_refinery/manage.py test` followed by `venv/bin/python -m coverage report -m`
- Date: 2026-06-12
- Tests: 240 passed
- Overall coverage: 92%
- Lowest coverage areas include:
  - `document_refinery/dashboard/runtime.py` (57%)
  - `document_refinery/documents/profiles.py` (77%)
  - `document_refinery/documents/formats.py` (81%)
  - `document_refinery/dashboard/web_views.py` (81%)
  - `document_refinery/documents/tasks.py` (82%)
