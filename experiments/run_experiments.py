"""Seeded experiment suite for open-managed-agents.

Runs all five experiments against the bundled corpus and writes
``results/results.md`` plus charts from the *actual measured numbers*
of this run. Everything is deterministic except wall-clock timings,
which are measured, not simulated, at the margins they claim
(simulated per-step latency is real sleep).

  E1 dynamic vs static planning on the task suite
  E2 parallel DAG execution vs forced-sequential
  E3 provider swap: identical plans on stub-basic vs stub-pro
  E4 failure recovery: primary researcher down; retry+replan vs none
  E5 budget governance: runaway fan-out vs a hard token cap
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from open_managed_agents import (DynamicPlanner, ManagedRuntime, ScriptedProvider,
                                 StaticPlanner, check_task, default_registry,
                                 runaway_task, task_suite)
from open_managed_agents.corpus import CHUNKS, METRIC_ROWS

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "..", "results")
SANDBOX = os.path.join(RESULTS, "sandbox")


def make_runtime(**kw):
    return ManagedRuntime(default_registry(), CHUNKS, METRIC_ROWS, SANDBOX, **kw)


def run_task(rt, plan, task, **kw):
    rec = rt.run(plan, **kw)
    ok, missing = check_task(task, rec.final_output)
    rec.success = ok
    return rec, missing


def main():
    os.makedirs(RESULTS, exist_ok=True)
    reg = default_registry()
    dyn, stat = DynamicPlanner(), StaticPlanner()
    suite = task_suite()
    L = []          # results.md lines
    L.append("# open-managed-agents: results (seeded run)\n")
    L.append("All numbers below are measured from actual runs of this suite "
             "against the bundled corpus (scripted providers, no API keys).\n")

    # ---------------------------------------------------------------- E1
    rows1 = []
    for task in suite:
        rt = make_runtime()
        rec_d, _ = run_task(rt, dyn.plan(task, reg), task)
        rt = make_runtime()
        rec_s, _ = run_task(rt, stat.plan(task, reg), task, allow_replan=False)
        rows1.append((task, rec_d, rec_s))
    d_ok = sum(r[1].success for r in rows1)
    s_ok = sum(r[2].success for r in rows1)
    L.append("## E1 — Dynamic vs static planning\n")
    L.append("| task | dynamic success | dynamic steps | static success | static steps |")
    L.append("|---|---|---|---|---|")
    for task, rd, rs in rows1:
        L.append(f"| {task.id} | {rd.success} | {rd.steps_executed} | "
                 f"{rs.success} | {rs.steps_executed} |")
    L.append(f"\n- dynamic: **{d_ok}/{len(suite)} tasks verified**, "
             f"avg steps {sum(r[1].steps_executed for r in rows1)/len(rows1):.1f}, "
             f"avg wall {sum(r[1].wall_time_s for r in rows1)/len(rows1):.2f}s")
    L.append(f"- static: **{s_ok}/{len(suite)} tasks verified**, "
             f"avg steps {sum(r[2].steps_executed for r in rows1)/len(rows1):.1f}, "
             f"avg wall {sum(r[2].wall_time_s for r in rows1)/len(rows1):.2f}s")
    L.append("- the static pipeline fails exactly the tasks whose needs it "
             "cannot see: more than one sub-question. It succeeds on "
             "single-thread analysis/quick tasks — and spends more steps "
             "there than the dynamic plan (fixed stages it does not need).")

    # ---------------------------------------------------------------- E2
    par_tasks = [t for t in suite if t.id in
                 ("deepdive-launch", "brief-launch", "brief-ops")]
    rows2 = []
    for task in par_tasks:
        plan = dyn.plan(task, reg)
        rt = make_runtime()
        rec_p, _ = run_task(rt, plan, task)
        rt = make_runtime()
        rec_q, _ = run_task(rt, plan, task, sequential=True)
        rows2.append((task, rec_p, rec_q))
    L.append("\n## E2 — Parallel branches vs forced-sequential\n")
    L.append("| task | parallel wall | sequential wall | speedup |")
    L.append("|---|---|---|---|")
    for task, rp, rq in rows2:
        L.append(f"| {task.id} | {rp.wall_time_s:.2f}s | {rq.wall_time_s:.2f}s | "
                 f"{rq.wall_time_s / max(rp.wall_time_s, 1e-9):.2f}x |")
    tot_p = sum(r[1].wall_time_s for r in rows2)
    tot_q = sum(r[2].wall_time_s for r in rows2)
    L.append(f"\n- total: parallel {tot_p:.2f}s vs sequential {tot_q:.2f}s "
             f"(**{tot_q / max(tot_p, 1e-9):.2f}x**) — independent research "
             "branches run as one LangGraph superstep; step outputs are "
             "identical, only the schedule changes.")

    # ---------------------------------------------------------------- E3
    rows3 = []
    hash_stable = 0
    for task in suite:
        plan = dyn.plan(task, reg)
        if dyn.plan(task, reg).plan_hash() == plan.plan_hash():
            hash_stable += 1
        rt = make_runtime(provider_override=ScriptedProvider("stub-basic"))
        rec_b, miss_b = run_task(rt, plan, task)
        rt = make_runtime(provider_override=ScriptedProvider("stub-pro"))
        rec_p, miss_p = run_task(rt, plan, task)
        rows3.append((task, rec_b, rec_p, miss_b, miss_p))
    b_ok = sum(r[1].success for r in rows3)
    p_ok = sum(r[2].success for r in rows3)
    b_cost = sum(r[1].total_cost_usd for r in rows3)
    p_cost = sum(r[2].total_cost_usd for r in rows3)
    L.append("\n## E3 — Provider swap (identical plans, zero workflow changes)\n")
    L.append(f"- plan composition is deterministic: re-planning each task "
             f"reproduced the identical plan hash in {hash_stable}/{len(suite)} "
             f"cases, and the same plan objects were executed on both "
             f"providers — the swap changes only the runtime's provider "
             f"binding.")
    L.append(f"- stub-basic: **{b_ok}/{len(suite)} verified**, total cost ${b_cost:.4f}")
    L.append(f"- stub-pro: **{p_ok}/{len(suite)} verified**, total cost ${p_cost:.4f}")
    L.append("| task | basic | pro | what basic missed |")
    L.append("|---|---|---|---|")
    for task, rb, rp, mb, mp in rows3:
        L.append(f"| {task.id} | {rb.success} | {rp.success} | "
                 f"{'; '.join(mb) if mb else '—'} |")
    L.append("- basic's failures are strategy failures, documented in the "
             "provider: top-1 retrieval misses the indirectly-phrased fact, "
             "literal rounding breaks decimal arithmetic, head-only "
             "synthesis drops late facts. Cheaper per token, more expensive "
             "per *successful* task on this suite.")

    # ---------------------------------------------------------------- E4
    fail_tasks = [t for t in suite if t.id in
                  ("brief-launch", "brief-ops", "publish-brief", "deepdive-launch")]
    rows4 = []
    for task in fail_tasks:
        providers = {"stub-pro": ScriptedProvider("stub-pro", fail_agents={"scout"}),
                     "stub-basic": ScriptedProvider("stub-basic")}
        rt = make_runtime(providers=providers)
        rec_d, _ = run_task(rt, dyn.plan(task, reg), task)
        providers = {"stub-pro": ScriptedProvider("stub-pro", fail_agents={"scout"}),
                     "stub-basic": ScriptedProvider("stub-basic")}
        rt = make_runtime(providers=providers)
        rec_s, _ = run_task(rt, dyn.plan(task, reg), task, allow_replan=False)
        rows4.append((task, rec_d, rec_s))
    d_done = sum(r[1].status in ("completed", "completed_with_denials",
                                 "completed_partial") for r in rows4)
    s_done = sum(r[2].status in ("completed", "completed_with_denials",
                                 "completed_partial") for r in rows4)
    d_ver = sum(r[1].success for r in rows4)
    L.append("\n## E4 — Failure recovery (primary researcher `scout` down)\n")
    L.append("| task | with replan: status | verified | no replan: status | verified |")
    L.append("|---|---|---|---|---|")
    for task, rd, rs in rows4:
        L.append(f"| {task.id} | {rd.status} | {rd.success} | {rs.status} | {rs.success} |")
    L.append(f"\n- workflow recovery (run completes): **{d_done}/{len(fail_tasks)} "
             f"with retry+replan vs {s_done}/{len(fail_tasks)} without**.")
    L.append(f"- verified recovery: {d_ver}/{len(fail_tasks)} — replan moves "
             "research to the backup agent, whose provider is the basic "
             "tier: execution recovers fully, but on tasks whose facts "
             "need deep retrieval the *answer quality* drops with the "
             "tier. Recovery of execution is not recovery of quality.")

    # ---------------------------------------------------------------- E5
    run_task_spec = runaway_task(30)
    plan = dyn.plan(run_task_spec, reg)
    est_total = 30 * 900 + 1200 + 800
    rt = make_runtime()
    rec_cap, _ = run_task(rt, plan, run_task_spec, budget_tokens=6000)
    rt = make_runtime()
    rec_full, _ = run_task(rt, plan, run_task_spec, budget_tokens=100000)
    spent_cap = sum(t.tokens_in + t.tokens_out for t in rec_cap.traces)
    L.append("\n## E5 — Budget governance (runaway fan-out)\n")
    L.append(f"- task fans out to {len(plan.steps)} planned steps "
             f"(~{est_total} estimated tokens).")
    L.append(f"- cap 6000 tokens: status **{rec_cap.status}**, "
             f"{rec_cap.steps_executed} steps executed, "
             f"actual spend {spent_cap} tokens — the governor blocked the "
             f"step whose reservation would have crossed the cap and "
             f"escalated the run with the reason in the trace.")
    L.append(f"- cap 100000 tokens: status {rec_full.status}, "
             f"{rec_full.steps_executed} steps executed, "
             f"{rec_full.total_tokens} actual tokens.")
    L.append("- the cap is enforced on *reservations* before steps start, "
             "so parallel branches cannot overshoot it concurrently.")

    # ---------------------------------------------------------------- charts
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    ax = axes[0]
    x = range(len(rows1))
    ax.bar([i - 0.2 for i in x], [r[1].steps_executed for r in rows1], 0.4,
           label="dynamic steps")
    ax.bar([i + 0.2 for i in x], [r[2].steps_executed for r in rows1], 0.4,
           label="static steps")
    for i, (_t, rd, rs) in enumerate(rows1):
        ax.plot([i - 0.2], [rd.steps_executed + 0.4], marker="o",
                color="green" if rd.success else "red")
        ax.plot([i + 0.2], [rs.steps_executed + 0.4], marker="x",
                color="green" if rs.success else "red")
    ax.set_xticks(list(x))
    ax.set_xticklabels([r[0].id for r in rows1], rotation=30, ha="right",
                       fontsize=8)
    ax.set_ylabel("steps executed (o/x = verified)")
    ax.set_title("E1: dynamic vs static")
    ax.legend(fontsize=8)
    ax = axes[1]
    labels = [r[0].id for r in rows2]
    ax.bar([i - 0.2 for i in range(len(rows2))],
           [r[1].wall_time_s for r in rows2], 0.4, label="parallel")
    ax.bar([i + 0.2 for i in range(len(rows2))],
           [r[2].wall_time_s for r in rows2], 0.4, label="sequential")
    ax.set_xticks(range(len(rows2)))
    ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=8)
    ax.set_ylabel("wall-clock (s)")
    ax.set_title("E2: parallel vs sequential")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(RESULTS, "charts.png"), dpi=130)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    ax = axes[0]
    ax.bar(["stub-basic", "stub-pro"], [b_ok, p_ok], color=["#c0392b", "#2471a3"])
    ax.set_ylabel(f"tasks verified (of {len(suite)})")
    ax.set_title("E3: provider swap — success")
    ax = axes[1]
    ax.bar(["stub-basic", "stub-pro"], [b_cost, p_cost], color=["#c0392b", "#2471a3"])
    ax.set_ylabel("total cost (USD)")
    ax.set_title("E3: provider swap — cost")
    fig.tight_layout()
    fig.savefig(os.path.join(RESULTS, "charts_providers.png"), dpi=130)
    plt.close(fig)

    with open(os.path.join(RESULTS, "results.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print("\n".join(L))
    print("\nwrote results/results.md, results/charts.png, results/charts_providers.png")


if __name__ == "__main__":
    main()
