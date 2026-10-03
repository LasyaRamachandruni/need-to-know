"""The `ntk` command.

    ntk generate --users 100        # synthetic users -> data/events.jsonl + data/truth.json
    ntk build                       # events -> facts with provenance (data/warehouse.duckdb)
    ntk quality                     # data-quality report
    ntk context <user> <purpose> --scope '{"album": "...", "recipient": "mom"}'
    ntk eval                        # run every task raw vs governed -> reports/
    ntk serve                       # context + action-review API
    ntk demo                        # generate + build + quality + eval
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path


def _paths(args):
    d = Path(args.data_dir)
    return d / "events.jsonl", d / "truth.json", d / "warehouse.duckdb"


def cmd_generate(args) -> int:
    from .data.synth import generate
    from .eval.run import save_truth

    events, truth, _ = _paths(args)
    ds = generate(users=args.users, seed=args.seed, injection_rate=args.injection_rate)
    events.parent.mkdir(parents=True, exist_ok=True)
    with events.open("w") as f:
        for e in ds.events:
            f.write(json.dumps(e) + "\n")
    save_truth(ds.truth, truth)
    print(f"{args.users} synthetic users, {len(ds.events):,} events "
          f"({ds.duplicates} redelivered, {ds.malformed} malformed) -> {events}")
    return 0


def cmd_build(args) -> int:
    from .data.pipeline import build

    events, _, wh = _paths(args)
    stream = [json.loads(line) for line in events.open()]
    stats = build(stream, wh)
    print(json.dumps(asdict(stats), indent=2))
    return 0


def cmd_quality(args) -> int:
    from .data.pipeline import quality_report

    report = quality_report(_paths(args)[2])
    report["skewed_sources"] = report["skewed_sources"][:5]
    print(json.dumps(report, indent=2, default=str))
    return 0


def cmd_context(args) -> int:
    from .context import ContextService

    svc = ContextService(_paths(args)[2])
    b = svc.get(args.user, args.purpose, json.loads(args.scope), mode=args.mode)
    out = b.to_json()
    if not args.full:
        out["items"] = out["items"][:5]
        out["items_shown"] = f"5 of {len(b.items)} (use --full for all)"
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_eval(args) -> int:
    from .data.pipeline import quality_report
    from .eval.report import write
    from .eval.run import load_truth, run

    _, truth_path, wh = _paths(args)
    truth = load_truth(truth_path)
    if getattr(args, "max_users", None):
        truth = dict(list(truth.items())[: args.max_users])
    agents = None
    if getattr(args, "agent", "reference") == "llm":
        from .agents.llm import LLMAgent

        llm = LLMAgent()
        agents = {"everyday": llm, "injected": llm}
    df = run(wh, truth, agents)
    summary = write(df, args.out, quality_report(wh))
    for kind, modes in summary.items():
        for mode, s in modes.items():
            print(f"{kind:9s} {mode:8s} tasks={s['tasks']:4d} success={s['success_rate']:.1%} "
                  f"wrong={s['tasks_with_wrong_actions']:3d} fields/task={s['mean_fields_released']:8,.0f} "
                  f"precision={s['context_precision']:.1%} injected_executed={s['injected_actions_executed']}")
    print(f"report: {Path(args.out) / 'REPORT.md'}")
    return 0


def cmd_serve(args) -> int:
    import os

    import uvicorn

    os.environ.setdefault("NTK_WAREHOUSE", str(_paths(args)[2]))
    uvicorn.run("needtoknow.api:app", host=args.host, port=args.port)
    return 0


def cmd_demo(args) -> int:
    for step in (cmd_generate, cmd_build, cmd_quality, cmd_eval):
        if step(args) != 0:
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ntk", description="need-to-know: privacy-aware context for agents")
    p.add_argument("--data-dir", default="data")
    sub = p.add_subparsers(dest="command", required=True)

    def gen_args(s):
        s.add_argument("--users", type=int, default=100)
        s.add_argument("--seed", type=int, default=0)
        s.add_argument("--injection-rate", type=float, default=0.15)

    def eval_args(s):
        s.add_argument("--out", default="reports")
        s.add_argument("--agent", choices=["reference", "llm"], default="reference",
                       help="reference: deterministic (worst-case injected agent for injection tasks); llm: needs ANTHROPIC_API_KEY")
        s.add_argument("--max-users", type=int, help="evaluate only the first N users")

    g = sub.add_parser("generate", help="synthetic users and their sync event stream")
    gen_args(g)
    g.set_defaults(fn=cmd_generate)
    sub.add_parser("build", help="rebuild state from events").set_defaults(fn=cmd_build)
    sub.add_parser("quality", help="data-quality report").set_defaults(fn=cmd_quality)

    c = sub.add_parser("context", help="print a context bundle")
    c.add_argument("user")
    c.add_argument("purpose")
    c.add_argument("--scope", default="{}")
    c.add_argument("--mode", choices=["governed", "raw"], default="governed")
    c.add_argument("--full", action="store_true")
    c.set_defaults(fn=cmd_context)

    e = sub.add_parser("eval", help="run all tasks raw vs governed and write a report")
    eval_args(e)
    e.set_defaults(fn=cmd_eval)

    s = sub.add_parser("serve", help="run the API")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)

    d = sub.add_parser("demo", help="generate + build + quality + eval")
    gen_args(d)
    eval_args(d)
    d.set_defaults(fn=cmd_demo)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
