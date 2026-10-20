"""The seeded world: documents, a metrics table, and the task suite.

Everything the demo and the experiments measure happens against this
fixed corpus. Documents are short operational memos about a fictional
product launch; facts the verifiers check live in specific chunks.
Some sub-questions are *direct* (the fact chunk is the top keyword hit)
and some are *indirect* (a keyword-heavy summary chunk outranks the
chunk that actually carries the fact, which sits at rank 2) — that
asymmetry is what makes retrieval depth (provider tier) measurable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .tools import Chunk

CHUNKS = [
    # --- Helios launch: summary chunk (keyword-heavy, no date) outranks
    # --- the detail chunk that carries the actual ship date.
    Chunk("helios-plan", "Helios launch plan",
          "Helios launch date summary: this note covers the launch plan, the "
          "ship readiness review, and the launch date announcement process "
          "for Helios. It summarises dates owned by the detail memo."),
    Chunk("helios-plan", "Helios launch plan",
          "Helios will ship to customers on the date of 14 March 2027, "
          "following the February code freeze."),
    Chunk("helios-budget", "Helios budget memo",
          "The Helios launch budget is 2.4 million dollars, of which 0.9 "
          "million is reserved for partner marketing."),
    Chunk("q3-memo", "Q3 business memo",
          "In Q3 the returns rate fell to 3.1 percent while churn held at "
          "1.8 percent; support tickets rose 12 percent after the billing "
          "migration."),
    Chunk("infra-postmortem", "Payments incident postmortem",
          "The payments outage on 9 January was caused by a misconfigured "
          "cache TTL that pinned stale exchange rates for six hours."),
    Chunk("vendor-contract", "Vendor contract summary",
          "The Northwind vendor contract grants a discount of 17.5 percent "
          "on all platform fees, renewing in November 2027."),
    Chunk("hiring-plan", "Hiring plan",
          "The 2027 hiring plan approves 12 engineers for the platform "
          "team and 4 designers for the growth team."),
    Chunk("security-review", "Security review",
          "The annual penetration test found two medium findings, both in "
          "the legacy reporting service; remediation is due by June 2027."),
]

METRICS = [
    # region, month, revenue, units
    ("North", "2027-01", 18200.50, 310), ("North", "2027-02", 21450.25, 355),
    ("North", "2027-03", 24900.75, 402),
    ("South", "2027-01", 12100.00, 205), ("South", "2027-02", 13320.40, 226),
    ("South", "2027-03", 15980.60, 271),
    ("East", "2027-01", 22150.90, 377), ("East", "2027-02", 23980.10, 409),
    ("East", "2027-03", 26420.00, 448),
    ("West", "2027-01", 9800.30, 164), ("West", "2027-02", 10450.60, 178),
    ("West", "2027-03", 11750.85, 199),
]

METRIC_ROWS = [{"region": r, "month": m, "revenue": rev, "units": u}
               for r, m, rev, u in METRICS]

# Q1 totals (verified against DataLookupTool): North revenue 64551.50, all-region revenue
# 210505.25, all-region units 3544. Net of the 17.5% vendor discount:
# 210505.25 * 0.825 = 173666.83.


@dataclass
class TaskSpec:
    id: str
    kind: str                       # brief | analysis | quick | deepdive | publish
    goal: str
    sub_questions: list[str] = field(default_factory=list)
    lookups: list[dict] = field(default_factory=list)
    computations: list[dict] = field(default_factory=list)
    publish: bool = False
    expected_facts: list[str] = field(default_factory=list)
    expected_values: list[str] = field(default_factory=list)  # "label = 123.45"


def _q1_tasks() -> list[TaskSpec]:
    return [
        TaskSpec(
            id="brief-launch", kind="brief",
            goal="Write a launch brief for Project Helios.",
            sub_questions=["helios launch date ship readiness",
                           "Helios launch budget amount",
                           "vendor contract discount percent"],
            expected_facts=["14 March 2027", "2.4 million", "17.5 percent"]),
        TaskSpec(
            id="brief-ops", kind="brief",
            goal="Write an operations brief.",
            sub_questions=["payments outage cause cache",
                           "hiring plan engineers platform team",
                           "penetration test findings reporting service"],
            expected_facts=["misconfigured cache TTL", "12 engineers",
                            "two medium findings"]),
        TaskSpec(
            id="analysis-net-revenue", kind="analysis",
            goal="Compute Q1 net platform revenue after the vendor discount.",
            lookups=[{"metric": "revenue", "label": "gross_revenue"}],
            computations=[{"label": "net_revenue",
                           "template": "{gross_revenue} * (1 - 0.175)",
                           "inputs": {"gross_revenue": "lookup_0"}}],
            expected_values=["net_revenue = 173666.83"]),
        TaskSpec(
            id="analysis-north", kind="analysis",
            goal="Compute North region Q1 revenue and its March share.",
            lookups=[{"metric": "revenue", "region": "North", "label": "north_q1"},
                     {"metric": "revenue", "region": "North", "month": "2027-03",
                      "label": "north_mar"}],
            computations=[{"label": "north_share",
                           "template": "{north_mar} / {north_q1} * 100",
                           "inputs": {"north_mar": "lookup_1", "north_q1": "lookup_0"}}],
            expected_values=["north_share = 38.58"]),
        TaskSpec(
            id="quick-units", kind="quick",
            goal="How many units were sold in Q1 across all regions?",
            lookups=[{"metric": "units", "label": "q1_units"}],
            expected_values=["q1_units = 3544.00"]),
        TaskSpec(
            id="quick-north-revenue", kind="quick",
            goal="What was North region revenue in Q1?",
            lookups=[{"metric": "revenue", "region": "North", "label": "north_q1"}],
            expected_values=["north_q1 = 64551.50"]),
        TaskSpec(
            id="deepdive-launch", kind="deepdive",
            goal="Write a deep-dive on the Helios launch readiness.",
            sub_questions=["helios launch date ship readiness",
                           "Helios launch budget amount",
                           "vendor contract discount percent",
                           "hiring plan engineers platform team",
                           "penetration test findings reporting service"],
            lookups=[{"metric": "revenue", "label": "gross_revenue"}],
            computations=[{"label": "net_revenue",
                           "template": "{gross_revenue} * (1 - 0.175)",
                           "inputs": {"gross_revenue": "lookup_0"}}],
            expected_facts=["14 March 2027", "2.4 million", "17.5 percent",
                            "12 engineers", "two medium findings"],
            expected_values=["net_revenue = 173666.83"]),
        TaskSpec(
            id="publish-brief", kind="publish",
            goal="Write and publish the Q3 operations brief.",
            sub_questions=["payments outage cause cache",
                           "Q3 returns rate churn percent"],
            publish=True,
            expected_facts=["misconfigured cache TTL", "3.1 percent"]),
    ]


def task_suite() -> list[TaskSpec]:
    return _q1_tasks()


def runaway_task(fanout: int = 30) -> TaskSpec:
    """A research task whose fan-out far exceeds any sane budget."""
    base = ["helios launch date ship readiness",
            "Helios launch budget amount",
            "vendor contract discount percent",
            "payments outage cause cache",
            "hiring plan engineers platform team",
            "penetration test findings reporting service",
            "Q3 returns rate churn percent"]
    questions = [base[i % len(base)] for i in range(fanout)]
    return TaskSpec(id="runaway-research", kind="brief",
                    goal="Research everything about the launch, exhaustively.",
                    sub_questions=questions,
                    expected_facts=["14 March 2027"])


def check_task(task: TaskSpec, artifact: str) -> tuple[bool, list[str]]:
    """Deterministic verifier: every expected fact/value in the artifact."""
    missing = [f for f in task.expected_facts if f not in artifact]
    missing += [v for v in task.expected_values if v not in artifact]
    return (not missing), missing
