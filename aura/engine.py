"""Deterministic decision scoring and sensitivity calculations."""

from __future__ import annotations

from copy import deepcopy
import math
import random
from typing import Any

ALGORITHM_VERSION = "aura-weighted-score-v1"
TIE_EPSILON = 0.01


class DecisionValidationError(ValueError):
    pass


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise DecisionValidationError(f"{label} must be numeric.")
    try:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise DecisionValidationError(f"{label} must be numeric.") from None


def validate_model(model: dict[str, Any]) -> None:
    options = model.get("options")
    criteria = model.get("criteria")
    if not isinstance(options, list) or not 2 <= len(options) <= 5:
        raise DecisionValidationError("A decision needs between 2 and 5 options.")
    if not isinstance(criteria, list) or not 2 <= len(criteria) <= 12:
        raise DecisionValidationError("A decision needs between 2 and 12 criteria.")
    if any(not isinstance(o, dict) for o in options) or any(not isinstance(c, dict) for c in criteria):
        raise DecisionValidationError("Options and criteria must be objects.")
    if any(not isinstance(o.get("name"), str) or not o["name"].strip() for o in options):
        raise DecisionValidationError("Every option needs a text name.")
    option_ids = [o.get("id") for o in options]
    criterion_ids = [c.get("id") for c in criteria]
    if not all(isinstance(x, str) and x for x in option_ids) or len(set(option_ids)) != len(option_ids):
        raise DecisionValidationError("Option IDs must be present and unique.")
    if not all(isinstance(x, str) and x for x in criterion_ids) or len(set(criterion_ids)) != len(criterion_ids):
        raise DecisionValidationError("Criterion IDs must be present and unique.")
    for criterion in criteria:
        raw_name = criterion.get("name", "")
        if not isinstance(raw_name, str):
            raise DecisionValidationError("Criterion names must be text.")
        name = raw_name.strip()
        if not name:
            raise DecisionValidationError("Each criterion needs a name.")
        if criterion.get("direction") not in {"benefit", "cost", "target"}:
            raise DecisionValidationError(f"Criterion '{name}' has an invalid direction.")
        if criterion.get("direction") == "target" and criterion.get("valueType") != "numeric":
            raise DecisionValidationError(f"Target criterion '{name}' requires a numeric scale.")
        importance = _number(criterion.get("importance"), f"Importance for '{name}'")
        if not 0 < importance <= 10:
            raise DecisionValidationError(f"Importance for '{name}' must be greater than 0 and at most 10.")
        if criterion.get("valueType", "ordinal") not in {"ordinal", "numeric"}:
            raise DecisionValidationError(f"Criterion '{name}' has an invalid value type.")
        if criterion.get("valueType") == "numeric":
            scale = criterion.get("scale") or {}
            lo, hi = _number(scale.get("min"), f"Minimum for '{name}'"), _number(scale.get("max"), f"Maximum for '{name}'")
            if lo >= hi:
                raise DecisionValidationError(f"Numeric criterion '{name}' requires min < max.")
            if criterion.get("direction") == "target":
                target = _number(scale.get("target"), f"Target for '{name}'")
                tolerance = _number(scale.get("tolerance"), f"Tolerance for '{name}'")
                if tolerance <= 0 or not lo <= target <= hi:
                    raise DecisionValidationError(f"Target scale for '{name}' requires a positive tolerance and an in-range target.")
            unit = criterion.get("unit", "")
            if not isinstance(unit, str) or len(unit) > 40:
                raise DecisionValidationError(f"Unit for '{name}' must be text with at most 40 characters.")
        constraint = criterion.get("constraint")
        if constraint:
            if constraint.get("operator") not in {"gte", "lte", "eq"}:
                raise DecisionValidationError(f"Constraint for '{name}' needs operator gte, lte, or eq.")
            _number(constraint.get("threshold"), f"Constraint threshold for '{name}'")
    assessments = model.get("assessments", {})
    if not isinstance(assessments, dict):
        raise DecisionValidationError("Assessments must be an object keyed by option ID.")
    for option_id, values in assessments.items():
        if option_id not in option_ids or not isinstance(values, dict):
            raise DecisionValidationError("Assessment references an unknown option.")
        for criterion_id, assessment in values.items():
            if criterion_id not in criterion_ids:
                raise DecisionValidationError("Assessment references an unknown criterion.")
            if isinstance(assessment, dict) and assessment.get("value") is not None:
                _number(assessment["value"], "Assessment value")


