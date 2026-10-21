# AURA threat model

**Scope:** local, single-user AURA MVP running on `127.0.0.1` with SQLite and optional local Ollama. This model covers the current implementation, not a future cloud deployment.

## Assets and trust boundaries

| Asset | Where it lives | Security need |
|---|---|---|
| Decision questions, ratings, constraints, and notes | Local SQLite database | Keep private; preserve integrity and revision history |
| Uploaded source payload and extracted passages | Temporary SQLite ingestion queue, then SQLite chunks | Limit payload retention; prevent cross-decision exposure; allow removal |
| Evaluation and scenario results | SQLite snapshots | Preserve input hash and reproducibility |
| Session credentials | SQLite stores only token hashes; browser stores an HttpOnly cookie | Prevent casual unauthorized access and session reuse |
| Optional prompts and model output | Local Ollama request/response memory | Treat output as untrusted; avoid logging personal content |

The browser is an untrusted client. The local HTTP service, SQLite database, uploaded documents, and Ollama process are separate trust zones. Documents and model responses are data, never privileged instructions. The MVP assumes the operating-system account and local machine are trusted.

## Threats and controls

| Threat | Current control | Remaining exposure / verification |
|---|---|---|
| Cross-site request forgery or cross-origin writes | Same-origin checks for mutation requests; `SameSite=Strict` session cookie | Verify in browser and run negative Origin tests |
| Cross-site scripting through decision/source text | HTML escaping in workspace rendering; restrictive CSP; `nosniff` | Review every dynamic rendering path; exported brief also escapes inserted text |
| Path traversal to static files | Resolved path must remain under the static directory | Add path traversal regression tests |
| Oversized or malformed uploads | Request, file, page-count, extension, and PDF parsing limits; extraction runs in a background worker and queue payload is cleared after completion or failure | Add malformed PDF and decompression-bomb fixtures; confirm queue cleanup during operational review |
| Cross-decision evidence access | Retrieval and evidence application are scoped to the decision ID | Add adversarial isolation tests for all resource routes |
| Unverified or misleading document claims | Evidence remains unverified until user confirmation; applying creates a cited revision; AI candidate facts require an exact quote from a retrieved passage | Exact quotes do not prove that an AI claim interprets the source correctly; human review remains required |
| Prompt injection in documents or user text | No autonomous tools; source text is explicitly marked untrusted; model output remains a draft; deterministic scoring never consumes unreviewed proposals; fixture cases and a local evaluation runner are included | Run `python -m aura.evaluate_evidence` against each selected model and review semantic failures |
| Unauthorized local access | First-run password setup, PBKDF2 password hashing, expiring server-side sessions, HttpOnly/SameSite cookie | Does not encrypt the database or defend against another process running as the same OS user |
| Password guessing against the local sign-in | Five failed or pending sign-in attempts per client address in a five-minute window; successful sign-in clears the window | In-memory limiter resets when the process restarts; loopback-only deployment is assumed |
| Data loss or accidental deletion | Explicit user confirmation for decision/source removal; JSON export | No automated encrypted backup or recovery workflow |
| Model outage or invented numeric ranking | Manual workflow remains available; LLM does not calculate scores; requests are bounded by a per-session rate cap, one in-flight model call, output limits, and timeout | Local machine resources remain finite; pilot users should be told that AI is optional |
| Ingestion backlog or resource exhaustion | A durable queue tracks job status, clears payloads when complete, and accepts at most five queued or processing documents | Queue is local to the SQLite database and not a distributed worker system |
| Operational blind spots | Authenticated `/api/metrics` reports aggregate request counts/statuses, latency totals, evaluations, AI calls, ingestion outcomes, and simulation runs | Counters reset when the process restarts; this is not hosted observability or alerting |

## Security acceptance checks

The unit/workflow suite verifies local authentication and session behavior, rejects a cross-origin mutation, confirms aggregate metrics exclude decision text, and enforces the configured per-session AI request cap.

- Unauthenticated API requests return 401; setup can be completed only once.
- Short passwords are rejected; password hashes and session tokens are never stored in plaintext.
- A session expires after 12 hours and logout revokes it server-side.
- Cross-origin mutation requests are rejected.
- Source text is not written to request logs; extraction errors expose only a safe reason.
- Rejected or unverified evidence cannot be applied to an assessment.
- Decision exports and deletes remain explicit user actions.

## Deferred before any hosted pilot

Add TLS, managed identity, tenant authorization, encryption-at-rest key management, secure cookie policy, secret management, malware scanning, durable external telemetry and alerting, retention and backup-purge policy, independent security review, and a documented incident response path. The local password gate is a workspace privacy control, not a substitute for production identity or data encryption.
