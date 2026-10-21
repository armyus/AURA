# AURA

AURA is a local-first decision workspace for students and early-career professionals. It turns a choice into options, criteria, constraints, evidence, and reproducible comparisons. Its scoring and scenario calculations are deterministic; optional local Ollama assistance proposes structure, source-linked candidate facts, clarification questions, and explanations for user review.

## Run locally

Requires Python 3.11 or newer. PDF text extraction uses `pypdf`; plain text and Markdown work without it.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m aura.server
```

Open <http://127.0.0.1:8000>. On first launch, create a workspace password of at least 12 characters. AURA binds only to localhost and stores data in `data/aura.sqlite3`; the password is PBKDF2-hashed, and sign-in sessions expire after 12 hours. This protects casual local access but does not encrypt the database or replace operating-system account security. Set `AURA_PORT` and `AURA_DB_PATH` to change the port or database path. Set `OLLAMA_URL` and `OLLAMA_MODEL` to enable optional local model proposals; the default model endpoint is `http://127.0.0.1:11434` and the default model is `qwen3.5:0.8b`.

### Enable the installed Ollama model (Windows PowerShell)

Start Ollama and confirm the model is installed (`ollama list`). Then, from the AURA folder, launch the workspace with the model configured:

```powershell
.\.venv\Scripts\Activate.ps1
$env:OLLAMA_MODEL = "qwen3.5:0.8b"
python -m aura.server
```

Keep that terminal open while using AURA. The workspace header's health endpoint reports whether a model is configured. Draft suggestions, source fact suggestions, and result explanations use the local model when available; otherwise AURA explains that it is using its deterministic/manual fallback. Model suggestions are always drafts for the user to review.

## Capabilities

- Create and revise decisions with alternatives, weighted criteria, ratings, and hard constraints.
- Run deterministic weighted multi-criteria evaluation with feasibility checks, score contributions, tie handling, and missing-input warnings.
- Upload text, Markdown, and text-based PDF evidence; extraction runs in a background worker with visible queued, processing, ready, and failed states. The temporary upload payload is removed from the queue after processing; extracted source-linked passages are stored in SQLite. Users can verify passages before applying values to a new decision revision.
- Optionally ask a configured local Ollama model to suggest candidate facts and clarification questions from retrieved passages. Candidate facts must include a quote that exactly matches a retrieved passage; suggestions remain unverified and never update ratings automatically. Prompt-injection evaluation fixtures are in `samples/prompt_injection_evaluation.json`.
- Bound optional local AI use to one in-flight generation and eight requests per session per minute by default. Set `AURA_AI_REQUESTS_PER_MINUTE` to a value from 1–60 to change the request cap. Ollama context and response lengths are capped; no hosted model billing is involved. Sign-in also allows at most five attempts per client address in five minutes.
- Expose authenticated aggregate operational counters and latency totals at `/api/metrics`; counters reset when the server restarts.
- Create scenarios as overrides of an immutable baseline evaluation; compare results, run one-variable weight sensitivity sweeps, and estimate ranking robustness across explicit rating/weight uncertainty ranges. Robustness runs save their revision, seed, settings, and result for replay.
- Generate a print-ready HTML decision brief with baseline results, criteria weights, caveats, saved scenarios, and linked-source inventory; use the browser print dialog to save it as PDF.
- Request optional LLM-generated decision structure. Suggestions remain drafts until the user reviews and saves them.
- Set a local workspace password on first launch; use expiring, revocable server-side sessions for API access.
- Include source status and citation references in evaluation output; SQLite schema versions are tracked with migrations.
- Run unit tests with `python -m unittest discover -s tests -v` and the synthetic evaluation with `python -m aura.evaluate`.
- Run the local-model prompt-injection evaluation with `python -m aura.evaluate_evidence` after configuring Ollama. Review its report; exact quote validation does not replace human review of semantic support.

## Architecture

