# AURA pilot readiness checklist

## Current release boundary

AURA is a local, single-user pilot workspace. It is not ready for public hosting or multi-user data. Keep the service bound to `127.0.0.1`; do not expose it to a network. The pilot must use fictional or participant-approved data and must explain that scores depend on user-entered assumptions.

## Build readiness

- [x] Background document queue records queued, processing, ready, and failed states.
- [x] Temporary upload payload is cleared after extraction succeeds or fails; queue accepts at most five pending or processing uploads.
- [x] Retrieval ranks decision-scoped passages using lexical relevance, query coverage, exact phrase matches, and term proximity.
- [x] AI fact suggestions require an exact quote from one retrieved passage and remain unverified until a person reviews them.
- [x] Ollama calls are limited to one in-flight request, 8 requests per session per minute by default, an 8,192-token context cap, and bounded response tokens. `AURA_AI_REQUESTS_PER_MINUTE` can lower or raise the request cap up to 60.
- [x] Local sign-in rejects further attempts after five tries per client address in five minutes; success clears the attempt window.
- [x] Robustness simulations accept explicit rating and importance ranges and persist the revision, ranges, seed, settings, and output for replay.
- [x] Authenticated `/api/metrics` returns aggregate process counters and latency totals without decision text or resource identifiers.
- [x] Prompt-injection fixtures and a runnable local evaluation command are included.
- [ ] Run the prompt-injection evaluation against the chosen Ollama model and review every failure. Command: `python -m aura.evaluate_evidence`.
- [ ] Run the complete unit and workflow suite after implementation freeze.
- [ ] Perform a keyboard-only and screen-reader pass on upload status, AI suggestions, and saved simulation replay.

## Internal security review

### Controls reviewed

- The service rejects non-loopback bind addresses, limits request and upload sizes, and does not enable cross-origin browser access.
- Mutating requests enforce same-origin checks when an Origin header is present; the session cookie is HttpOnly and SameSite Strict.
- Decision and evidence text is escaped in the browser; source search and evidence application are scoped to the selected decision.
- AI output is treated as a proposal. Evidence facts must cite an exact retrieved quote, and deterministic scoring never consumes an AI proposal automatically.
- Uploaded documents are processed asynchronously with page-count and file-size limits. The temporary queued payload is removed after processing.
- AI generation is local Ollama only, with an in-flight semaphore, per-session request limit, bounded context/output, and request timeout.
- Metrics omit decision identifiers and content and require the local workspace session.

### Findings and limits

- **Open for hosted use:** SQLite and local files are not encrypted at rest; local password authentication does not protect against another process under the same operating-system account.
- **Open for hosted use:** no malware scanner, managed identity, TLS, tenant model, managed secret store, backup restoration process, retention policy, or incident-response procedure.
- **Open for local hardening:** sign-in attempt limits are in-memory and reset when the process restarts. The workspace is assumed to run on a trusted personal device and remain bound to loopback.
- **Open for AI quality:** an exact quote proves that the text exists in a retrieved passage, not that the generated claim correctly interprets it. Human review is required; prompt-injection evaluation is model-specific and still needs to be run.
- **Open for file parser assurance:** malformed PDF, decompression-bomb, and adversarial cross-decision isolation regression cases still need execution in the release suite.

This is an internal review of the local MVP, not an independent penetration test or production security certification.

## Pilot launch steps

1. Freeze the build and run the unit, workflow, and prompt-injection evaluations. Save the outputs with the release notes.
2. Confirm the chosen Ollama model is installed and that the app still works when Ollama is unavailable.
3. Review the threat model and explain the local-only boundary, password limitation, data export, and deletion behavior to participants.
4. Use a fictional internship or learning-path decision. Do not ask participants to upload private documents.
5. Recruit five target users for the first moderated usability pass. Obtain informed agreement before recording notes; do not record source documents or decision text.
6. Ask each participant to create a comparison, find and verify a source passage, run a baseline, change one assumption, replay a saved simulation, and explain what the score does and does not mean.
7. Capture completion without help, elapsed time, hesitation points, source/citation understanding, interpretation of uncertainty, and usefulness/trust ratings from 1 to 5.
8. Fix blocking issues, then repeat with up to five additional pilot users if the first pass shows major confusion.
9. Publish only aggregate findings with the participant count. Never describe internal checks as user validation.

## Pilot feedback record

Use one record per participant with a random label such as `P01`. Do not include names, contact information, decision text, or document excerpts.

| Field | Record |
|---|---|
| Participant label and date |  |
| Completed without moderator help | Yes / No |
| Time to complete |  |
| Main hesitation or error |  |
| Could explain a weight, rating, constraint, and source behind a result | Yes / No; note confusion |
| Understood score and simulation caveats | Yes / No; note misunderstanding |
| Usefulness rating | 1–5 |
| Trust rating | 1–5 |
| Most useful change |  |
| Blocking issue |  |

Pilot completion remains an external activity. Do not mark the feedback exit gate complete until real sessions have taken place and actual observations are recorded.
