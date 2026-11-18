# Offline terminal demo

Captured on 2026-11-18 with Python 3.11.7 from this checkout. The commands below use a private, ignored `.agentdbg/demo-evidence` store. Run IDs and output are from the actual bundled fixture runs; no provider or external service was called.

## Delivery: the tool says delivered, the model says in transit

```console
$ runsplice --store .agentdbg/demo-evidence record --capture-content -- python -m agentdbg.delivery_example
run d4ecb681c5f0 answer=in transit expected=delivered
$ runsplice --store .agentdbg/demo-evidence inspect d4ecb681c5f0 --step 2
run d4ecb681c5f0: wrong_answer replayable=True
step 2: model ok origin=recorded
request: {
  "model": "local-fixture",
  "tool_results": [
    {
      "name": "track_package",
      "result": {
        "package_id": "P-8",
        "status": "delivered"
      }
    }
  ]
}
response: {
  "answer": "in transit",
  "model": "local-fixture"
}
```

The `inspect` block is a verbatim excerpt with the duration and later state, failure, and context lines omitted for brevity.

## Invoice: a stale tool result reaches the answer

```console
$ runsplice --store .agentdbg/demo-evidence record --capture-content -- python -m agentdbg.example
run ccf4b6e0b93a answer=unpaid expected=paid
$ runsplice --store .agentdbg/demo-evidence inspect ccf4b6e0b93a --step 2
run ccf4b6e0b93a: wrong_answer replayable=True
step 2: tool ok origin=recorded
request: {
  "arguments": {
    "invoice_id": "INV-42"
  },
  "name": "search_invoice"
}
response: {
  "as_of": "2025-01-01",
  "invoice_id": "INV-42",
  "status": "unpaid"
}
```

The `inspect` block is a verbatim excerpt with the duration and state delta omitted. Inspecting step 4 of the same run showed the `search_invoice` result in the model request and the final response `"answer": "unpaid"`.

## Linux Docker replay and response experiments

The [Ubuntu release smoke job](https://github.com/weber-stephen/Agent-Replay-Lab/actions/runs/44393008223/job/135115376241) ran the complete CLI flows on 2026-11-18. These are excerpts of its actual output; the runner used a temporary store and deleted it after the job. Both original runs were inspected again afterward and remained `wrong_answer` with `origin=recorded` at the edited step.

```console
=== delivery ===
run 6d82dc9923c8 answer=in transit expected=delivered
5301f089e82d replayed
efd05246f698 replayed
first changed response at step 2:
answer: in transit → delivered

=== invoice ===
run 84f8fc438ab4 answer=unpaid expected=paid
0c3e58ece87f replayed
26235144e5d3 blocked
first changed response at step 2:
no final answer comparison
```

The invoice fork stopped because the corrected lookup changed a later model request. The blocked fork establishes the first difference, not a corrected final answer. The earlier recording excerpts came from a Mac without Docker; the Linux results above are from a separate CI run.
