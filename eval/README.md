# Eval harness

Regression gate for classifier changes. Run before shipping any rule or prompt edit.

## Workflow

```bash
# 1. Bootstrap fixtures from your last triage runs (gitignored; personal mail).
uv run mail-agent eval seed --account personal --limit 20

# 2. Open eval/fixtures.yaml. Verify the labels. Fix any mislabels.
#    For each example, set:
#      expected_bucket: ignore | notify | respond
#      expected_rule_name: <rule from config/rules.yaml> | null
#      notes: free text

# 3. Run.
uv run mail-agent eval run --min-bucket-accuracy 0.85
```

## Schema

```yaml
examples:
  - email:
      id: "msg_id_from_gmail"
      thread_id: "..."
      account: "personal"
      from_email: "..."
      subject: "..."
      snippet: "..."
      headers: {...}
      received_at: "2026-05-20T21:00:00+00:00"
    expected_bucket: ignore
    expected_rule_name: newsletters   # null if no rule expected
    notes: "LinkedIn cold invite"
```

## When to add fixtures

- Whenever the classifier surprises you (mis-bucket, wrong rule)
- New rule added → add positive + negative examples
- LLM prompt changed → run before merging

## Files

- `fixtures.yaml` — gitignored. Personal mail; never commit.
- Optional `fixtures.public.yaml` (you create) — safe-to-commit synthetic examples.

## Notes

- Bucket accuracy is the primary metric.
- Rule accuracy only counted on examples that set `expected_rule_name`.
- `expected_rule_name: null` = any rule (or LLM with null rule) is acceptable as long as bucket matches.
