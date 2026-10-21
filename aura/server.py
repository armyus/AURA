"""Local HTTP API and static web app for AURA."""

from __future__ import annotations

import json
import hashlib
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from collections import defaultdict, deque
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .engine import DecisionValidationError, apply_overrides, evaluate, robustness, sensitivity, validate_model
from .ingestion import IngestionError, MAX_UPLOAD_BYTES, SUPPORTED_EXTENSIONS, extract_document, sha256
from .llm import ModelUnavailable, configured as local_model_configured, explain_result, extract_evidence, propose_decision
from .repository import Repository
from .retrieval import search_chunks

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
MAX_BODY = 11 * 1024 * 1024
repo = Repository()
_ingestion_worker_lock = threading.Lock()
_ingestion_worker_started = False
_metrics_lock = threading.Lock()
_metrics = {"requestsTotal": 0, "requestStatusCounts": {}, "requestLatencyMs": {"count": 0, "sum": 0.0, "max": 0.0},
            "aiRequests": 0, "evaluations": 0, "scenarioEvaluations": 0, "robustnessRuns": 0,
            "documentsQueued": 0, "documentsReady": 0, "documentsFailed": 0}
_ai_requests_by_session: dict[str, deque[float]] = defaultdict(deque)
_ai_rate_lock = threading.Lock()
_login_attempts: dict[str, deque[float]] = defaultdict(deque)
_login_rate_lock = threading.Lock()


def _metric_increment(name: str, amount: int = 1) -> None:
    with _metrics_lock:
        _metrics[name] = _metrics.get(name, 0) + amount


def _metric_request(method: str, status: int) -> None:
    with _metrics_lock:
        _metrics["requestsTotal"] += 1
        key = f"{method} {status}"
        _metrics["requestStatusCounts"][key] = _metrics["requestStatusCounts"].get(key, 0) + 1


def _metric_latency(duration_ms: float) -> None:
    with _metrics_lock:
        values = _metrics["requestLatencyMs"]
        values["count"] += 1
        values["sum"] = round(values["sum"] + duration_ms, 3)
        values["max"] = round(max(values["max"], duration_ms), 3)


def _metrics_snapshot() -> dict[str, Any]:
    with _metrics_lock:
        return {**_metrics, "requestStatusCounts": dict(_metrics["requestStatusCounts"]),
                "requestLatencyMs": dict(_metrics["requestLatencyMs"]), "scope": "since process start; no decision text or identifiers"}


def _explain_result_locally(result: dict[str, Any]) -> str:
    """Explain the deterministic comparison without requiring an installed LLM."""
    options = result.get("options", [])
    ranked = sorted((o for o in options if isinstance(o, dict)),
                    key=lambda o: (o.get("rank") is None, o.get("rank") or 999, o.get("name", "")))
    if not ranked:
        return "AURA has no scored options to explain yet. Add options and complete their ratings, then compare again."
    leaders = [o for o in ranked if o.get("rank") == 1]
    if not leaders:
        return "AURA cannot identify a leading option from these inputs. Review any hard limits and missing ratings, then compare again."
    if len(leaders) > 1:
        names = ", ".join(str(o.get("name", "Option")) for o in leaders)
        opening = f"The comparison is tied between {names}. The current inputs do not distinguish a single top option."
    else:
        lead = leaders[0]
        score = lead.get("displayScore")
        score_text = f" ({float(score):.2f} out of 1.00)" if isinstance(score, (int, float)) else ""
        opening = f"{lead.get('name', 'The top option')} ranks first under the priorities and ratings entered{score_text}."
        runner_up = next((o for o in ranked if o.get("rank") not in (None, 1)), None)
        if runner_up and isinstance(score, (int, float)) and isinstance(runner_up.get("displayScore"), (int, float)):
            gap = float(score) - float(runner_up["displayScore"])
            opening += f" It leads {runner_up.get('name', 'the next option')} by {gap:.2f}."
    lead = leaders[0]
    contributions = sorted((c for c in lead.get("contributions", []) if isinstance(c, dict)),
                           key=lambda c: c.get("contribution", 0), reverse=True)
    if contributions:
        strongest = ", ".join(f"{c.get('criterion', 'criterion')} ({c.get('contribution', 0):.3f})"
                               for c in contributions[:2])
        opening += f" Its largest positive score contributions come from {strongest}."
    missing = sum(len(o.get("missing", [])) for o in options if isinstance(o, dict))
    failed = [str(o.get("name", "An option")) for o in options
              if isinstance(o, dict) and o.get("constraintStatus") == "fail"]
    if failed:
        opening += " " + ", ".join(failed) + (" do" if len(failed) > 1 else " does") + " not meet at least one hard limit."
    if missing:
        opening += f" {missing} rating(s) are still missing across the options, so the comparison may change when you fill them in."
    opening += " This is a weighted comparison of your stated assumptions, not a prediction or a decision made for you."
    return opening


