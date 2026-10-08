from __future__ import annotations

from collections import Counter
from typing import Literal


CaseTag = Literal[
    "different_units",
    "different_currencies",
    "percentage_changes",
    "percentage_points",
    "different_years",
    "quarter_vs_annual",
    "charts",
    "tables",
    "ocr",
    "footnotes",
    "compound_claims",
    "missing_evidence",
    "rounded_values",
    "zero_baseline",
    "negative_values",
]

ChallengeMode = Literal[
    "straightforward",
    "isolated",
    "combined",
]

CASE_TAGS = (
    "different_units",
    "different_currencies",
    "percentage_changes",
    "percentage_points",
    "different_years",
    "quarter_vs_annual",
    "charts",
    "tables",
    "ocr",
    "footnotes",
    "compound_claims",
    "missing_evidence",
    "rounded_values",
    "zero_baseline",
    "negative_values",
)

MODE_TARGETS = {
    "straightforward": 50,
    "isolated": 35,
    "combined": 15,
}

VERDICT_TARGETS = {
    "SUPPORTED": 25,
    "CONTRADICTED": 25,
    "PARTIALLY_SUPPORTED": 25,
    "UNKNOWN": 25,
}


def coverage_counts(
    run: dict,
    statuses: tuple[str, ...] = ("approved",),
) -> dict:
    modes = Counter()
    tags = Counter()
    verdicts = Counter()
    unclassified = 0

    for record in run["records"]:
        if record["status"] not in statuses:
            continue

        candidate = record["candidate"]

        verdicts[candidate["verdict"]] += 1

        mode = candidate.get("challenge_mode")

        if mode in MODE_TARGETS:
            modes[mode] += 1
        else:
            unclassified += 1

        for tag in set(candidate.get("case_tags", [])):
            if tag in CASE_TAGS:
                tags[tag] += 1

    return {
        "by_mode": {
            mode: modes[mode]
            for mode in MODE_TARGETS
        },
        "by_tag": {
            tag: tags[tag]
            for tag in CASE_TAGS
        },
        "by_verdict": {
            verdict: verdicts[verdict]
            for verdict in VERDICT_TARGETS
        },
        "straightforward": modes["straightforward"],
        "difficult": modes["isolated"] + modes["combined"],
        "unclassified": unclassified,
    }


def batch_requests(
    run: dict,
    limit: int = 10,
) -> list[dict]:
    active_records = [
        record
        for record in run["records"]
        if record["status"] in {"draft", "approved"}
    ]

    for record in active_records:
        candidate = record["candidate"]

        if (
            candidate.get("challenge_mode") not in MODE_TARGETS
            or "case_tags" not in candidate
            or "challenge_tags" not in candidate
        ):
            raise ValueError(
                "This dataset contains older drafts without "
                "coverage fields. Add challenge_mode, case_tags "
                "and challenge_tags during review, or build a "
                "new dataset."
            )

    counts = coverage_counts(
        run,
        statuses=("draft", "approved"),
    )

    for verdict, target in VERDICT_TARGETS.items():
        if counts["by_verdict"][verdict] > target:
            raise ValueError(
                f"Too many active {verdict} examples. "
                "Reject excess examples before generating more. "
                "Do not change a correct label just to fill a quota."
            )

    for mode, target in MODE_TARGETS.items():
        if counts["by_mode"][mode] > target:
            raise ValueError(
                f"Too many active {mode} examples. "
                "Reject excess examples or correct their "
                "challenge classification before generating more."
            )

    verdict_remaining = {
        verdict: target - counts["by_verdict"][verdict]
        for verdict, target in VERDICT_TARGETS.items()
    }

    mode_remaining = {
        mode: target - counts["by_mode"][mode]
        for mode, target in MODE_TARGETS.items()
    }

    requests = Counter()

    for _ in range(limit):
        if (
            not any(verdict_remaining.values())
            or not any(mode_remaining.values())
        ):
            break

        verdict = max(
            verdict_remaining,
            key=lambda value: verdict_remaining[value],
        )

        mode = max(
            mode_remaining,
            key=lambda value: (
                mode_remaining[value] / MODE_TARGETS[value]
            ),
        )

        requests[(verdict, mode)] += 1

        verdict_remaining[verdict] -= 1
        mode_remaining[mode] -= 1

    return [
        {
            "verdict": verdict,
            "challenge_mode": mode,
            "count": count,
        }
        for (verdict, mode), count in requests.items()
    ]


def validate_case_fields(
    candidate: dict,
    cited_types: set[str],
) -> None:
    mode = candidate["challenge_mode"]
    case_tags = set(candidate["case_tags"])
    challenge_tags = set(candidate["challenge_tags"])

    if len(case_tags) != len(candidate["case_tags"]):
        raise ValueError("case_tags contains duplicates.")

    if len(challenge_tags) != len(candidate["challenge_tags"]):
        raise ValueError("challenge_tags contains duplicates.")

    if not challenge_tags.issubset(case_tags):
        raise ValueError(
            "Every challenge_tag must also appear in case_tags."
        )

    if mode == "straightforward" and challenge_tags:
        raise ValueError(
            "Straightforward claims must have empty challenge_tags."
        )

    if mode == "isolated" and len(challenge_tags) != 1:
        raise ValueError(
            "An isolated difficult claim must target exactly "
            "one challenge."
        )

    if mode == "combined" and len(challenge_tags) < 2:
        raise ValueError(
            "A combined difficult claim must target at least "
            "two distinct challenges."
        )

    modality_requirements = {
        "charts": "chart",
        "tables": "table",
        "ocr": "ocr",
        "footnotes": "footnote",
    }

    for tag, evidence_type in modality_requirements.items():
        if tag in case_tags and evidence_type not in cited_types:
            raise ValueError(
                f"The {tag} tag requires a citation of type "
                f"{evidence_type}."
            )

    subclaims = candidate["subclaims"]

    if (
        "compound_claims" in case_tags
        and len(subclaims) < 2
    ):
        raise ValueError(
            "compound_claims requires at least two subclaims."
        )

    if (
        "missing_evidence" in case_tags
        and not any(
            item["verdict"] == "UNKNOWN"
            for item in subclaims
        )
    ):
        raise ValueError(
            "missing_evidence requires an unresolved subclaim."
        )


def export_coverage_ready(run: dict) -> bool:
    counts = coverage_counts(run)

    return (
        counts["unclassified"] == 0
        and all(
            counts["by_verdict"][verdict] == target
            for verdict, target in VERDICT_TARGETS.items()
        )
        and all(
            counts["by_mode"][mode] == target
            for mode, target in MODE_TARGETS.items()
        )
    )


def validate_export_coverage(run: dict) -> None:
    if export_coverage_ready(run):
        return

    counts = coverage_counts(run)

    raise ValueError(
        "Export requires 25 approved claims per verdict, "
        "50 straightforward claims, 35 isolated difficult "
        "claims and 15 combined difficult claims. "
        f"Current coverage: {counts['by_mode']}; "
        f"verdicts: {counts['by_verdict']}; "
        f"unclassified: {counts['unclassified']}."
    )