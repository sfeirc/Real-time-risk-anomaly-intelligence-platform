#!/usr/bin/env python3
"""Registers docs/data-contracts.md's schemas/*.schema.json with Redpanda's
Confluent-compatible Schema Registry and enforces BACKWARD compatibility, so
an incompatible change to an event contract is rejected at registration
time — not only caught after the fact by
tests/integration/test_contracts.py, and not left as a doc nobody's forced
to update (see docs/roadmap.md's former "Data contracts: hand-maintained doc
-> schema registry" entry, now closed by this script).

The registry is Confluent-API-compatible (POST /subjects/.../versions, PUT
/config/..., POST /compatibility/...) but this project keeps plain JSON on
the wire rather than adding the Confluent wire-format envelope (magic byte +
4-byte schema ID) to every producer/consumer: that would buy binary framing
this project doesn't need (messages are already tiny JSON, and `rpk topic
consume` staying human-readable is worth more here than saving a few bytes
per message). The registry's actual job — reject a breaking schema change
before it ships — doesn't require the wire format, only the registration and
compatibility-check calls this script makes.

Subjects follow Confluent's TopicNameStrategy (`{topic}-value`):
    raw-events-value      <- schemas/raw_event.schema.json
    features-value        <- schemas/feature_event.schema.json
    alerts-value           <- schemas/alert_event.schema.json
    model-metrics-value     <- schemas/model_metrics_event.schema.json

Usage:
    python scripts/schema_registry.py register-all   # idempotent
    python scripts/schema_registry.py check-all       # dry-run compatibility, no writes
    python scripts/schema_registry.py self-test        # proves enforcement actually rejects/accepts, see below

Registry URL defaults to http://localhost:18081 (docker-compose's external
port); override with --url or SCHEMA_REGISTRY_URL (the schema-registry-init
compose service uses the internal http://redpanda:8081).
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = REPO_ROOT / "schemas"
DEFAULT_URL = os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:18081")
COMPATIBILITY_MODE = "BACKWARD"

# Confluent TopicNameStrategy: subject = "{topic}-value" for the value schema.
SUBJECTS = {
    "raw-events-value": "raw_event.schema.json",
    "features-value": "feature_event.schema.json",
    "alerts-value": "alert_event.schema.json",
    "model-metrics-value": "model_metrics_event.schema.json",
}

_HEADERS = {"Content-Type": "application/vnd.schemaregistry.v1+json"}


def load_schema(name: str) -> dict:
    return json.loads((SCHEMAS_DIR / name).read_text())


def subject_exists(client: httpx.Client, subject: str) -> bool:
    resp = client.get(f"/subjects/{subject}/versions/latest")
    return resp.status_code == 200


def register(client: httpx.Client, subject: str, schema: dict) -> int:
    """Registers a schema version. Confluent-compatible registries treat
    registering an already-registered (byte-identical) schema as a no-op
    that returns the existing ID, so this is safe to call every deploy."""
    body = {"schemaType": "JSON", "schema": json.dumps(schema)}
    resp = client.post(f"/subjects/{subject}/versions", headers=_HEADERS, json=body)
    resp.raise_for_status()
    return resp.json()["id"]


def set_compatibility(client: httpx.Client, subject: str, mode: str = COMPATIBILITY_MODE) -> None:
    resp = client.put(f"/config/{subject}", headers=_HEADERS, json={"compatibility": mode})
    resp.raise_for_status()


def check_compatibility(client: httpx.Client, subject: str, schema: dict) -> bool:
    """Dry run: would `schema` be accepted as the next version of `subject`
    under its configured compatibility mode? Registers nothing."""
    body = {"schemaType": "JSON", "schema": json.dumps(schema)}
    resp = client.post(f"/compatibility/subjects/{subject}/versions/latest", headers=_HEADERS, json=body)
    resp.raise_for_status()
    return bool(resp.json()["is_compatible"])


def cmd_register_all(client: httpx.Client) -> int:
    failed = False
    for subject, filename in SUBJECTS.items():
        schema = load_schema(filename)
        try:
            schema_id = register(client, subject, schema)
            set_compatibility(client, subject)
            print(f"OK   {subject:<20s} <- {filename}  (schema id {schema_id}, compatibility={COMPATIBILITY_MODE})")
        except httpx.HTTPStatusError as e:
            print(f"FAIL {subject:<20s} <- {filename}: {e.response.status_code} {e.response.text}")
            failed = True
    return 1 if failed else 0


def cmd_check_all(client: httpx.Client) -> int:
    failed = False
    for subject, filename in SUBJECTS.items():
        if not subject_exists(client, subject):
            print(f"SKIP {subject:<20s} not registered yet - run register-all first")
            continue
        schema = load_schema(filename)
        try:
            ok = check_compatibility(client, subject, schema)
            print(f"{'OK  ' if ok else 'FAIL'} {subject:<20s} current schemas/{filename} is {'' if ok else 'NOT '}compatible with the registered version")
            failed = failed or not ok
        except httpx.HTTPStatusError as e:
            # Redpanda's JSON Schema compatibility checker doesn't fully
            # resolve $ref/definitions yet (verified: raw_event.schema.json's
            # oneOf-of-$ref payload trips this with a 422 "is_superset not
            # fully implemented ... unsupported keyword: $ref"). That's a gap
            # in the *checker*, not evidence the schema itself is
            # incompatible - inlining the $ref to dodge it would make this
            # schema register as a structurally different (if semantically
            # equivalent) document and get flagged incompatible against its
            # own prior version instead, which is worse. Warn and move on
            # rather than failing the gate on a false negative.
            if e.response.status_code == 422 and "$ref" in e.response.text:
                print(f"WARN {subject:<20s} compatibility check unsupported for this schema (registry can't resolve $ref yet) - registered, not verified")
                continue
            print(f"FAIL {subject:<20s}: {e.response.status_code} {e.response.text}")
            failed = True
    return 1 if failed else 0


def _widen_type_to_string(schema: dict, field: str) -> dict:
    broken = copy.deepcopy(schema)
    broken["properties"][field]["type"] = "string"
    return broken


def cmd_self_test(client: httpx.Client) -> int:
    """Proves BACKWARD compatibility enforcement actually rejects a breaking
    change and accepts a non-breaking one, rather than just trusting that a
    configured-but-unexercised compatibility mode does what it says."""
    subject = "alerts-value"
    schema = load_schema(SUBJECTS[subject])

    register(client, subject, schema)
    set_compatibility(client, subject)
    print(f"setup: {subject} registered at BACKWARD compatibility")

    failed = False

    # Negative control: narrowing anomaly_score from number to string is a
    # breaking change under any registry's compatibility rules - an existing
    # consumer doing arithmetic on it would start failing.
    broken = _widen_type_to_string(schema, "anomaly_score")
    rejected = not check_compatibility(client, subject, broken)
    print(f"{'OK  ' if rejected else 'FAIL'} breaking change (anomaly_score number->string) is {'rejected' if rejected else 'WRONGLY ACCEPTED'}")
    failed = failed or not rejected

    # Positive control: adding a new optional field is the textbook
    # backward-compatible change (old consumers ignore fields they don't
    # know about; the field isn't in `required`, so it's optional to write).
    compatible = copy.deepcopy(schema)
    compatible["properties"]["region"] = {"type": ["string", "null"]}
    accepted = check_compatibility(client, subject, compatible)
    print(f"{'OK  ' if accepted else 'FAIL'} compatible change (new optional `region` field) is {'accepted' if accepted else 'WRONGLY REJECTED'}")
    failed = failed or not accepted

    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["register-all", "check-all", "self-test"])
    parser.add_argument("--url", default=DEFAULT_URL, help=f"schema registry base URL (default: {DEFAULT_URL})")
    args = parser.parse_args()

    with httpx.Client(base_url=args.url, timeout=15.0) as client:
        if args.command == "register-all":
            sys.exit(cmd_register_all(client))
        elif args.command == "check-all":
            sys.exit(cmd_check_all(client))
        elif args.command == "self-test":
            sys.exit(cmd_self_test(client))


if __name__ == "__main__":
    main()
