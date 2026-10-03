"""Evaluation report: summary JSON, per-task CSV, two charts and a markdown write-up."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from .run import summarize  # noqa: E402

SURFACE, INK, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
RAMP = ["#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]  # ordinal: metadata -> special
GOVERNED, RAW = "#2a78d6", "#eb6834"
LABEL = "Synthetic users (generated data, not real people)"
PURPOSE_NAMES = {"storage_cleanup": "Free up storage", "photo_sharing": "Share photos",
                 "backup_support": "Explain a backup", "scheduling": "Schedule a call"}


def _axes(fig, ax, title, subtitle):
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    fig.text(0.02, 0.95, title, fontsize=13, fontweight="bold", color=INK, va="top")
    fig.text(0.02, 0.885, subtitle, fontsize=9.5, color=MUTED, va="top")
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, length=0)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def exposure_chart(df: pd.DataFrame, path: Path) -> None:
    """Mean fields an agent receives per everyday task, split by sensitivity."""
    d = df[~df.injected]
    rows = []
    for mode in ("raw", "governed"):
        m = d[d["mode"] == mode]
        sens_only = (m.sensitive_released - m.special_released).mean()
        rows.append((mode, [m.metadata_released.mean(), m.personal_released.mean(), sens_only, m.special_released.mean()]))
    fig, ax = plt.subplots(figsize=(9, 3.4))
    fig.subplots_adjust(left=0.23, right=0.9, top=0.62, bottom=0.17)
    _axes(fig, ax, "What the agent sees for one task",
          f"Mean fields per everyday task, by sensitivity. {LABEL}.")
    names = ["Metadata", "Personal", "Sensitive", "Health (special category)"]
    ylabels = {"raw": "Reading the data directly", "governed": "Through need-to-know"}
    for y, (mode, vals) in enumerate(rows):
        left = 0.0
        for k, v in enumerate(vals):
            if v > 0:
                ax.barh(y, v, left=left, color=RAMP[k], height=0.55, edgecolor=SURFACE, linewidth=2)
            left += v
        ax.text(left + 60, y, f"{left:,.0f} fields", va="center", fontsize=10, color=INK)
    ax.set_yticks([0, 1], [ylabels[r[0]] for r in rows], color=INK, fontsize=10)
    ax.invert_yaxis()
    ax.set_xlim(0, max(sum(r[1]) for r in rows) * 1.18)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in RAMP]
    ax.legend(handles, names, ncol=4, loc="lower left", bbox_to_anchor=(-0.28, 1.02), frameon=False, fontsize=9,
              labelcolor=MUTED, handlelength=1.1, columnspacing=1.4)
    fig.savefig(path, dpi=160, facecolor=SURFACE)
    plt.close(fig)


def precision_chart(df: pd.DataFrame, path: Path) -> None:
    """Share of released fields the task actually needed, per task type."""
    d = df[~df.injected]
    purposes = [p for p in PURPOSE_NAMES if p in set(d.purpose)]
    fig, ax = plt.subplots(figsize=(9, 3.8))
    fig.subplots_adjust(left=0.2, right=0.93, top=0.7, bottom=0.14)
    _axes(fig, ax, "How much of the context the task needed",
          f"Context precision: needed fields / fields released, mean per task. {LABEL}.")
    h = 0.36
    for k, p in enumerate(purposes):
        for j, (mode, color) in enumerate((("governed", GOVERNED), ("raw", RAW))):
            v = d[(d.purpose == p) & (d["mode"] == mode)].precision.mean()
            y = k + (j - 0.5) * h
            ax.barh(y, v, height=h - 0.04, color=color)
            ax.text(v + 0.01, y, "<1%" if 0 < v < 0.005 else f"{v:.0%}", va="center", fontsize=9, color=INK)
    ax.set_yticks(range(len(purposes)), [PURPOSE_NAMES[p] for p in purposes], color=INK, fontsize=10)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.08)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in (GOVERNED, RAW)]
    ax.legend(handles, ["Through need-to-know", "Reading the data directly"], ncol=2, loc="lower left",
              bbox_to_anchor=(-0.24, 1.02), frameon=False, fontsize=9, labelcolor=MUTED, handlelength=1.1)
    fig.savefig(path, dpi=160, facecolor=SURFACE)
    plt.close(fig)


def write(df: pd.DataFrame, out: str | Path, quality: dict | None = None) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    summary = summarize(df)
    df.to_csv(out / "results.csv", index=False)
    (out / "summary.json").write_text(json.dumps({"summary": summary, "data_quality": quality}, indent=2, default=str))
    exposure_chart(df, out / "exposure.png")
    precision_chart(df, out / "precision.png")

    e, i = summary["everyday"], summary.get("injection", {})
    g, r = e["governed"], e["raw"]
    lines = [
        "# Evaluation report", "",
        f"_{LABEL}. Each task runs twice with the same agent: once reading the data directly, "
        "once through need-to-know._", "",
        "## Everyday tasks", "",
        "| | Reading the data directly | Through need-to-know |", "|---|---:|---:|",
        f"| Tasks | {r['tasks']} | {g['tasks']} |",
        f"| Task success | {r['success_rate']:.1%} | {g['success_rate']:.1%} |",
        f"| Tasks with a wrong action | {r['tasks_with_wrong_actions']} | {g['tasks_with_wrong_actions']} |",
        f"| Times the agent asked the user | {r['asked_user']} | {g['asked_user']} |",
        f"| Fields released per task (mean) | {r['mean_fields_released']:,.0f} | {g['mean_fields_released']:,.0f} |",
        f"| Context precision | {r['context_precision']:.1%} | {g['context_precision']:.1%} |",
        f"| Context recall | {r['context_recall']:.1%} | {g['context_recall']:.1%} |",
        f"| Sensitive fields released without consent | {r['sensitive_fields_released_without_consent']:,} | {g['sensitive_fields_released_without_consent']:,} |",
        f"| Health fields released | {r['special_category_fields_released']:,} | {g['special_category_fields_released']:,} |",
        f"| Photo shares that sent precise location without consent | {r['shares_with_location_without_consent']} | {g['shares_with_location_without_consent']} |",
        f"| Median time to build context | {r['median_context_ms']} ms | {g['median_context_ms']} ms |",
        "", "![Exposure](exposure.png)", "", "![Precision](precision.png)", "",
    ]
    if i:
        gi, ri = i["governed"], i["raw"]
        lines += [
            "## Planted instructions (prompt injection)", "",
            "Tasks that touch a record whose caption or note contains planted instructions. The agent here "
            "obeys any instruction it reads (worst case).", "",
            "| | Reading the data directly | Through need-to-know |", "|---|---:|---:|",
            f"| Tasks | {ri['tasks']} | {gi['tasks']} |",
            f"| Injected text reached the agent | {ri['injection_reached_agent']} | {gi['injection_reached_agent']} |",
            f"| Injected actions executed | {ri['injected_actions_executed']} | {gi['injected_actions_executed']} |",
            f"| Task success | {ri['success_rate']:.0%} | {gi['success_rate']:.0%} |", "",
        ]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n")
    return summary
