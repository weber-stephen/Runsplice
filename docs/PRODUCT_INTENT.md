# Product intent

## Problem and audience

An agent developer needs to answer: **Why did this run fail, and what would happen if one model or tool response had been different?** The intended workflow is to record a run, inspect the model and tool steps around a failure, resume from a saved checkpoint with one substituted response, and compare the resulting path with the original. The host application remains responsible for prompts, tool permissions, retries, and stopping rules.

## Use cases

| Use case | What the developer checks | Prototype status |
| --- | --- | --- |
| A stale invoice lookup produces an `unpaid` answer | Inspect the stale tool result and the later model request; substitute `paid` and identify the first changed request | Bundled example; the fork blocks at the changed request |
| A delivery model says `in transit` after a tool reports `delivered` | Replace the final model response and compare the answer | Bundled example; the fork completes with `delivered` |
| A support ticket marked `low` priority gets routine handling | Replace the ticket result with `high` and see where the next tool request changes | Bundled example; the fork blocks at the changed request |
| A model request carries repeated or growing context | Inspect message changes, sizes, and repeated tool-output candidates | Partly shown by the terminal inspector; byte counts are not provider token counts |
| A developer debugs their own agent | Capture its model and tool boundaries and test a corrected response | OpenAI Agents SDK and Claude Agent SDK wrappers support new sessions, with provider-specific fork behavior |

The blocked examples are useful results: they show the point where a corrected response changes the agent's next request. The debugger must not silently use a captured response for that different request.

## What deterministic replay means

Replay restores saved agent state in a fresh process and supplies captured responses only when the next model or tool request matches the recorded request. A developer can override a selected response; an unmatched request is **blocked**. The original run remains intact and the fork is saved separately. Step origins identify recorded, overridden, and blocked behavior.

This does not reproduce a model's hidden behavior, rewind an external service, or guarantee the same result from a fresh live call. The current prototype makes no live model or tool calls during replay. Live calls after divergence are an intended possibility, not a current CLI feature; any future partly live fork must be labeled as such.

## Integration boundary and current scope

The intended library boundary sits around the host agent's model and tool calls. The host must provide complete serializable state through `get_state()` and restore it through `restore_state(snapshot)` in a fresh process. Its model and tool operations must pass through the recording boundary, and checkpoints belong after completed steps when state is consistent. Work hidden inside an uninstrumented library or external service cannot be replayed from these checkpoints. An incomplete state snapshot limits what a replay can establish.

The three offline examples demonstrate that contract with fixture responses and a Linux Docker worker. The OpenAI Agents SDK wrapper instead reruns a new local SDK session from its first turn, supplying recorded responses while requests match; optional live continuation begins only after an edited response changes the path. The Claude Agent SDK wrapper captures message and tool events, then reruns a selected turn in a live session branch with a changed tool result if the call recurs. Claude model responses cannot be replaced in this version. The local timeline labels these different guarantees.

Arbitrary agent hosts and OpenTelemetry export remain future directions. SDK integration does not import existing conversations or rewind external services.

## Privacy and success criteria

Full request, response, and state capture is opt-in because it may retain credentials or personal data. Redaction and size limits can make a run unreplayable; inspection must show that limitation. Replay must never restore files over a developer's working files or execute an external write without an explicit mock response.

The product succeeds when a developer can inspect a seeded failure, replay the same captured path, substitute one response, locate the first divergence, and understand whether the final answer changed or replay stopped. The [README](../README.md) contains the commands and the prototype's operational limits.
