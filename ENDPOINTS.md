# Endpoints

Auth: `Authorization: Api-Key <token>`

## Documents
- `POST /v1/documents` — upload PDF (multipart `file`, optional `ingest`, `options_json`)
- `GET /v1/documents` — list documents (tenant-scoped)
- `GET /v1/documents/{id}` — document detail (tenant-scoped)

## Admin
- `GET /admin/` — Django admin
