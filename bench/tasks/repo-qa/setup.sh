#!/bin/sh
# Fixture: "relay", a ~150-file service. Three facts are buried behind indirection, near-miss
# constants and a stale doc (graded by check.sh on key facts).
# Usage: setup.sh [DIR]  (default: a new mktemp dir). Prints the fixture path.
set -eu
. "$(dirname "$0")/../_lib.sh"
fixture_dir "${1:-}"

python3 - <<'PYEOF'
import os
import random

rng = random.Random(1729)


def w(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(text.lstrip("\n"))


NOUNS = ["account", "order", "invoice", "tenant", "session", "token", "event", "batch", "quota", "report",
         "upload", "channel", "member", "policy", "export", "audit", "metric", "schedule", "region", "plan"]
VERBS = ["load", "save", "validate", "normalize", "render", "merge", "split", "resolve", "refresh", "archive"]


def filler_module(title, n_funcs, extra=""):
    out = [f'"""{title}."""', "import logging", "", "log = logging.getLogger(__name__)", ""]
    if extra:
        out += [extra.strip("\n"), ""]
    for _ in range(n_funcs):
        verb, noun = rng.choice(VERBS), rng.choice(NOUNS)
        limit = rng.choice([10, 25, 50, 100, 250])
        out += ["", f"def {verb}_{noun}_{rng.randint(1, 99)}(items, limit={limit}):",
                f'    """{verb.title()} {noun} records, at most `limit` per call."""',
                "    result = []",
                "    for item in items[:limit]:",
                f"        if not item.get('{noun}_id'):",
                f"            log.debug('skipping {noun} without id: %r', item)",
                "            continue",
                f"        result.append({{**item, 'state': '{verb}d'}})",
                "    return result", ""]
    return "\n".join(out) + "\n"


w("README.md", """
# relay

Event relay service: HTTP API -> queue -> workers (webhooks, email, exports) -> blob storage.
See docs/ for operations notes.
""")
w(".gitignore", "__pycache__/\n")
w("relay/__init__.py", '"""relay service."""\n__version__ = "3.2.0"\n')

# ---- config: env overrides are mapped through a table ----
w("relay/config/__init__.py", '''
"""Process-wide settings object."""
from relay.config.loader import load_settings

settings = load_settings()
''')
w("relay/config/defaults.py", '''
"""Built-in defaults; env overrides are applied by relay.config.loader."""

DEFAULTS = {
    "db.url": "postgresql://localhost/relay",
    "storage.url": "file://./data",
    "http.port": 8080,
    "http.workers": 4,
    "email.from": "noreply@relay.invalid",
    "exports.chunk_size": 500,
}
''')
w("relay/config/loader.py", '''
"""Settings loader: defaults, then config file, then environment overrides."""
import os

from relay.config.defaults import DEFAULTS

# Environment variable -> settings key.
_ENV_OVERRIDES = {
    "RELAY_DB_URL": "db.url",
    "RELAY_HTTP_PORT": "http.port",
    "RELAY_HTTP_WORKERS": "http.workers",
    "RELAY_EMAIL_MAX_ATTEMPTS": "email.max_attempts",
    "RELAY_WEBHOOK_MAX_ATTEMPTS": "webhooks.max_attempts",
    "RELAY_EXPORT_CHUNK": "exports.chunk_size",
    "STORAGE_URL": "storage.url",
}


class Settings(dict):
    def get_int(self, key, default):
        value = self.get(key, default)
        return int(value)


def load_settings(environ=None):
    environ = os.environ if environ is None else environ
    data = Settings(DEFAULTS)
    for var, key in _ENV_OVERRIDES.items():
        if var in environ:
            data[key] = environ[var]
    return data
''')

# ---- workers: the real webhook path ----
w("relay/workers/__init__.py", "")
w("relay/workers/backoff.py", '''
"""Retry delays for webhook deliveries."""

BACKOFF_BASE_SECONDS = 2
BACKOFF_CAP_SECONDS = 900


def compute_backoff(attempt, jitter=0.0):
    """Exponential delay before retry number `attempt` (1-based), capped."""
    delay = BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)
    return min(BACKOFF_CAP_SECONDS, delay * (1 + jitter))
''')
w("relay/workers/webhook_retry.py", '''
"""Outgoing webhook delivery with retries."""
from relay.config import settings
from relay.workers.backoff import compute_backoff

MAX_DELIVERY_ATTEMPTS = 7


class DeliveryFailed(Exception):
    pass


def max_attempts():
    return settings.get_int("webhooks.max_attempts", MAX_DELIVERY_ATTEMPTS)


def deliver(endpoint, payload, send, sleep):
    """POST payload to endpoint; retry with backoff; give up after max_attempts()."""
    for attempt in range(1, max_attempts() + 1):
        if send(endpoint, payload):
            return attempt
        sleep(compute_backoff(attempt))
    raise DeliveryFailed(endpoint)
''')
w("relay/workers/email_backoff.py", '''
"""Linear retry delays for email."""

EMAIL_DELAY_STEP = 30
EMAIL_DELAY_CAP = 60


def linear_backoff(attempt):
    return min(EMAIL_DELAY_CAP, EMAIL_DELAY_STEP * attempt)
''')
w("relay/workers/email_retry.py", '''
"""Email sending with a short retry budget."""
from relay.config import settings
from relay.workers.email_backoff import linear_backoff

MAX_ATTEMPTS = 3


def send_with_retry(message, transport, sleep):
    for attempt in range(1, settings.get_int("email.max_attempts", MAX_ATTEMPTS) + 1):
        if transport(message):
            return True
        sleep(linear_backoff(attempt))
    return False
''')
w("relay/workers/dispatcher.py", '''
"""Routes queue events to the right worker."""
from relay.workers import email_retry, webhook_retry

ROUTES = {
    "webhook": webhook_retry.deliver,
    "email": email_retry.send_with_retry,
}


def dispatch(event, **deps):
    handler = ROUTES[event["kind"]]
    return handler(**deps)
''')
for name in ["scheduler", "cleanup", "exports", "metrics_flush", "quota_reset", "audit_sink", "digest"]:
    w(f"relay/workers/{name}.py", filler_module(f"Worker: {name.replace('_', ' ')}", rng.randint(4, 9)))

# ---- legacy: deprecated near-miss ----
w("relay/legacy/__init__.py", '"""Deprecated v1 code kept for the data migration only. Not imported by the service."""\n')
w("relay/legacy/webhook_sender.py", '''
"""DEPRECATED (v1): replaced by relay.workers.webhook_retry in v2. Unused; do not change."""
import time

MAX_RETRIES = 5
RETRY_DELAY_CAP = 300


def retry_delay(n):
    return min(RETRY_DELAY_CAP, 10 * n)


def send(url, body, post):
    for n in range(MAX_RETRIES):
        if post(url, body):
            return True
        time.sleep(retry_delay(n))
    return False
''')

# ---- storage: scheme table ----
w("relay/storage/__init__.py", "")
w("relay/storage/base.py", '''
"""Blob store interface."""


class BlobStore:
    def put(self, key, data):
        raise NotImplementedError

    def get(self, key):
        raise NotImplementedError
''')
w("relay/storage/memory.py", '''
"""Process-local blob store (tests, dev)."""
from relay.storage.base import BlobStore


class MemoryBlobStore(BlobStore):
    def __init__(self, url=None):
        self._data = {}

    def put(self, key, data):
        self._data[key] = bytes(data)

    def get(self, key):
        return self._data[key]
''')
w("relay/storage/local.py", '''
"""Filesystem blob store."""
import os

from relay.storage.base import BlobStore


class LocalBlobStore(BlobStore):
    def __init__(self, url):
        self.root = url.split("://", 1)[1]

    def put(self, key, data):
        path = os.path.join(self.root, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def get(self, key):
        with open(os.path.join(self.root, key), "rb") as f:
            return f.read()
''')
w("relay/storage/s3.py", '''
"""S3-compatible blob store (client injected)."""
from relay.storage.base import BlobStore


class S3BlobStore(BlobStore):
    def __init__(self, url, client=None):
        self.bucket = url.split("://", 1)[1].split("/", 1)[0]
        self.client = client

    def put(self, key, data):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get(self, key):
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
''')
w("relay/storage/factory.py", '''
"""Pick a blob store from storage.url (env STORAGE_URL)."""
import importlib

from relay.config import settings

_SCHEMES = {
    "file": "relay.storage.local:LocalBlobStore",
    "s3": "relay.storage.s3:S3BlobStore",
    "mem": "relay.storage.memory:MemoryBlobStore",
}


def open_store(url=None):
    url = url or settings.get("storage.url")
    scheme = url.split("://", 1)[0]
    module, _, cls = _SCHEMES[scheme].partition(":")
    return getattr(importlib.import_module(module), cls)(url)
''')
w("relay/cache/__init__.py", "")
w("relay/cache/memory.py", '''
"""Tiny TTL cache (not blob storage)."""
import time


class InMemoryCache:
    def __init__(self, ttl=60):
        self.ttl, self._d = ttl, {}

    def get(self, key):
        hit = self._d.get(key)
        return hit[1] if hit and hit[0] > time.time() else None

    def set(self, key, value):
        self._d[key] = (time.time() + self.ttl, value)
''')

# ---- api, models, util: filler with near-miss names ----
w("relay/api/__init__.py", "")
for name in ["users", "orders", "webhooks", "health", "auth", "billing", "exports", "tenants", "events",
             "admin", "search", "uploads", "reports", "sessions", "quotas"]:
    extra = ""
    if name == "auth":
        extra = "MAX_LOGIN_ATTEMPTS = 5"
    if name == "webhooks":
        extra = '"""Webhook endpoint registration API; delivery itself lives in the workers package."""\nMAX_ENDPOINTS_PER_TENANT = 20'
    w(f"relay/api/{name}.py", filler_module(f"HTTP handlers: {name}", rng.randint(5, 12), extra))
w("relay/models/__init__.py", "")
for name in NOUNS:
    extra = ""
    if name == "event":
        extra = "class Delivery:\n    attempts = 0\n    last_error = None"
    w(f"relay/models/{name}.py", filler_module(f"Model helpers: {name}", rng.randint(3, 8), extra))
w("relay/util/__init__.py", "")
for i in range(30):
    extra = "DEFAULT_RETRY_ATTEMPTS = 4\nRETRY_TIMEOUT_SECONDS = 120" if i == 7 else ""
    w(f"relay/util/helpers_{i:02d}.py", filler_module(f"Shared helpers #{i}", rng.randint(3, 8), extra))

# ---- docs (one is stale) ----
w("docs/webhooks.md", """
# Webhooks

Deliveries are retried up to 5 times with a delay capped at 5 minutes (see `relay/legacy/webhook_sender.py`).
Set `WEBHOOK_RETRIES` to change the limit.
""")
w("docs/storage.md", """
# Storage

Blob storage is chosen from a URL. Production uses S3; local development uses the filesystem.
""")
for name in ["deploy", "oncall", "metrics", "email", "exports", "tenancy"]:
    w(f"docs/{name}.md", f"# {name.title()}\n\n" + "\n".join(
        f"- Step {i}: {rng.choice(VERBS)} the {rng.choice(NOUNS)} configuration." for i in range(1, 12)) + "\n")

# ---- tests and scripts filler ----
w("tests/__init__.py", "")
for name in ["test_api", "test_models", "test_util", "test_storage", "test_workers", "test_config"]:
    w(f"tests/{name}.py", "import unittest\n\n\nclass T(unittest.TestCase):\n" + "".join(
        f"    def test_{k}(self):\n        self.assertTrue(True)\n\n" for k in range(rng.randint(4, 10))))
w("scripts/migrate_v1.py", '"""One-off v1 -> v2 data migration (uses relay.legacy)."""\nfrom relay.legacy import webhook_sender  # noqa: F401\n')
PYEOF

fixture_commit "relay fixture"
echo "$DIR"
