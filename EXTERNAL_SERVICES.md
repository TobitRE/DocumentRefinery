# External Services Configuration

This project uses Redis (Celery broker), ClamAV (scan), and Nginx (download acceleration).
All services are configurable via environment variables in `.env`.

## Redis (Celery broker)

Set:
```
CELERY_BROKER_URL=redis://<host>:<port>/<db>
```

If Redis is remote, ensure:
- firewall allows TCP to the Redis port
- Redis is configured to bind to the appropriate interface
- AUTH (if used) is included in the URL

Example:
```
CELERY_BROKER_URL=redis://:password@redis.example.com:6379/0
```

## ClamAV (clamd)

Set:
```
CLAMAV_HOST=<host>
CLAMAV_PORT=3310
```

If clamd is remote, ensure:
- firewall allows port 3310
- clamd is configured to listen on the network interface

If clamd is socket-activated (default on Ubuntu), use the Unix socket instead:
```
CLAMAV_SOCKET=/run/clamav/clamd.ctl
```
When `CLAMAV_SOCKET` is set, the TCP host/port are ignored.

## Nginx (X-Accel-Redirect)

Set:
```
X_ACCEL_REDIRECT_ENABLED=true
X_ACCEL_REDIRECT_LOCATION=/protected
DATA_ROOT=/var/lib/docling_service
```

Nginx must map the internal location to the same `DATA_ROOT`:
```
location /protected/ {
    internal;
    alias /var/lib/docling_service/;
}
```

## Celery cancel signal and queue

Set:
```
CELERY_CANCEL_SIGNAL=SIGTERM
CELERY_DEFAULT_QUEUE=default
```

Use `CELERY_DEFAULT_QUEUE` when running workers with custom queues.

## Docling options

Defaults are resolved in this order:
1) API request `options_json`
2) API key `docling_options_json`
3) Tenant `docling_options_json`
4) `DOC_DEFAULT_OPTIONS` in settings

Example JSON:
```json
{
  "max_num_pages": 50,
  "max_file_size": 52428800,
  "exports": ["markdown", "text", "doctags"],
  "ocr": false,
  "ocr_languages": ["eng"]
}
```