def _draft_from_description(prompt: str) -> dict[str, Any]:
    """Create an editable starter draft when the optional local model is unavailable."""
    text = " ".join(str(prompt).split())[:8000]

    def split_items(value: str) -> list[str]:
        parts = re.split(r"\s*(?:,|;|\band\b|\bor\b)\s*", value, flags=re.IGNORECASE)
        cleaned = []
        for part in parts:
            item = re.sub(r"^[\s:–—-]+|[\s.!?]+$", "", part)
            item = re.sub(r"^(?:a|an|the)\s+", "", item, flags=re.IGNORECASE)
            item = re.sub(r"\s+(?:for|during)\s+(?:the\s+)?(?:summer|fall|spring|winter)$", "", item, flags=re.IGNORECASE)
            if item and item.casefold() not in {x.casefold() for x in cleaned}:
                cleaned.append(item[:120])
        return cleaned

    option_match = re.search(r"\b(?:between|among)\s+(.+?)(?:[.!?]|$)", text, re.IGNORECASE)
    if option_match:
        option_text = re.split(r"\b(?:because|and\s+i\s+(?:care|want|need)|i\s+(?:care|want|need))\b", option_match.group(1), maxsplit=1, flags=re.IGNORECASE)[0]
        options = split_items(option_text)[:5]
    else:
        options = []
    if len(options) < 2:
        options = ["Option 1", "Option 2"]

    criteria_match = re.search(r"\b(?:care\s+about|prioriti[sz]e|value|important\s+to\s+me\s+(?:are|is))\s+(.+?)(?:[.!?]|$)", text, re.IGNORECASE)
    criteria_names = split_items(criteria_match.group(1))[:8] if criteria_match else []
    if not criteria_names:
        criteria_names = ["Learning opportunities", "Mentorship", "Workload", "Pay"]
    criteria = []
    for name in criteria_names:
        direction = "cost" if re.search(r"\b(?:hours|workload|cost|expenses|commute|travel time)\b", name, re.IGNORECASE) and not re.search(r"\bmanageable\b", name, re.IGNORECASE) else "benefit"
        criteria.append({"name": name, "direction": direction, "importance": 3, "valueType": "ordinal"})
    return {"options": options, "criteria": criteria,
            "questions": ["Review the options and criteria, then adjust them to match your decision."],
            "requiresUserReview": True, "source": "description-draft"}


