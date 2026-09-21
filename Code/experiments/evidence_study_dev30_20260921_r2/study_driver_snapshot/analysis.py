"""Issue-block estimates; missing provider usage never becomes measured zero."""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import random
import statistics


def distribution(values):
    if not values:
        return {"count": 0, "mean": None, "p50": None, "p90": None, "p95": None}
    ordered = sorted(values)
    def quantile(p):
        position = (len(ordered) - 1) * p
        low = int(position)
        high = min(low + 1, len(ordered) - 1)
        return ordered[low] + (ordered[high] - ordered[low]) * (position - low)
    return {"count": len(values), "mean": statistics.mean(values),
            "p50": quantile(.5), "p90": quantile(.9), "p95": quantile(.95)}


def bootstrap_factorial(issue_vectors, *, seed=20260920, samples=10000):
    """Mean issue-level additive contrasts in order F00,F10,F01,F11."""
    names = ("demand_at_no_reuse", "reuse_at_fixed", "interaction")
    if not issue_vectors:
        return {"status": "NO_COMPLETE_BLOCKS", "effects": {}}
    contrasts = [(b-a, c-a, d-b-c+a) for a,b,c,d in issue_vectors]
    rng = random.Random(seed)
    draws = [[] for _ in names]
    for _ in range(samples):
        sample = rng.choices(contrasts, k=len(contrasts))
        for index in range(3):
            draws[index].append(statistics.mean(row[index] for row in sample))
    effects = {}
    for index, name in enumerate(names):
        ordered = sorted(draws[index])
        effects[name] = {"mean_tokens_per_issue": statistics.mean(row[index] for row in contrasts),
            "ci95": [ordered[int((samples-1)*p)] for p in (.025,.975)] if len(contrasts)>1 else None}
    return {"status": "EXPLORATORY" if len(contrasts)>1 else "DESCRIPTIVE_SINGLE_ISSUE",
            "independent_issues": len(contrasts), "effects": effects}

from reachpatch.execution.request_journal import read_events, reconcile_requests


def bootstrap_cost_ratio(pairs, *, seed=20260920, samples=10000):
    if not pairs or sum(a for a, _ in pairs) <= 0:
        return {"status": "UNDEFINED_ZERO_BASELINE", "reduction": None, "ci95": None}
    rng = random.Random(seed)
    observed = 1 - sum(b for _, b in pairs) / sum(a for a, _ in pairs)
    ratios = []
    for _ in range(samples):
        draw = rng.choices(pairs, k=len(pairs))
        denominator = sum(a for a, _ in draw)
        if denominator > 0:
            ratios.append(1 - sum(b for _, b in draw) / denominator)
    ratios.sort()
    ci = [ratios[int((len(ratios) - 1) * p)] for p in (.025, .975)]
    return {"status": "DESCRIPTIVE_SINGLE_ISSUE" if len(pairs) == 1 else "EXPLORATORY",
            "independent_issues": len(pairs), "reduction": observed,
            "ci95": ci if len(pairs) > 1 else None}


def missing_outcome_bounds(outcomes):
    n = len(outcomes)
    if not n:
        return {"status": "NO_OUTCOMES", "lower": None, "upper": None}
    successes = sum(x is True for x in outcomes)
    missing = sum(x is None for x in outcomes)
    return {"n": n, "resolved": successes, "missing": missing,
            "lower": successes / n, "upper": (successes + missing) / n}


