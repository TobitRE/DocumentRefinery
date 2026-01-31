import io
import os
import time

from django.core.management.base import BaseCommand

import requests


class Command(BaseCommand):
    help = "Run a lightweight upload load test against the API."

    def add_arguments(self, parser):
        parser.add_argument("--host", default="http://localhost:8000")
        parser.add_argument("--api-key", required=True)
        parser.add_argument("--count", type=int, default=50)
        parser.add_argument("--size-kb", type=int, default=128)
        parser.add_argument("--ingest", action="store_true")

    def handle(self, *args, **options):
        host = options["host"].rstrip("/")
        api_key = options["api_key"]
        count = options["count"]
        size_kb = options["size_kb"]
        ingest = options["ingest"]

        headers = {"Authorization": f"Api-Key {api_key}"}
        url = f"{host}/v1/documents/"

        payload = b"%PDF-1.4\n%loadtest\n" + b"x" * (size_kb * 1024)
        success = 0
        failures = 0
        started = time.time()

        self.stdout.write(
            f"Uploading {count} PDFs (~{size_kb} KB each) to {url} (ingest={ingest})"
        )

        for i in range(count):
            file_obj = io.BytesIO(payload)
            files = {"file": (f"loadtest_{i}.pdf", file_obj, "application/pdf")}
            data = {"ingest": "true"} if ingest else {}
            try:
                response = requests.post(url, headers=headers, files=files, data=data, timeout=30)
                if response.status_code == 201:
                    success += 1
                else:
                    failures += 1
                    self.stderr.write(
                        f"[{i+1}/{count}] {response.status_code}: {response.text[:200]}"
                    )
            except requests.RequestException as exc:
                failures += 1
                self.stderr.write(f"[{i+1}/{count}] error: {exc}")

        elapsed = time.time() - started
        self.stdout.write("")
        self.stdout.write(f"Done in {elapsed:.1f}s. Success: {success}, Failed: {failures}")