- `aura.engine` is a pure deterministic module for validation, normalized weighted scoring, constraints, ties, missing values, scenario overrides, sensitivity sweeps, and a top-two criterion contribution (tornado-style) analysis. Its seeded Monte Carlo-style robustness analysis makes repeated uniform samples over user-supplied ranges, then reports lead shares and score percentiles. These are conditional stability summaries, not real-world outcome probabilities.
- `aura.repository` stores immutable revisions, evaluations, scenarios, replayable robustness runs, source metadata, and extracted passages in SQLite; schema migrations add local authentication, a durable temporary-payload ingestion queue, and simulation history.
- `aura.ingestion` extracts selectable PDF/text/Markdown content and preserves page and character offsets. `aura.retrieval` ranks decision-scoped passages with lexical relevance, phrase matches, query coverage, and term proximity.
- `aura.llm` is an optional, bounded Ollama adapter. It proposes structure, source-linked evidence candidates, clarification questions, and explanations; canonical inputs and scores remain user-confirmed and deterministic.
- `aura.server` provides the local HTTP API and serves the responsive browser app in `aura/static`.

The workspace began with the specification document and no application source tree, so this MVP uses a dependency-light Python service and static browser client rather than the specification's proposed TypeScript service layout. The decision contracts and trust boundaries remain the same.

## API outline

The local API includes decision creation and revision (`POST /api/decisions`, `POST /api/decisions/{id}/revisions`), baseline evaluation (`POST /api/decisions/{id}/evaluations`), background evidence upload and status/search/verification (`/documents`, `/evidence`), review-only AI evidence suggestions (`POST /api/decisions/{id}/evidence/assist`), scenario save and comparison (`/scenarios`, `/api/scenarios/{id}/evaluate`), one-variable sensitivity with top-two drivers (`/sensitivity`), seeded robustness sampling (`POST /api/decisions/{id}/robustness`) and saved simulation history (`GET /api/decisions/{id}/robustness-runs`), aggregate metrics (`GET /api/metrics`), browser-generated decision brief and JSON data exports, and deletion. The browser uses the API on the same origin; the service binds to loopback by default.

## Decision model

Each revision contains `options`, `criteria`, and `assessments`. Ordinal scores use a 1–5 scale. Numeric criteria require explicit user-defined `scale.min` and `scale.max`; optional target criteria use `scale.target` and `scale.tolerance`. Criterion `importance` values are normalized for scoring. Hard constraints use `constraint.operator` (`gte`, `lte`, or `eq`) and `constraint.threshold` and cannot be compensated for by a high aggregate score. Missing values remain missing and prevent an option from receiving a definitive rank.

Scenario overrides use `{"weights":{"criterion-id":4},"assessments":{"option-id":{"criterion-id":3}}}`. Re-evaluating a scenario never changes its base revision or baseline evaluation.

Robustness requests accept assessment ranges keyed by option and criterion and optional weight ranges, for example `{"assessmentRanges":{"option-id":{"criterion-id":[2,5]}},"weightRanges":{"criterion-id":[1,8]},"iterations":1000,"seed":42}`. Ranges must be explicit and remain inside the criterion's allowed domain. The seed, revision ID, input hash, sampling method, no-clear-winner share, option lead share, mean score, and 5th/95th score percentiles are returned so the run can be repeated and interpreted in context.

## Scope and limitations

This is a single-user local MVP. It does not include multi-user authentication, cloud deployment, OCR for scanned PDFs, autonomous external actions, or a hosted LLM. Retrieval uses local lexical matching; candidate passages are not facts until the user verifies them. Robustness sampling assumes independent uniform ranges because the MVP has no learned distributions; its lead shares describe only those supplied assumptions and are not objective probabilities of real-world success.

## Foundation and security notes

The current local threat model and residual risks are recorded in `docs/THREAT_MODEL.md`. The five-participant workspace review protocol is in `docs/WORKSPACE_USER_REVIEW_PLAN.md`; sessions have not been conducted, so no user validation is claimed. Keyboard support includes a skip link, visible focus styling, and arrow-key/Home/End navigation across workspace tabs. The target-user keyboard-only review remains part of pilot validation.

