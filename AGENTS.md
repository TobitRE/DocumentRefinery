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
- Command: `venv/bin/python -m coverage report -m`
- Overall coverage: 93%
- Lowest coverage areas include:
  - `document_refinery/authn/admin.py` (46%)
  - `document_refinery/dashboard/web_views.py` (85%)
  - `document_refinery/documents/views.py` (86%)
