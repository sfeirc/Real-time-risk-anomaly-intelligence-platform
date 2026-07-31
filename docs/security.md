# Security

## Dependency vulnerability scanning

Every dependency across every language is scanned on every push/PR
(`.github/workflows/ci.yml`'s `security-audit` job):

| Ecosystem | Tool | Services |
|---|---|---|
| Rust (`Cargo.lock`) | [`cargo-audit`](https://github.com/rustsec/rustsec) against the [RustSec Advisory DB](https://github.com/RustSec/advisory-db) | `ingestion`, `feature-service` |
| Python (`requirements.txt`) | [`pip-audit`](https://github.com/pypa/pip-audit) against the [OSV](https://osv.dev/) database | `api-gateway`, `ml-inference`, `data-generator` |
| JavaScript (`package-lock.json`) | `npm audit` | `dashboard` |

This is a recurring CI gate, not a one-time manual check — the point is
catching the *next* dependency bump that reintroduces a real CVE, not just
this one.

## What this scan actually found

`PyJWT==2.10.1` (`services/api-gateway`, securing the operator JWT flow —
see `docs/runbook.md`'s "Authentication" section) had several open CVEs at
the time of writing, fixed in 2.12.0-2.13.0. Assessed each one against how
this project actually uses the library before deciding what mattered:

| Advisory | What it is | Applies here? |
|---|---|---|
| PYSEC-2026-176, -177, -179, -175 | Issues in `PyJWKClient` (remote JWKS key fetching) and algorithm-confusion when mixing symmetric/asymmetric keys via a raw JWK | **No** — this project never fetches remote keys or accepts a JWK as the signing key; `require_operator` decodes with a single, fixed HS256 secret and `algorithms=["HS256"]` only (`services/api-gateway/app/auth.py`) |
| PYSEC-2026-178 | Detached-JWS (`"b64": false`, RFC 7797) payload handling | **No** — standard `jwt.encode`/`jwt.decode`, no detached payloads used |
| PYSEC-2026-120 | `crit` (RFC 7515 critical header) not validated, so an unknown extension is silently accepted instead of rejected | **Low** — tokens issued here never set `crit`; an attacker adding one to a forged token still can't produce a valid HMAC signature without the secret, which is the actual authorization boundary |
| PYSEC-2025-183 | "Weak encryption" — disputed by the maintainer; key length is the *application's* responsibility, not the library's | **N/A** — the deployed `API_GATEWAY_JWT_SECRET` is 48 bytes; PyJWT 2.13+ actively warns (`InsecureKeyLengthWarning`) below RFC 7518's 32-byte HS256 minimum, which caught two under-length *test fixture* secrets (not real ones) during the upgrade |

None of these had a practical exploit path against this specific HS256-
only, no-remote-keys usage — but upgraded to `2.13.0` anyway (security
hygiene doesn't wait for "am I sure this one's exploitable"), verified
against the live stack afterward (token issuance, protected-endpoint
access, and a real scenario injection all re-confirmed working end to end),
and the finding is what motivated turning this into a recurring CI job
instead of a one-off check.

Every other dependency set (both Rust services, the other two Python
services, the dashboard's npm tree) came back clean.

## What this doesn't cover (honest gaps, not oversights)

- **Container/OS-level scanning** (e.g. Trivy against the built images'
  base-layer packages) — this audit covers language-level dependencies
  only, not the Debian/Python base image's own OS packages.
- **SAST** (e.g. CodeQL, Semgrep) — no static analysis for
  injection/traversal/etc. patterns in this project's own code, only in
  its dependencies.
- **Secrets scanning** of git history (e.g. gitleaks/trufflehog) — nothing
  found by inspection, but never run as a tool.
- **Penetration testing** — no attempt to actually attack the running
  system (auth bypass attempts, fuzzing, etc.) beyond the unit/integration
  tests already in each service's `tests/`.

See `docs/roadmap.md` for how this fits alongside the project's other
scoped-out production-hardening work (mTLS, per-operator identity, etc.).
