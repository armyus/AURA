"""Run the checked-in synthetic decision evaluation cases."""

import json
import math
from pathlib import Path
from time import perf_counter

from .engine import evaluate


def main():
    cases = json.loads((Path(__file__).resolve().parent.parent / "samples" / "evaluation_cases.json").read_text(encoding="utf-8"))
    failures, latencies = 0, []
    for case in cases:
        started = perf_counter()
        result = evaluate(case["model"])
        latencies.append((perf_counter() - started) * 1000)
        winner = result["recommendation"]
        ok = result["status"] == case["expected_status"] and winner == case["expected_winner"]
        print(f"{'PASS' if ok else 'FAIL'}  {case['name']} | status={result['status']} winner={winner}")
        failures += not ok
    ordered = sorted(latencies)
    p95 = ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)] if ordered else 0
    print(f"Accuracy/reliability: {len(cases)-failures}/{len(cases)} ({(len(cases)-failures)/max(1,len(cases)):.0%})")
    print(f"Deterministic engine latency: mean={sum(latencies)/max(1,len(latencies)):.3f} ms, p95={p95:.3f} ms")
    print("Model cost: no LLM calls in this synthetic engine suite; optional local-model runtime is not metered here.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

