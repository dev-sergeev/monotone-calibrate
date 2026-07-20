# Acceptance verifier runtime v1

This caller-owned environment is separate from the implementation project.
Its frozen `uv.lock`, including artifact SHA-256 values, is part of the
out-of-band normative release snapshot.

Prepare once (network may be required):

```console
UV_PROJECT_ENVIRONMENT=/ABSOLUTE/CALLER/monotone-verifier-v1 \
  uv --no-config sync --project docs/acceptance/verifier-runtime --frozen --no-dev --python 3.12
```

Run verification without dependency resolution or network:

```console
UV_PROJECT_ENVIRONMENT=/ABSOLUTE/CALLER/monotone-verifier-v1 \
  uv --no-config run --project docs/acceptance/verifier-runtime --frozen --offline --no-dev \
    python -I docs/acceptance/verify_acceptance_results.py ...
```

The verifier itself rejects a non-CPython-3.12 or non-isolated process and
checks the exact installed `jsonschema` and `cryptography` versions before it
loads any project schema.