def _normalized(value: float, criterion: dict[str, Any]) -> float:
    if criterion.get("valueType", "ordinal") == "ordinal":
        if not 1 <= value <= 5 or value != round(value):
            raise DecisionValidationError(f"Ordinal value for '{criterion['name']}' must be a whole number from 1 to 5.")
        raw = (value - 1) / 4
        return 1 - raw if criterion["direction"] == "cost" else raw
    scale = criterion.get("scale") or {}
    lo, hi = float(scale["min"]), float(scale["max"])
    if not lo <= value <= hi:
        raise DecisionValidationError(f"Value for '{criterion['name']}' must be between {lo:g} and {hi:g}.")
    if criterion["direction"] == "target":
        return max(0.0, 1 - abs(value - float(scale["target"])) / float(scale["tolerance"]))
    raw = (value - lo) / (hi - lo)
    return 1 - raw if criterion["direction"] == "cost" else raw


def apply_overrides(model: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    result = deepcopy(model)
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict) or set(overrides) - {"weights", "assessments"}:
        raise DecisionValidationError("Scenario overrides must contain only weights and assessments.")
    if not isinstance(overrides.get("weights", {}), dict) or not isinstance(overrides.get("assessments", {}), dict):
        raise DecisionValidationError("Scenario weights and assessments must be objects.")
    criteria = {c["id"]: c for c in result["criteria"]}
    options = {o["id"] for o in result["options"]}
    for criterion_id, weight in (overrides.get("weights") or {}).items():
        if criterion_id not in criteria:
            raise DecisionValidationError("Scenario references an unknown criterion weight.")
        value = _number(weight, "Scenario weight")
        if value <= 0:
            raise DecisionValidationError("Scenario weights must be greater than zero.")
        criteria[criterion_id]["importance"] = value
    for option_id, values in (overrides.get("assessments") or {}).items():
        if option_id not in options or not isinstance(values, dict):
            raise DecisionValidationError("Scenario references an unknown option assessment.")
        result["assessments"].setdefault(option_id, {})
        for criterion_id, value in values.items():
            if criterion_id not in criteria:
                raise DecisionValidationError("Scenario references an unknown criterion assessment.")
            _number(value, "Scenario assessment value")
            result["assessments"][option_id][criterion_id] = {"value": value, "status": "scenario"}
    validate_model(result)
    return result


def evaluate(model: dict[str, Any]) -> dict[str, Any]:
    validate_model(model)
    criteria = model["criteria"]
    total_weight = sum(float(c["importance"]) for c in criteria)
    normalized_weights = {c["id"]: float(c["importance"]) / total_weight for c in criteria}
    results = []
    provenance = []
    any_missing = False
    for option in model["options"]:
        contributions, missing, violations, warnings = [], [], [], []
        assessments = model.get("assessments", {}).get(option["id"], {})
        weighted_score = 0.0
        for criterion in criteria:
            raw = assessments.get(criterion["id"])
            if isinstance(raw, dict):
                provenance.append({"optionId": option["id"], "criterionId": criterion["id"],
                                   "status": raw.get("status", "unknown"),
                                   "source": raw.get("source")})
                if raw.get("status") in {"unverified", "disputed"}:
                    warnings.append(f"{criterion['name']} has {raw['status']} evidence.")
                raw = raw.get("value")
            if raw is None:
                missing.append(criterion["name"])
                continue
            value = _number(raw, f"Value for {criterion['name']}")
            norm = _normalized(value, criterion)
            weight = normalized_weights[criterion["id"]]
            contribution = norm * weight
            weighted_score += contribution
            contributions.append({"criterionId": criterion["id"], "criterion": criterion["name"], "rawValue": value,
                                  "normalizedValue": round(norm, 6), "weight": round(weight, 6),
                                  "contribution": round(contribution, 6)})
            constraint = criterion.get("constraint")
            if constraint:
                threshold = float(constraint["threshold"])
                op = constraint["operator"]
                passed = value >= threshold if op == "gte" else value <= threshold if op == "lte" else abs(value - threshold) <= 1e-9
                if not passed:
                    violations.append({"criterion": criterion["name"], "operator": op, "threshold": threshold, "value": value})
        feasible = not violations if not missing else None
        any_missing |= bool(missing)
        results.append({"optionId": option["id"], "name": option["name"],
                        "score": round(weighted_score, 6) if not missing else None,
                        "displayScore": round(weighted_score, 2) if not missing else None,
                        "constraintStatus": "unknown" if missing and any(c.get("constraint") and c["name"] in missing for c in criteria) else ("fail" if violations else "pass"),
                        "feasible": feasible, "violations": violations, "missing": missing,
                        "contributions": contributions, "warnings": warnings, "rank": None})
    ranked = sorted((r for r in results if r["score"] is not None and r["feasible"]), key=lambda r: (-r["score"], r["name"].casefold()))
    if not any_missing:
        for idx, row in enumerate(ranked):
            row["rank"] = 1 if idx == 0 else (ranked[idx - 1]["rank"] if abs(row["score"] - ranked[idx - 1]["score"]) <= TIE_EPSILON else idx + 1)
    if any_missing:
        status = "incomplete"
    elif not ranked:
        status = "no_feasible_options"
    elif len(ranked) > 1 and abs(ranked[0]["score"] - ranked[1]["score"]) <= TIE_EPSILON:
        status = "tied"
    else:
        status = "complete"
    return {"algorithmVersion": ALGORITHM_VERSION, "status": status, "tieEpsilon": TIE_EPSILON,
            "options": results, "normalizedWeights": normalized_weights,
            "assessmentProvenance": provenance,
            "recommendation": ranked[0]["optionId"] if status == "complete" else None,
            "warnings": ["Missing values prevent a definitive recommendation; complete the highlighted assessments." ] if any_missing else []}


