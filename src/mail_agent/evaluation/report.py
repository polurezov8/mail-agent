from __future__ import annotations

from rich.console import Console
from rich.table import Table

from .dataset import EvalReport


def print_report(report: EvalReport, console: Console | None = None) -> None:
    console = console or Console()
    if report.total == 0:
        console.print("[yellow]No fixtures. Run `mail-agent eval seed` first.[/yellow]")
        return

    summary = Table(title="Eval summary")
    summary.add_column("Metric")
    summary.add_column("Value", justify="right")
    summary.add_row("Total examples", str(report.total))
    summary.add_row(
        "Bucket accuracy",
        f"{report.bucket_accuracy * 100:.1f}%  ({report.bucket_correct}/{report.total})",
    )
    summary.add_row(
        "Rule accuracy (where labeled)",
        f"{report.rule_accuracy * 100:.1f}%  ({len(report.rule_examples)} examples)",
    )
    console.print(summary)

    breakdown = Table(title="By expected bucket")
    breakdown.add_column("Bucket")
    breakdown.add_column("Correct", justify="right")
    breakdown.add_column("Total", justify="right")
    breakdown.add_column("Acc", justify="right")
    for bucket, (correct, total) in report.bucket_breakdown().items():
        acc = (correct / total * 100) if total else 0
        breakdown.add_row(bucket.value, str(correct), str(total), f"{acc:.0f}%" if total else "-")
    console.print(breakdown)

    if report.failures:
        fail = Table(title=f"Failures ({len(report.failures)})", show_lines=False)
        fail.add_column("From", overflow="fold")
        fail.add_column("Subject", overflow="fold")
        fail.add_column("Expected")
        fail.add_column("Actual")
        fail.add_column("Conf", justify="right")
        fail.add_column("Source")
        for r in report.failures:
            expected = r.example.expected_bucket.value
            if r.example.expected_rule_name:
                expected += f" / {r.example.expected_rule_name}"
            actual = r.actual_bucket.value
            if r.actual_rule_name:
                actual += f" / {r.actual_rule_name}"
            fail.add_row(
                r.example.email.from_email,
                r.example.email.subject,
                expected,
                actual,
                f"{r.confidence:.2f}",
                r.source,
            )
        console.print(fail)