def _suggest_evidence_from_passages(query: str, passages: list[dict[str, Any]]) -> dict[str, Any]:
    """Return exact source excerpts and generic review questions without an LLM."""
    terms = {term.casefold() for term in re.findall(r"[\w'-]{3,}", query)}
    candidates = []
    for passage in passages:
        passage_text = str(passage.get("text", ""))
        for line in re.split(r"[\r\n]+", passage_text):
            for sentence in re.split(r"(?<=[.!?])\s+", line):
                quote = sentence.strip(" \t•-*\r\n")
                if len(quote) < 18:
                    continue
                words = {term.casefold() for term in re.findall(r"[\w'-]{3,}", quote)}
                overlap = len(terms & words)
                if overlap:
                    candidates.append((overlap, float(passage.get("relevance", 0) or 0), quote, passage))
    candidates.sort(key=lambda item: (item[0], item[1], len(item[2])), reverse=True)
    facts = []
    seen = set()
    for _, _, quote, passage in candidates:
        if quote in seen:
            continue
        seen.add(quote)
        facts.append({"claim": quote[:500], "quote": quote[:1200], "chunkId": passage["id"],
                      "filename": str(passage.get("filename", "")), "pageNo": passage.get("page_no"),
                      "optionHint": "", "criterionHint": ""})
        if len(facts) == 5:
            break
    return {"facts": facts,
            "questions": ["Does this information apply to the specific option and time period you are comparing?",
                          "What detail would you like to confirm or search for next?"],
            "requiresUserReview": True, "source": "passage-matching"}


def _ingestion_worker():
    while True:
        try:
            job = repo.claim_ingestion_job()
            if not job:
                time.sleep(0.35)
                continue
            try:
                _, chunks = extract_document(job["filename"], "", job["payload"])
                repo.complete_ingestion_job(job["id"], job["document_id"], chunks)
                _metric_increment("documentsReady")
            except IngestionError as exc:
                repo.fail_ingestion_job(job["id"], job["document_id"], str(exc))
                _metric_increment("documentsFailed")
            except Exception as exc:
                repo.fail_ingestion_job(job["id"], job["document_id"], "Unable to process this document.")
                _metric_increment("documentsFailed")
                sys.stderr.write("AURA ingestion error: %s\n" % type(exc).__name__)
        except Exception as exc:
            sys.stderr.write("AURA ingestion worker error: %s\n" % type(exc).__name__)
            time.sleep(1)


def _start_ingestion_worker():
    global _ingestion_worker_started
    with _ingestion_worker_lock:
        if _ingestion_worker_started:
            return
        repo.requeue_interrupted_ingestion_jobs()
        threading.Thread(target=_ingestion_worker, name="aura-document-ingestion", daemon=True).start()
        _ingestion_worker_started = True


class AuthenticationError(RuntimeError):
    pass


class RateLimitError(RuntimeError):
    pass


def _identifier(prefix: str, segment: str) -> str | None:
    match = re.fullmatch(re.escape(prefix) + r"/([^/]+)", segment)
    return unquote(match.group(1)) if match else None