def sensitivity(model: dict[str, Any], criterion_id: str, minimum: float, maximum: float, steps: int = 21) -> dict[str, Any]:
    validate_model(model)
    if criterion_id not in {c["id"] for c in model["criteria"]}:
        raise DecisionValidationError("Unknown criterion for sensitivity analysis.")
    if not 2 <= steps <= 101 or minimum <= 0 or maximum <= 0 or minimum > maximum:
        raise DecisionValidationError("Sensitivity needs positive weights and 2–101 steps with min <= max.")
    points = []
    for i in range(steps):
        weight = minimum + (maximum - minimum) * i / (steps - 1)
        result = evaluate(apply_overrides(model, {"weights": {criterion_id: weight}}))
        winner = result["recommendation"]
        points.append({"weight": round(weight, 6), "winner": winner,
                       "scores": {row["optionId"]: row["displayScore"] for row in result["options"]}})
    transitions = []
    for point in points:
        if not transitions or transitions[-1]["winner"] != point["winner"]:
            transitions.append({"fromWeight": point["weight"], "winner": point["winner"]})
    baseline = evaluate(model)
    ranked = sorted((row for row in baseline["options"] if row["score"] is not None and row["feasible"]),
                    key=lambda row: (-row["score"], row["name"].casefold()))
    tornado = []
    if len(ranked) >= 2:
        first, second = ranked[:2]
        first_values = {c["criterionId"]: c for c in first["contributions"]}
        second_values = {c["criterionId"]: c for c in second["contributions"]}
        for criterion in model["criteria"]:
            left, right = first_values.get(criterion["id"]), second_values.get(criterion["id"])
            if left and right:
                tornado.append({"criterionId": criterion["id"], "criterion": criterion["name"],
                                "leaderContribution": left["contribution"], "runnerUpContribution": right["contribution"],
                                "scoreDifference": round(left["contribution"] - right["contribution"], 6),
                                "absoluteImpact": round(abs(left["contribution"] - right["contribution"]), 6)})
        tornado.sort(key=lambda row: (-row["absoluteImpact"], row["criterion"].casefold()))
    return {"criterionId": criterion_id, "steps": steps, "points": points, "transitions": transitions,
            "tornado": {"leader": ranked[0]["name"] if ranked else None,
                        "runnerUp": ranked[1]["name"] if len(ranked) > 1 else None,
                        "drivers": tornado}}