def summarize(root: Path, protocol):
    rows = []
    for cell in protocol["cells"]:
        path = root / "cells" / cell["cell_id"] / "cell_result.json"
        if not path.exists():
            continue
        row = {**cell, **json.loads(path.read_text())}
        events, truncated = read_events(Path(row["journal_path"]))
        cost = reconcile_requests(events)
        row["cost"] = {k: v for k, v in cost.items() if k != "requests"}
        row["cost"]["truncated_records"] = truncated
        row["cost"]["journal_present"] = Path(row["journal_path"]).is_file()
        stages = {}
        for event in events:
            if event["kind"] not in {"MODEL_ADMITTED", "MODEL", "MODEL_ERROR"}:
                continue
            stage = stages.setdefault(event["stage"], {"calls": 0, "reported_tokens": 0,
                                                       "model_seconds": 0})
            stage["calls"] += event["kind"] == "MODEL_ADMITTED"
            stage["reported_tokens"] += event.get("usage", {}).get("total_tokens", 0) or 0
            stage["model_seconds"] += event.get("seconds", 0)
        row["stage_costs"] = stages
        efficiency = Path(row["run_root"]) / "token_efficiency.json"
        evidence = json.loads(efficiency.read_text()) if efficiency.exists() else {}
        row["mechanism_metrics"] = {"available": efficiency.exists(),
            "events": evidence.get("events", {}),
            "context_elided_bytes": evidence.get("context_elided_bytes"),
            "question_reopen_count": evidence.get("question_reopen_count")}
        rows.append(row)
    arms = {}
    for arm in protocol["arms"]:
        selected = [r for r in rows if r["arm"] == arm]
        counts = defaultdict(int)
        for row in selected:
            counts[row["status"]] += 1
        arms[arm] = {"completed_cells": len(selected), "statuses": dict(counts),
                     "admitted_model_calls": sum(r["cost"]["model_calls"] for r in selected),
                     "reported_tokens_lower_bound": sum(r["cost"]["reported_tokens_lower_bound"] for r in selected),
                     "unknown_usage_requests": sum(r["cost"]["unknown_usage_requests"] for r in selected),
                     "missing_journals": sum(not r["cost"]["journal_present"] for r in selected),
                     "controller_certified": sum(r.get("controller_certified", False) for r in selected),
                     "public_acceptance": sum(r.get("public_acceptance", False) for r in selected),
                     "elapsed_seconds": sum(r["elapsed_seconds"] for r in selected)}
        arms[arm]["distributions"] = {
            "elapsed_seconds": distribution([r["elapsed_seconds"] for r in selected]),
            "calls": distribution([r["cost"]["model_calls"] for r in selected]),
            "reported_tokens": distribution([r["cost"]["reported_tokens_lower_bound"] for r in selected])}
        stages = {}
        for row in selected:
            for stage, metrics in row["stage_costs"].items():
                total = stages.setdefault(stage, {"calls":0,"reported_tokens":0,"model_seconds":0})
                for key,value in metrics.items():
                    total[key] += value
        arms[arm]["stage_costs"] = stages
        arms[arm]["mechanism_artifacts_missing"] = sum(not r["mechanism_metrics"]["available"] for r in selected)
    grouped = defaultdict(dict)
    for row in rows:
        grouped[(row["instance_id"], row["repetition"])][row["arm"]] = row
    pairs_by_issue = defaultdict(lambda: [0, 0])
    factorial_by_issue = defaultdict(lambda: [0, 0, 0, 0])
    for (issue, _), block in grouped.items():
        if set(block) == set(protocol["arms"]):
            for i, name in enumerate(("F00", "F11")):
                pairs_by_issue[issue][i] += block[name]["cost"]["reported_tokens_lower_bound"]
            for i,name in enumerate(("F00","F10","F01","F11")):
                factorial_by_issue[issue][i] += block[name]["cost"]["reported_tokens_lower_bound"] / protocol["repetitions"]
    comparable = (len(rows) == len(protocol["cells"]) and
                  all(not v["missing_journals"] and not v["unknown_usage_requests"] for v in arms.values()) and
                  not any(row["cost"]["truncated_records"] for row in rows))
    result = {"scope": protocol["scope"], "completed_cells": len(rows),
              "registered_cells": len(protocol["cells"]), "arms": arms, "rows": rows,
              "cost_comparison": bootstrap_cost_ratio(list(pairs_by_issue.values()),
                   seed=protocol["analysis"]["seed"], samples=protocol["analysis"]["bootstrap_samples"])
                   if comparable else {"status": "INCOMPLETE_OR_UNKNOWN_COST"},
              "effectiveness_claim": "NOT_ESTABLISHED_BY_DEVELOPMENT_EXPERIMENT"}
    if comparable:
        result["factorial_issue_bootstrap"] = bootstrap_factorial(list(factorial_by_issue.values()),
            seed=protocol["analysis"]["seed"], samples=protocol["analysis"]["bootstrap_samples"])
        c = {arm: data["reported_tokens_lower_bound"] for arm, data in arms.items()}
        result["factorial_total_token_contrasts"] = {
            "demand_at_no_reuse": c["F10"] - c["F00"],
            "reuse_at_fixed": c["F01"] - c["F00"],
            "interaction": c["F11"] - c["F10"] - c["F01"] + c["F00"]}
    (root / "analysis.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def write_paper_table(result, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = [r"\begin{tabular}{lrrrr}", r"\toprule",
             r"Arm & Runs & Public pass & Calls & Reported tokens \\", r"\midrule"]
    for arm, data in sorted(result["arms"].items()):
        lines.append(f"{arm} & {data['completed_cells']} & {data['public_acceptance']} & "
                     f"{data['admitted_model_calls']:,} & {data['reported_tokens_lower_bound']:,} " + r"\\")
    lines.extend([r"\bottomrule", r"\end{tabular}"])
    destination.write_text("\n".join(lines) + "\n")
