from __future__ import annotations

"""Seal and evaluate the diagnostic ten-case generation.

This is an experiment-only adapter. It deliberately runs after generation has
stopped and never feeds official harness observations into Reach-Avoid or
DeepSeek. The one unfinished generation is represented by an empty patch as
requested for this sealed evaluation.
"""

import json
import os
import sys
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[2]
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))
EXPERIMENT_ROOT = Path(__file__).resolve().parent
RESULT_ROOT = EXPERIMENT_ROOT / "results"
RUN_ROOT = EXPERIMENT_ROOT / "runs"
EMPTY_PATCH = RUN_ROOT / "sphinx-doc__sphinx-8282" / "final.patch"
OFFICIAL_SOURCE = CODE_ROOT / "dataset" / "patchpsro_55_unique51" / "official_instances.jsonl"
OFFICIAL_DIAGNOSTIC = CODE_ROOT / "dataset" / "diagnostic10_official_instances.jsonl"

CASES = (
    "astropy__astropy-7746",
    "django__django-11742",
    "django__django-13321",
    "django__django-14534",
    "django__django-15695",
    "scikit-learn__scikit-learn-14092",
    "sphinx-doc__sphinx-8282",
    "sympy__sympy-20049",
    "django__django-12747",
    "django__django-13448",
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    os.environ["REACHPATCH_RA51_ROOT"] = str(EXPERIMENT_ROOT)
    os.environ["REACHPATCH_DIAGNOSTIC10"] = "1"
    from experiments.reachavoid_51 import runner

    results = {}
    for case_id in CASES:
        result_path = RESULT_ROOT / f"{case_id}.json"
        if case_id == "sphinx-doc__sphinx-8282":
            if not EMPTY_PATCH.is_file() or EMPTY_PATCH.read_bytes():
                raise RuntimeError("the unfinished sphinx prediction must be an empty patch")
            # Evaluation-only metadata; no Reach-Avoid evidence is fabricated.
            results[case_id] = {
                "schema": runner.SCHEMA,
                "instance_id": case_id,
                "status": "GENERATOR_BLOCKED_EXTERNAL",
                "run_root": str(RUN_ROOT / case_id),
                "p0_patch_path": str(EMPTY_PATCH),
                "p0_patch_hash": runner.content_hash(""),
                "p0_patch_sha256": runner._sha256(EMPTY_PATCH),
                "final_patch_path": str(EMPTY_PATCH),
                "final_patch_hash": runner.content_hash(""),
                "final_patch_sha256": runner._sha256(EMPTY_PATCH),
                "execution_backend": {"kind": "EVALUATION_EMPTY_PATCH"},
                "empty_patch": True,
                "generation_note": "unfinished generation; empty patch required by sealed evaluation",
                "component_evidence": {},
            }
            continue
        if not result_path.is_file():
            raise RuntimeError(f"missing generated result: {case_id}")
        result = read_json(result_path)
        if not Path(str(result.get("p0_patch_path", ""))).is_file():
            raise RuntimeError(f"missing p0 patch for {case_id}")
        if not Path(str(result.get("final_patch_path", ""))).is_file():
            raise RuntimeError(f"missing final patch for {case_id}")
        results[case_id] = result

    # Seal predictions before reading official-only instance data.
    runner.HARNESS_ROOT.mkdir(parents=True, exist_ok=True)
    runner._seal_predictions(results, "p0")
    runner._seal_predictions(results, "final")
    sealed = {
        "schema": runner.SCHEMA,
        "sealed_at": runner.utc_now(),
        "case_count": len(CASES),
        "instance_ids": list(CASES),
        "p0_predictions_sha256": runner._sha256(runner.HARNESS_ROOT / "sealed_p0_predictions.jsonl"),
        "final_predictions_sha256": runner._sha256(runner.HARNESS_ROOT / "sealed_final_predictions.jsonl"),
        "empty_patch_instance_ids": ["sphinx-doc__sphinx-8282"],
        "empty_patch_reason": "generation unfinished and user requested empty patch for official evaluation",
        "generator_results_sha256": runner.content_hash({key: value for key, value in results.items() if key != "sphinx-doc__sphinx-8282"}),
    }
    write_json(runner.SEALED_MANIFEST, sealed)

    # Materialize the selected official rows only after sealing.
    official_rows = read_jsonl(OFFICIAL_SOURCE)
    selected = [row for row in official_rows if str(row.get("instance_id")) in set(CASES)]
    if {str(row["instance_id"]) for row in selected} != set(CASES):
        raise RuntimeError("official dataset does not contain exactly the diagnostic cases")
    OFFICIAL_DIAGNOSTIC.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in selected),
        encoding="utf-8",
    )

    def official_rows_after_seal() -> list[dict]:
        payload = read_json(runner.SEALED_MANIFEST)
        if payload.get("case_count") != len(CASES):
            raise RuntimeError("diagnostic predictions are not sealed")
        rows = read_jsonl(OFFICIAL_DIAGNOSTIC)
        if {str(row["instance_id"]) for row in rows} != set(CASES):
            raise RuntimeError("sealed official rows do not match diagnostic set")
        return rows

    runner._official_rows_after_seal = official_rows_after_seal
    runner.OFFICIAL_PATH = OFFICIAL_DIAGNOSTIC

    # Official harness is the final isolated operation. Its outputs are
    # persisted for reporting only and are never read by the generator.
    harness = runner.harness(workers=2, timeout=1800)
    generation_summary = {
        "schema": runner.SCHEMA,
        "case_count": len(CASES),
        "sealed_case_count": len(CASES),
        "results": [results[key] for key in CASES],
        "status_counts": {},
        "empty_patch_instance_ids": ["sphinx-doc__sphinx-8282"],
    }
    write_json(EXPERIMENT_ROOT / "generation_summary_sealed.json", generation_summary)
    write_json(EXPERIMENT_ROOT / "official_evaluation_manifest.json", {
        "cases": list(CASES),
        "sealed_generation": sealed,
        "harness": harness,
        "official_results_not_feedback": True,
    })
    print(json.dumps({"status": "COMPLETE", "cases": len(CASES), "harness": harness}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