def robustness(model: dict[str, Any], assessment_ranges: dict[str, dict[str, list[float]]],
               weight_ranges: dict[str, list[float]] | None = None, iterations: int = 1000,
               seed: int = 42) -> dict[str, Any]:
    """Estimate ranking stability under explicitly supplied uniform input ranges.

    This is a reproducible sensitivity aid, not a probability forecast. Missing baseline
    assessments remain missing; constraints are evaluated on each sampled value.
    """
    validate_model(model)
    if not isinstance(iterations, int) or isinstance(iterations, bool) or not 100 <= iterations <= 10000:
        raise DecisionValidationError("Robustness simulations need 100–10,000 iterations.")
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 2**31 - 1:
        raise DecisionValidationError("Simulation seed must be an integer from 0 to 2,147,483,647.")
    if not isinstance(assessment_ranges, dict) or not isinstance(weight_ranges or {}, dict):
        raise DecisionValidationError("Assessment and weight ranges must be objects.")
    options = {o["id"] for o in model["options"]}
    criteria = {c["id"]: c for c in model["criteria"]}
    for option_id, ranges in assessment_ranges.items():
        if option_id not in options or not isinstance(ranges, dict):
            raise DecisionValidationError("Simulation references an unknown option.")
        for criterion_id, bounds in ranges.items():
            if criterion_id not in criteria:
                raise DecisionValidationError("Simulation references an unknown criterion.")
            if not isinstance(bounds, list) or len(bounds) != 2:
                raise DecisionValidationError("Each assessment range needs [minimum, maximum].")
            lo, hi = (_number(v, "Assessment range") for v in bounds)
            c = criteria[criterion_id]
            domain_lo, domain_hi = (1, 5) if c.get("valueType", "ordinal") == "ordinal" else (float(c["scale"]["min"]), float(c["scale"]["max"]))
            if lo > hi or lo < domain_lo or hi > domain_hi:
                raise DecisionValidationError(f"Assessment range for '{c['name']}' must stay within {domain_lo:g}–{domain_hi:g}.")
            current = model.get("assessments", {}).get(option_id, {}).get(criterion_id)
            if isinstance(current, dict): current = current.get("value")
            if current is None:
                raise DecisionValidationError("Set a baseline value before simulating its uncertainty.")
    for criterion_id, bounds in (weight_ranges or {}).items():
        if criterion_id not in criteria or not isinstance(bounds, list) or len(bounds) != 2:
            raise DecisionValidationError("Each weight range must reference a criterion and contain [minimum, maximum].")
        lo, hi = (_number(v, "Weight range") for v in bounds)
        if lo <= 0 or hi < lo or hi > 10:
            raise DecisionValidationError("Weight ranges must be positive, ordered, and at most 10.")

    rng = random.Random(seed)
    winners = {o["id"]: 0 for o in model["options"]}
    score_samples = {o["id"]: [] for o in model["options"]}
    no_clear_winner = 0
    for _ in range(iterations):
        sample = deepcopy(model)
        c_by_id = {c["id"]: c for c in sample["criteria"]}
        for cid, bounds in (weight_ranges or {}).items():
            c_by_id[cid]["importance"] = rng.uniform(*bounds)
        for oid, ranges in assessment_ranges.items():
            for cid, bounds in ranges.items():
                value = rng.uniform(*bounds)
                if criteria[cid].get("valueType", "ordinal") == "ordinal":
                    value = max(1, min(5, int(round(value))))
                sample.setdefault("assessments", {}).setdefault(oid, {})[cid] = {"value": value, "status": "simulation"}
        result = evaluate(sample)
        winner = result["recommendation"]
        if winner is None:
            no_clear_winner += 1
        else:
            winners[winner] += 1
        for row in result["options"]:
            if row["score"] is not None and row["feasible"]:
                score_samples[row["optionId"]].append(row["score"])

    def percentile(values: list[float], p: float):
        if not values: return None
        ordered = sorted(values)
        position = (len(ordered) - 1) * p
        lower = math.floor(position); upper = math.ceil(position)
        return ordered[lower] if lower == upper else ordered[lower] * (upper - position) + ordered[upper] * (position - lower)

    return {"simulationVersion": "aura-uniform-robustness-v1", "seed": seed, "iterations": iterations,
            "sampling": "independent uniform ranges supplied by the user; ordinal values are rounded to the nearest whole rating",
            "noClearWinnerShare": round(no_clear_winner / iterations, 4),
            "options": [{"optionId": o["id"], "name": o["name"], "winShare": round(winners[o["id"]] / iterations, 4),
                         "meanScore": round(sum(score_samples[o["id"]]) / len(score_samples[o["id"]]), 4) if score_samples[o["id"]] else None,
                         "scoreP05": round(percentile(score_samples[o["id"]], .05), 4) if score_samples[o["id"]] else None,
                         "scoreP95": round(percentile(score_samples[o["id"]], .95), 4) if score_samples[o["id"]] else None}
                        for o in model["options"]]}

