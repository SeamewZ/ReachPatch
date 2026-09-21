"""Publish sealed developmental results; never promote them to confirmation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def publish(root: Path, *, compile_pdf=False):
    repository = Path(__file__).resolve().parents[2]
    paper = repository / "Paper/fse2027"
    protocol = json.loads((root / "protocol.json").read_text())
    sealed = json.loads((root / "sealed_study.json").read_text())
    if sealed["protocol_sha256"] != hashlib.sha256((root / "protocol.json").read_bytes()).hexdigest():
        raise ValueError("paper export requires consistent study seal")
    analysis = json.loads((root / "analysis.json").read_text())
    if analysis["completed_cells"] != len(protocol["cells"]):
        raise ValueError("do not publish incomplete cells as a completed study")
    for cell in sealed["cells"]:
        if hashlib.sha256(Path(cell["patch_path"]).read_bytes()).hexdigest() != cell["patch_sha256"]:
            raise ValueError("sealed patch mutated before publication")
    output = paper / "generated"
    output.mkdir(parents=True, exist_ok=True)
    is_smoke = protocol["scope"] == "public_smoke"
    if is_smoke:
        official = None
    else:
        official = json.loads((root / "official_outcomes.json").read_text())
        if len(official["rows"]) != len(protocol["cells"]):
            raise ValueError("official assessment incomplete; keep paper marked pending")
    filename = "prospective_smoke_results" if is_smoke else "development_results"
    lines = [r"\subsection{Prospective integration study}" if is_smoke else
             r"\subsection{Prospective development-cohort results}"]
    if is_smoke:
        lines.append("We ran a blocked scheduling-by-reuse factorial on one public Python fixture, "
                     f"with {protocol['repetitions']} independently generated runs per arm. "
                     "Arm order was randomized within each repetition; two blocks could run concurrently. "
                     "The target requires empty-list support while preserving first-element behavior and "
                     "a TypeError for None. Independent public executions check both target and preservation "
                     "twice on the baseline and selected patch. This is an integration experiment, not "
                     "an estimate of repository-level resolution.")
    else:
        lines.append(f"We evaluated {len(protocol['case_ids'])} exposed development issues using "
                     "repository-stratified sampling and randomized arm order within issue blocks. "
                     "All four arms used DeepSeek Flash, temperature zero, a 40-call/250,000-token "
                     "admission ceiling, a 900-second case budget, and at most two repair revisions. "
                     "Each cell received one whole-case attempt; generation failures remain in the denominator. "
                     "Official tests were opened only after every registered cell had sealed its final patch. "
                     "These are developmental, not held-out confirmatory, results.")
    lines += [r"\begin{table}[t]", r"\caption{Prospective public-fixture factorial. Pass counts are independent public acceptance, not official Resolved@1.}" if is_smoke else
              r"\caption{Prospective development factorial. Missing official outcomes are reported separately; reported tokens exclude unknown usage rather than imputing it as free.}",
              r"\label{tab:prospective-smoke}" if is_smoke else r"\label{tab:development}",
              r"\begin{tabular}{@{}lrrrrr@{}}", r"\toprule",
              r"Arm & Runs & Public pass & Calls & Reported tokens & Unknown usage \\" if is_smoke else
              r"Arm & Resolved & Errors & Calls & Reported tokens & Unknown usage \\", r"\midrule"]
    for arm, values in sorted(analysis["arms"].items()):
        if is_smoke:
            first, second = values["completed_cells"], values["public_acceptance"]
        else:
            first = f"{official['arms'][arm]['resolved']}/{official['arms'][arm]['n']}"
            second = official["arms"][arm]["missing"]
        lines.append(f"{arm} & {first} & {second} & {values['admitted_model_calls']:,} & "
                     f"{values['reported_tokens_lower_bound']:,} & {values['unknown_usage_requests']} " + r"\\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    comparison = analysis["cost_comparison"]
    if comparison.get("reduction") is not None:
        reduction = 100 * comparison["reduction"]
        lines.append(f"The F11-versus-F00 ratio-of-totals token reduction is {reduction:.1f}\\%. " + (
            "We do not compute an issue-level confidence interval from repetitions of a single fixture."
            if is_smoke else
            f"The exploratory issue-block bootstrap 95\\% interval is [{100 * comparison['ci95'][0]:.1f}\\%, {100 * comparison['ci95'][1]:.1f}\\%]. "
            "This interval addresses sampling variability, not development exposure or model contamination."))
    else:
        lines.append("Incomplete accounting prevents a complete-cost contrast; the token totals must be read as lower bounds.")
    lines.append("These observations do not establish repair non-inferiority or effectiveness of each individual reuse mechanism. "
                 "A non-significant capability difference would not establish equivalence.")
    (output / f"{filename}.tex").write_text("\n".join(lines) + "\n")
    (output / f"{filename}.json").write_text(json.dumps({"study_root": str(root),
        "protocol_sha256": sealed["protocol_sha256"], "analysis": analysis, "official": official}, indent=2) + "\n")
    if compile_pdf:
        compiler = Path("/tmp/reachpatch-tex-bin/tectonic")
        if not compiler.is_file():
            raise FileNotFoundError("results exported; Tectonic unavailable for PDF compilation")
        import os
        with (root / "paper_compile.log").open("w") as log:
            subprocess.run([str(compiler), "-X", "compile", "main.tex", "--keep-logs", "--keep-intermediates"],
                cwd=paper, env={**os.environ, "XDG_CACHE_HOME": "/tmp/reachpatch-tex-cache"},
                stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--compile", action="store_true")
    args = parser.parse_args()
    publish(args.root.resolve(), compile_pdf=args.compile)
