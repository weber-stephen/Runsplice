"""Terminal entry point."""

import argparse
import asyncio
import json
import os
import subprocess
from pathlib import Path
import sys
import webbrowser

from .report import format_diff, format_inspect, format_runs
from .store import RunStore
from .sandbox import EXAMPLE_MODULES, ReplayController
from . import sandbox
from .privacy import environment_secrets


def record_example(root, application, capture_content, redact_fields, agent_command=None):
    if not isinstance(application, str) or application not in EXAMPLE_MODULES:
        raise ValueError("record accepts only the bundled example, delivery, or triage modules")
    store = RunStore(Path(root), frozenset(redact_fields), environment_secrets())
    run_id = store.create_run(application, capture_content)
    env = dict(os.environ, AGENTDBG_RECORD_RUN=run_id, AGENTDBG_RECORD_STORE=str(store.root.resolve()),
               AGENTDBG_CAPTURE_CONTENT="1" if capture_content else "0",
               AGENTDBG_REDACT_FIELDS=json.dumps(list(redact_fields)))
    child = subprocess.run(agent_command if agent_command is not None else [sys.executable, "-m", EXAMPLE_MODULES[application]],
                           env=env, capture_output=True, text=True)
    if child.returncode:
        store.set_status(run_id, "error")
        raise ValueError(f"recording child failed (exit {child.returncode}); inspect the run for details")
    return run_id, child.stdout.strip()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="runsplice", description="Record bundled examples, then inspect or replay their captured runs; SDK apps use Python wrappers.")
    parser.add_argument("--store", default=os.environ.get("AGENTDBG_STORE", ".agentdbg"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("runs")
    commands.add_parser("ui")
    record = commands.add_parser("record", help="Record a bundled example (example, delivery, triage)",
                                 description="Record only agentdbg.example, agentdbg.delivery_example, or agentdbg.triage_example. SDK apps use OpenAISession or ClaudeSession.",
                                 epilog="Example: runsplice record --capture-content -- python -m agentdbg.delivery_example")
    record.add_argument("--capture-content", action="store_true")
    record.add_argument("--redact-field", action="append", default=[])
    record.add_argument("agent_command", nargs=argparse.REMAINDER)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("run_id")
    inspect.add_argument("--step", type=int)
    replay = commands.add_parser("replay", help="Replay a bundled example from a checkpoint",
                                 description="Replay a captured bundled example from a checkpoint. Requires captured content and a Docker CLI connected to a Linux engine; SDK runs use fork.")
    replay.add_argument("run_id")
    replay.add_argument("--from", dest="from_step", type=int, required=True)
    fork = commands.add_parser("fork", help="Replace one saved response and continue the run",
                               description="Fork a bundled example, OpenAI SDK run, or Claude SDK run using a JSON response file. Bundled examples require a Docker CLI connected to a Linux engine; SDK runs require their installed extra and importable saved factory.",
                               epilog="Install SDK extras with python -m pip install 'runsplice[openai]' or 'runsplice[claude]'. Claude forks require --continue-live and a captured tool step.")
    fork.add_argument("run_id")
    fork.add_argument("--step", type=int, required=True)
    fork.add_argument("--response", type=Path, required=True)
    fork.add_argument("--continue-live", action="store_true")
    diff = commands.add_parser("diff")
    diff.add_argument("original_id")
    diff.add_argument("fork_id")
    args = parser.parse_args(argv)
    provider = None
    try:
        if args.command == "record":
            agent_command = args.agent_command[1:] if args.agent_command[:1] == ["--"] else args.agent_command
            module = (agent_command[-1] if len(agent_command) == 3 and
                      agent_command[0] in ("python", "python3", sys.executable) and agent_command[1] == "-m"
                      else agent_command[-1] if len(agent_command) == 4 and agent_command[:3] == ["py", "-3", "-m"]
                      else None)
            application = next((name for name, value in EXAMPLE_MODULES.items() if value == module), None)
            if application is None:
                raise ValueError("record accepts only python -m agentdbg.example, agentdbg.delivery_example, or agentdbg.triage_example; SDK apps use Python wrappers")
            _, output = record_example(args.store, application, args.capture_content, args.redact_field, agent_command)
            print(output)
            return 0
        store = RunStore(Path(args.store))
        if args.command == "ui":
            from .ui import make_server
            server = make_server(store.root)
            url = f"http://127.0.0.1:{server.server_port}/?token={server.token}"
            print(url, flush=True)
            webbrowser.open(url)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
        elif args.command == "runs":
            print(format_runs(store))
        elif args.command == "inspect":
            print(format_inspect(store, args.run_id, args.step))
        elif args.command == "replay":
            if store.get_run(args.run_id)["provider"] != "example":
                raise ValueError("SDK runs use fork; unchanged SDK replay is unavailable")
            fork = ReplayController(store).replay(args.run_id, args.from_step)
            print(f"{fork} {store.get_run(fork)['status']}")
            if store.get_run(fork)["status"] == "blocked" and not sandbox.linux_docker_available():
                print("Replay blocked: install the Docker CLI and start a Linux container engine, then retry.", file=sys.stderr)
        elif args.command == "fork":
            override = json.loads(args.response.read_text())
            provider = store.get_run(args.run_id)["provider"]
            if provider == "example":
                if not isinstance(override, dict):
                    raise ValueError("override file must contain an object")
                fork_id = ReplayController(store).replay(args.run_id, args.step, override)
            elif provider == "openai":
                from .openai_sdk import fork_openai
                fork_id = asyncio.run(fork_openai(store, args.run_id, args.step, override, args.continue_live))
            elif provider == "claude":
                if not args.continue_live:
                    raise ValueError("Claude fork requires --continue-live")
                from .claude_sdk import fork_claude
                fork_id = asyncio.run(fork_claude(store, args.run_id, args.step, override))
            else:
                raise ValueError("unsupported run provider")
            print(f"{fork_id} {store.get_run(fork_id)['status']}")
            if provider == "example" and store.get_run(fork_id)["status"] == "blocked" and not sandbox.linux_docker_available():
                print("Fork blocked: install the Docker CLI and start a Linux container engine, then retry.", file=sys.stderr)
        elif args.command == "diff":
            print(format_diff(store, args.original_id, args.fork_id))
        return 0
    except ModuleNotFoundError as exc:
        extra = "openai" if args.command == "fork" and provider == "openai" else "claude" if args.command == "fork" and provider == "claude" else None
        if extra and exc.name in ({"agents", "pydantic_core"} if extra == "openai" else {"claude_agent_sdk", "pydantic_core"}):
            print(f"{extra} SDK extra is missing; install with python -m pip install 'runsplice[{extra}]'", file=sys.stderr)
        else:
            print(str(exc), file=sys.stderr)
        return 1
    except (KeyError, ValueError, PermissionError, OSError, ImportError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("SDK action failed; inspect the saved run", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
