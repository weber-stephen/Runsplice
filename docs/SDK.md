# SDK sessions: setup, forks, and limits

Install the integration you use, then run the interactive example from this checkout. These examples save submitted prompts, observed model messages, and tool results in the private `.agentdbg/live` store. Claude's internal model request and hidden context are not exposed by the SDK; Raw data shows the submitted prompt, prior prompts, system prompt setting, and captured step references. Review captured data before sharing it. Use your own importable `module:function` agent factory in an application; call the same `OpenAISession.run(prompt)` or `ClaudeSession.run(prompt)` instance for every turn.

```sh
python -m pip install '.[openai]'
python -m examples.openai_live
```

OpenAI needs `OPENAI_API_KEY`. Ask an order question that makes `lookup_order` run, then ask a follow-up. The script prints the run ID. To use Claude instead, authenticate the Claude Agent SDK and run:

```sh
python -m pip install '.[claude]'
```

## Claude read and fork check

For a small, disposable Claude check, sign in with `claude auth login` if needed and check `claude auth status`. A `loggedIn: true` result can still precede a 401 expired-token error on the first live call; run `claude auth login` again if that happens. Do not paste tokens or API keys. From this checkout, create a test file, run the example, and enter the prompt below. Press Return at the next empty `You (blank to finish):` prompt so this run has just one turn:

```sh
mkdir -p .agentdbg
printf 'The project codename is ORBIT-17.\n' > .agentdbg/claude-read-test.txt
python -m examples.claude_live
```

```text
Use the Read tool to read .agentdbg/claude-read-test.txt. What project codename does the file state? Answer with the codename only.
```

Copy the printed `RUN_ID`. Confirm that `runs` shows `complete`, then find the `Read` step and inspect its saved response. Its `file.content` should contain `ORBIT-17`; retain the complete JSON shape when editing it. In another terminal, activate the same virtual environment, return to this checkout, open the same store, and select that run and tool step:

```sh
python -m agentdbg.cli --store .agentdbg/live runs
python -m agentdbg.cli --store .agentdbg/live inspect RUN_ID
python -m agentdbg.cli --store .agentdbg/live inspect RUN_ID --step STEP
python -m agentdbg.cli --store .agentdbg/live ui
```

Launch the UI and CLI forks with `python -m agentdbg.cli` from this checkout for these examples.

Select the `Read` step and choose **Try a different response**. Change only `file.content` from `ORBIT-17` to `ORBIT-18`, confirm the live branch, and create the test run. A Claude fork starts a live session branch from the preceding completed turn and proposes the replacement only if Claude makes the same tool call again. Compare the runs: a successful experiment shows a new final answer containing `ORBIT-18`, `live_complete`, and `override applied: True`. Inspect the original run again to confirm it still says `ORBIT-17` and remains `complete`. The UI reports an override as applied only when the SDK's tool-result message confirms it; a rejected or unconfirmed replacement is reported as not confirmed. Claude model responses are view-only.

To probe a nested shape mismatch, start again from the captured response and replace its `file` object with an array such as `["ORBIT-18"]`, keeping the other top-level keys. This passes the current top-level shape check but may be rejected by the live SDK. Use a separate fork and check whether `runs` reports `override_not_applied`, `diff` says `override applied: False`, and `inspect` shows the observed tool result with `origin=live` rather than `origin=overridden`. A missing matching `Read` call can also leave the override unapplied, so inspect the fork steps before concluding that Claude rejected the value. A mocked unit test does not establish live SDK behavior.

An OpenAI fork first reuses matching recorded model and tool calls. If an edited response changes the next request, enable **Allow live continuation** to get a new answer; new model calls cost tokens, and only tools explicitly declared read-only by the app may execute. Both SDK paths leave the original run intact.

## CLI forks

The CLI offers the same experiment with a JSON file containing the replacement value. For Claude, and for OpenAI live continuation, pass `--continue-live`:

```sh
python -m agentdbg.cli --store .agentdbg/live fork RUN_ID --step STEP --response replacement.json --continue-live
python -m agentdbg.cli --store .agentdbg/live runs
python -m agentdbg.cli --store .agentdbg/live inspect FORK_ID
python -m agentdbg.cli --store .agentdbg/live inspect FORK_ID --step STEP
python -m agentdbg.cli --store .agentdbg/live diff RUN_ID FORK_ID
python -m agentdbg.cli --store .agentdbg/live inspect RUN_ID --step STEP
```

`replacement.json` must contain the entire edited JSON response from `inspect` or the UI, not just `file.content`. To clean up, delete `.agentdbg/claude-read-test.txt` and your replacement files. Delete `.agentdbg/live` only if you created it solely for this check; it may contain other captures.

## Support limits

SDK v1 supports new sessions started through these wrappers. OpenAI supports one agent, nonstreaming runs, local function tools, and text prompts. Its factory must be importable when forking; handoffs, MCP, hosted tools, dynamic prompts, guardrails, hooks, approvals, and server-managed conversation IDs are unsupported. Declare only tools whose bodies are safe to run in a live branch as `read_only_tools`. Claude live forks permit only the intersection of the factory's `tools` and `allowed_tools` with built-in read-only tools `Read`, `Glob`, `Grep`, `WebSearch`, and `WebFetch`, excluding `disallowed_tools`; they load no MCP servers, plugins, or settings. The Claude factory must return `ClaudeAgentOptions` without existing hooks. Claude forks are live experiments: SDK session forking copies conversation history, but does not replay hidden model calls or restore filesystem snapshots. A changed workspace can change the outcome. Metadata-only or redacted captures can be viewed but cannot fork.


## Troubleshooting

If a Claude login reports `loggedIn: true` but the first live call returns 401, run `claude auth login` again. If a fork reports `No module named 'examples'`, invoke `python -m agentdbg.cli` from the checkout so the saved `examples.claude_live:make_options` factory can be imported. An override that remains unapplied may mean the selected tool call did not recur or the SDK rejected the edited result; inspect the fork steps before deciding which happened.
