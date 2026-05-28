from __future__ import annotations

import json
from pathlib import Path


def write_eval_reports(results, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "name": result.name,
            "passed": result.passed,
            "expected": result.expected,
            "actual": result.actual,
            "failures": result.failures,
        }
        for result in results
    ]
    (output_dir / "eval_report.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = []
    for item in payload:
        lines.append(f"{item['name']}: {'PASS' if item['passed'] else 'FAIL'}")
        phase_trace = item["actual"].get("phase_trace", {})
        for merchant, phases in phase_trace.items():
            if phases:
                lines.append(f"  - {merchant} phase trace: {' -> '.join(phases)}")
        for failure in item["failures"]:
            lines.append(f"  - {failure}")
    (output_dir / "eval_report.txt").write_text("\n".join(lines), encoding="utf-8")