class AuraHandler(BaseHTTPRequestHandler):
    server_version = "AURA/1.0"

    def log_message(self, fmt, *args):
        # Keep local decision content and request paths out of console logs.
        sys.stderr.write("AURA request %s\n" % (args[1] if len(args) > 1 else "completed"))

    def handle_one_request(self):
        started = time.perf_counter()
        try:
            return super().handle_one_request()
        finally:
            _metric_latency((time.perf_counter() - started) * 1000)

    def send_response(self, code, message=None):
        _metric_request(getattr(self, "command", "UNKNOWN"), code)
        return super().send_response(code, message)

    def _consume_ai_budget(self):
        try:
            limit = int(os.environ.get("AURA_AI_REQUESTS_PER_MINUTE", "8"))
        except ValueError:
            limit = 8
        limit = max(1, min(60, limit))
        token = self._session_token() or self.client_address[0]
        key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now_monotonic = time.monotonic()
        with _ai_rate_lock:
            if len(_ai_requests_by_session) > 2000:
                for old_key in list(_ai_requests_by_session)[:500]:
                    if not _ai_requests_by_session[old_key] or now_monotonic - _ai_requests_by_session[old_key][-1] > 3600:
                        _ai_requests_by_session.pop(old_key, None)
            recent = _ai_requests_by_session[key]
            while recent and now_monotonic - recent[0] >= 60:
                recent.popleft()
            if len(recent) >= limit:
                raise RateLimitError(f"Local AI request limit reached. Try again in about {max(1, int(60 - (now_monotonic - recent[0])))} seconds.")
            recent.append(now_monotonic)

    def _consume_login_budget(self):
        key = hashlib.sha256(self.client_address[0].encode("utf-8")).hexdigest()
        now_monotonic = time.monotonic()
        with _login_rate_lock:
            recent = _login_attempts[key]
            while recent and now_monotonic - recent[0] >= 300:
                recent.popleft()
            if len(recent) >= 5:
                raise RateLimitError("Too many sign-in attempts. Try again after five minutes.")
            recent.append(now_monotonic)

    def _clear_login_budget(self):
        key = hashlib.sha256(self.client_address[0].encode("utf-8")).hexdigest()
        with _login_rate_lock:
            _login_attempts.pop(key, None)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        super().end_headers()

    def _json(self, status: int, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if getattr(self, "auth_cookie", None):
            self.send_header("Set-Cookie", self.auth_cookie)
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        try: length = int(self.headers.get("Content-Length", "0"))
        except ValueError: raise DecisionValidationError("Invalid content length.") from None
        if length < 0 or length > MAX_BODY:
            raise DecisionValidationError("Request exceeds the 11 MB limit.")
        return self.rfile.read(length)

    def _read_json(self) -> dict:
        body = self._read_body()
        try: value = json.loads(body or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError): raise DecisionValidationError("Request body must be valid JSON.") from None
        if not isinstance(value, dict): raise DecisionValidationError("Request body must be a JSON object.")
        return value

    def _protect_origin(self):
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlparse(origin)
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.port != self.server.server_port:
                raise PermissionError("Cross-origin requests are not allowed.")

    def _session_token(self) -> str | None:
        jar = SimpleCookie()
        try: jar.load(self.headers.get("Cookie", ""))
        except Exception: return None
        morsel = jar.get("AURA_SESSION")
        return morsel.value if morsel else None

    def _authenticated(self) -> bool:
        token = self._session_token()
        return bool(token and repo.session_valid(token))

    def _issue_session(self):
        token = secrets.token_urlsafe(32)
        expiry = datetime.now(timezone.utc) + timedelta(hours=12)
        repo.create_session(token, expiry.isoformat(timespec="seconds"))
        self.auth_cookie = f"AURA_SESSION={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200"

    def _clear_session(self):
        token = self._session_token()
        if token: repo.revoke_session(token)
        self.auth_cookie = "AURA_SESSION=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"

    def _handle_error(self, exc):
        if isinstance(exc, PermissionError): self._json(403, {"error": str(exc)})
        elif isinstance(exc, AuthenticationError): self._json(401, {"error": str(exc)})
        elif isinstance(exc, RateLimitError): self._json(429, {"error": str(exc)})
        elif isinstance(exc, KeyError): self._json(404, {"error": str(exc).strip("'")})
        elif isinstance(exc, (DecisionValidationError, IngestionError)): self._json(422, {"error": str(exc)})
        elif isinstance(exc, ModelUnavailable): self._json(503, {"error": str(exc)})
        elif isinstance(exc, FileExistsError): self._json(409, {"error": str(exc)})
        else:
            self._json(500, {"error": "AURA could not complete the request."})
            sys.stderr.write("AURA error: %s\n" % type(exc).__name__)

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path.strip("/")
            if path == "api/health":
                return self._json(200, {"status": "ok", "modelConfigured": bool(os.environ.get("OLLAMA_MODEL"))})
            if path == "api/auth/status":
                return self._json(200, {"configured": repo.auth_configured(), "authenticated": self._authenticated()})
            if path.startswith("api/") and not self._authenticated():
                raise AuthenticationError("Sign in to access this AURA workspace.")
            if path == "api/metrics":
                return self._json(200, _metrics_snapshot())
            if path == "api/decisions": return self._json(200, {"decisions": repo.list_decisions()})
            decision_id = _identifier("api/decisions", path)
            if decision_id:
                return self._json(200, repo.get_decision(decision_id))
            parts = path.split("/")
            if len(parts) == 4 and parts[:2] == ["api", "decisions"] and parts[3] == "export":
                decision_id = unquote(parts[2])
                return self._json(200, {"decision": repo.get_decision(decision_id),
                    "evaluations": repo.list_evaluations(decision_id), "scenarios": repo.list_scenarios(decision_id),
                    "documents": repo.list_documents(decision_id), "evidencePassages": repo.list_chunks(decision_id),
                    "robustnessRuns": repo.list_robustness_runs(decision_id)})
            if len(parts) == 4 and parts[:2] == ["api", "decisions"] and parts[3] == "evaluations":
                return self._json(200, {"evaluations": repo.list_evaluations(unquote(parts[2]))})
            if len(parts) == 4 and parts[:2] == ["api", "decisions"] and parts[3] == "scenarios":
                return self._json(200, {"scenarios": repo.list_scenarios(unquote(parts[2]))})
            if len(parts) == 4 and parts[:2] == ["api", "decisions"] and parts[3] == "robustness-runs":
                return self._json(200, {"runs": repo.list_robustness_runs(unquote(parts[2]))})
            if len(parts) == 4 and parts[:2] == ["api", "decisions"] and parts[3] == "documents":
                return self._json(200, {"documents": repo.list_documents(unquote(parts[2]))})
            if len(parts) == 4 and parts[:2] == ["api", "decisions"] and parts[3] == "evidence":
                decision_id = unquote(parts[2])
                query = parse_qs(parsed.query).get("q", [""])[0]
                chunks = repo.list_chunks(decision_id)
                ranked = search_chunks(chunks, query)
                return self._json(200, {"query": query, "passages": ranked})
            if len(parts) == 3 and parts[:2] == ["api", "evaluations"]:
                return self._json(200, repo.get_evaluation(unquote(parts[2])))
            if path.startswith("api/"):
                return self._json(404, {"error": "API route not found."})
            return self._static(path)
        except Exception as exc: self._handle_error(exc)

    def _static(self, path: str):
        filename = "index.html" if path in {"", "index.html"} else path
        target = (STATIC / filename).resolve()
        if not target.is_relative_to(STATIC.resolve()) or not target.is_file():
            self.send_error(404); return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        try:
            self._protect_origin()
            parsed = urlparse(self.path)
            path = parsed.path.strip("/")
            if path == "api/auth/setup":
                body = self._read_json()
                repo.set_password(body.get("password"))
                self._issue_session()
                return self._json(201, {"authenticated": True})
            if path == "api/auth/login":
                body = self._read_json()
                self._consume_login_budget()
                if not repo.verify_password(body.get("password", "")):
                    raise AuthenticationError("Password is incorrect.")
                self._clear_login_budget()
                self._issue_session()
                return self._json(200, {"authenticated": True})
            if path == "api/auth/logout":
                self._clear_session()
                return self._json(200, {"authenticated": False})
            if path.startswith("api/") and not self._authenticated():
                raise AuthenticationError("Sign in to access this AURA workspace.")
            if path == "api/decisions":
                body = self._read_json()
                if not str(body.get("title", "")).strip() or not str(body.get("question", "")).strip():
                    raise DecisionValidationError("Decision title and question are required.")
                item = repo.create_decision(str(body["title"])[:160], str(body["question"])[:2000], body.get("model", {}))
                return self._json(201, item)
            parts = path.split("/")
            if len(parts) == 4 and parts[:2] == ["api", "decisions"] and parts[3] == "revisions":
                body = self._read_json(); return self._json(201, repo.save_revision(unquote(parts[2]), body.get("model", {})))
            if len(parts) == 4 and parts[:2] == ["api", "decisions"] and parts[3] == "evaluations":
                body = self._read_json(); decision_id = unquote(parts[2])
                revision = repo.get_revision(decision_id, body.get("revisionId"))
                result = evaluate(revision["model"])
                item = repo.save_evaluation(decision_id, revision["id"], result)
                _metric_increment("evaluations")
                return self._json(201, {**item, "result": result})
            if len(parts) == 4 and parts[:2] == ["api", "decisions"] and parts[3] == "scenarios":
                body = self._read_json()
                decision_id = unquote(parts[2])
                baseline = repo.get_evaluation(body.get("baselineEvaluationId", ""))
                if baseline["decision_id"] != decision_id or baseline["scenario_id"] is not None:
                    raise DecisionValidationError("Select a baseline evaluation for this decision.")
                base_revision = repo.get_revision(decision_id, baseline["revision_id"])
                apply_overrides(base_revision["model"], body.get("overrides", {}))
                scenario = repo.create_scenario(decision_id, body.get("baselineEvaluationId", ""), str(body.get("name", "What-if scenario"))[:160], body.get("overrides", {}))
                return self._json(201, scenario)
            if len(parts) == 4 and parts[:2] == ["api", "scenarios"] and parts[3] == "evaluate":
                scenario_id = unquote(parts[2]); scenario = repo.get_scenario(scenario_id)
                baseline = repo.get_evaluation(scenario["baseline_evaluation_id"])
                revision = repo.get_revision(scenario["decision_id"], baseline["revision_id"])
                scenario_model = apply_overrides(revision["model"], scenario["overrides"])
                result = evaluate(scenario_model)
                item = repo.save_evaluation(scenario["decision_id"], revision["id"], result, scenario_id, scenario_model)
                _metric_increment("scenarioEvaluations")
                comparison = {"baselineEvaluationId": baseline["id"], "scenarioEvaluationId": item["id"],
                              "baseline": baseline["result"], "scenario": result,
                              "scoreChanges": _score_changes(baseline["result"], result)}
                return self._json(201, {"evaluation": item, "comparison": comparison})
            if len(parts) == 4 and parts[:2] == ["api", "decisions"] and parts[3] == "sensitivity":
                body = self._read_json(); decision_id = unquote(parts[2])
                revision = repo.get_revision(decision_id, body.get("revisionId"))
                try:
                    minimum, maximum, steps = float(body.get("minimum", 1)), float(body.get("maximum", 5)), int(body.get("steps", 21))
                except (TypeError, ValueError): raise DecisionValidationError("Sensitivity range and step count must be numeric.") from None
                result = sensitivity(revision["model"], str(body.get("criterionId", "")), minimum, maximum, steps)
                return self._json(200, result)
            if len(parts) == 4 and parts[:2] == ["api", "decisions"] and parts[3] == "robustness":
                body = self._read_json(); decision_id = unquote(parts[2])
                revision = repo.get_revision(decision_id, body.get("revisionId"))
                try:
                    iterations = int(body.get("iterations", 1000))
                    seed = int(body.get("seed", 42))
                except (TypeError, ValueError):
                    raise DecisionValidationError("Iterations and seed must be integers.") from None
                result = robustness(revision["model"], body.get("assessmentRanges", {}), body.get("weightRanges", {}), iterations, seed)
                result["revisionId"] = revision["id"]
                result["inputHash"] = revision["input_hash"]
                settings = {"revisionId": revision["id"], "assessmentRanges": body.get("assessmentRanges", {}),
                            "weightRanges": body.get("weightRanges", {}), "iterations": iterations, "seed": seed}
                saved_run = repo.save_robustness_run(decision_id, revision["id"], revision["input_hash"], settings, result)
                _metric_increment("robustnessRuns")
                result["runId"] = saved_run["id"]
                result["createdAt"] = saved_run["created_at"]
                return self._json(200, result)
            if len(parts) == 5 and parts[:2] == ["api", "decisions"] and parts[3:] == ["evidence", "assist"]:
                body = self._read_json(); decision_id = unquote(parts[2])
                query = str(body.get("query", "")).strip()
                if len(query) < 2: raise DecisionValidationError("Enter a question or topic to look for in your sources.")
                ranked = search_chunks(repo.list_chunks(decision_id), query, limit=6)
                if local_model_configured():
                    self._consume_ai_budget()
                    _metric_increment("aiRequests")
                    try:
                        proposal = extract_evidence(query, ranked)
                        proposal["source"] = "local-ai"
                    except ModelUnavailable:
                        proposal = _suggest_evidence_from_passages(query, ranked)
                else:
                    proposal = _suggest_evidence_from_passages(query, ranked)
                return self._json(200, proposal)
            if len(parts) == 4 and parts[:2] == ["api", "decisions"] and parts[3] == "documents":
                return self._upload(unquote(parts[2]))
            if len(parts) == 5 and parts[:2] == ["api", "decisions"] and parts[3:] == ["evidence", "apply"]:
                body = self._read_json(); decision_id = unquote(parts[2]); chunk = repo.get_chunk(str(body.get("chunkId", "")))
                if chunk["decision_id"] != decision_id: raise KeyError("Evidence passage not found.")
                if chunk["verified_status"] != "verified": raise DecisionValidationError("Verify this source passage before applying a value.")
                option_id, criterion_id, value = str(body.get("optionId", "")), str(body.get("criterionId", "")), body.get("value")
                if value is None: raise DecisionValidationError("Enter a confirmed assessment value.")
                revision = repo.get_revision(decision_id)
                model = revision["model"]
                if option_id not in {o["id"] for o in model["options"]} or criterion_id not in {c["id"] for c in model["criteria"]}:
                    raise DecisionValidationError("Select an option and criterion from this decision.")
                # User action confirms the cited passage and applies the entered value to a new immutable revision.
                assessment = {"value": value, "status": "verified", "source": {"chunkId": chunk["id"], "filename": chunk["filename"], "pageNo": chunk["page_no"]}}
                model.setdefault("assessments", {}).setdefault(option_id, {})[criterion_id] = assessment
                updated = repo.save_revision(decision_id, model)
                repo.set_chunk_status(chunk["id"], "verified")
                return self._json(201, updated)
            if len(parts) == 4 and parts[:2] == ["api", "decisions"] and parts[3] == "assist":
                body = self._read_json()
                prompt = str(body.get("prompt", ""))
                if local_model_configured():
                    self._consume_ai_budget()
                    _metric_increment("aiRequests")
                    try:
                        proposal = propose_decision(prompt)
                        proposal["source"] = "local-ai"
                    except ModelUnavailable:
                        proposal = _draft_from_description(prompt)
                else:
                    proposal = _draft_from_description(prompt)
                return self._json(200, proposal)
            if len(parts) == 4 and parts[:2] == ["api", "decisions"] and parts[3] == "explain":
                body = self._read_json(); decision = repo.get_decision(unquote(parts[2]))
                evaluation = repo.get_evaluation(str(body.get("evaluationId", "")))
                if evaluation["decision_id"] != decision["id"]: raise KeyError("Evaluation not found.")
                explanation_source = "local-analysis"
                if local_model_configured():
                    self._consume_ai_budget()
                    _metric_increment("aiRequests")
                    try:
                        explanation = explain_result(decision["question"], evaluation["result"])
                        explanation_source = "local-ai"
                    except ModelUnavailable:
                        explanation = _explain_result_locally(evaluation["result"])
                else:
                    explanation = _explain_result_locally(evaluation["result"])
                return self._json(200, {"explanation": explanation, "isAdvisory": True,
                                        "source": explanation_source})
            return self._json(404, {"error": "API route not found."})
        except Exception as exc: self._handle_error(exc)

    def _upload(self, decision_id: str):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type.lower():
            raise DecisionValidationError("Upload must use multipart/form-data.")
        body = self._read_body()
        message = BytesParser(policy=default).parsebytes((f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n").encode() + body)
        upload = next((p for p in message.iter_parts() if p.get_filename()), None)
        if upload is None: raise DecisionValidationError("Select one file to upload.")
        filename = Path(upload.get_filename()).name.replace("\x00", "")[:200]
        data = upload.get_payload(decode=True) or b""
        if not data: raise IngestionError("The uploaded file is empty.")
        if len(data) > MAX_UPLOAD_BYTES: raise IngestionError("File exceeds the 10 MB upload limit.")
        if Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise IngestionError("Supported formats are PDF, TXT, and Markdown.")
        media_type = "application/pdf" if Path(filename).suffix.lower() == ".pdf" else "text/markdown" if Path(filename).suffix.lower() in {".md", ".markdown"} else "text/plain"
        digest = sha256(data)
        try:
            document = repo.enqueue_document(decision_id, filename, media_type, digest, data)
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc): raise FileExistsError("This file is already attached to the decision.") from None
            raise
        _metric_increment("documentsQueued")
        _start_ingestion_worker()
        return self._json(202, document)

    def do_PATCH(self):
        try:
            self._protect_origin()
            path = urlparse(self.path).path.strip("/")
            if path.startswith("api/") and not self._authenticated():
                raise AuthenticationError("Sign in to access this AURA workspace.")
            parts = path.split("/")
            if len(parts) == 3 and parts[:2] == ["api", "evidence"]:
                body = self._read_json()
                chunk_id, status = unquote(parts[2]), str(body.get("status", ""))
                chunk = repo.get_chunk(chunk_id)
                revision_change = None
                if status != "verified":
                    decision = repo.get_decision(chunk["decision_id"])
                    model = decision["currentRevision"]["model"]
                    changed = False
                    for values in model.get("assessments", {}).values():
                        for criterion_id, assessment in list(values.items()):
                            if isinstance(assessment, dict) and (assessment.get("source") or {}).get("chunkId") == chunk_id:
                                del values[criterion_id]; changed = True
                    if changed:
                        revision_change = repo.save_revision(chunk["decision_id"], model)["currentRevision"]["revisionNo"]
                result = repo.set_chunk_status(chunk_id, status)
                if revision_change is not None: result["decisionRevision"] = revision_change
                return self._json(200, result)
            return self._json(404, {"error": "API route not found."})
        except Exception as exc: self._handle_error(exc)

    def do_DELETE(self):
        try:
            self._protect_origin()
            path = urlparse(self.path).path.strip("/")
            if path.startswith("api/") and not self._authenticated():
                raise AuthenticationError("Sign in to access this AURA workspace.")
            parts = path.split("/")
            if len(parts) == 3 and parts[:2] == ["api", "decisions"]:
                repo.delete_decision(unquote(parts[2])); return self._json(200, {"deleted": True})
            if len(parts) == 3 and parts[:2] == ["api", "documents"]:
                repo.delete_document(unquote(parts[2])); return self._json(200, {"deleted": True})
            return self._json(404, {"error": "API route not found."})
        except Exception as exc: self._handle_error(exc)


def _score_changes(baseline: dict, scenario: dict) -> list[dict]:
    base = {o["optionId"]: o for o in baseline.get("options", [])}
    changes = []
    for option in scenario.get("options", []):
        original = base.get(option["optionId"], {})
        changes.append({"optionId": option["optionId"], "name": option["name"],
                        "baselineScore": original.get("displayScore"), "scenarioScore": option.get("displayScore"),
                        "delta": round(option["score"] - original["score"], 6) if option.get("score") is not None and original.get("score") is not None else None,
                        "baselineRank": original.get("rank"), "scenarioRank": option.get("rank"),
                        "constraintStatus": option.get("constraintStatus")})
    return changes


def main():
    host = os.environ.get("AURA_HOST", "127.0.0.1")
    port = int(os.environ.get("AURA_PORT", "8000"))
    if host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("AURA only binds to localhost. Do not expose this single-user workspace to a network.")
    server = ThreadingHTTPServer((host, port), AuraHandler)
    _start_ingestion_worker()
    print(f"AURA is ready at http://{host}:{port}")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == "__main__":
    main()

