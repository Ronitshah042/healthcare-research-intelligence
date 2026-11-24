"""Verdict-only exports of spot-check sheets, and judge agreement from them.

The full spot-check sheet holds article text and stays local; the export
keeps only identifiers and verdicts, so it can be committed and traced back
to its source passages.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Collection, Iterable

from .judge import VERDICTS

VERDICT_COLUMNS = (
    "question_id", "claim", "cited_pmcids", "cited_passage_ids",
    "judge_verdict", "your_verdict", "your_notes",
)


def export_verdicts(sheet_csv: str) -> str:
    """Turn a full spot-check sheet into a verdict-only CSV (no article text)."""
    rows = list(csv.DictReader(io.StringIO(sheet_csv)))
    missing = {"question_id", "claim", "cited_passage_ids", "judge_verdict",
               "your_verdict", "your_notes"} - set(rows[0] if rows else {})
    if not rows or missing:
        raise ValueError(
            "Not a spot-check sheet with passage IDs"
            + (f" (missing {', '.join(sorted(missing))})" if missing else " (no rows)") + "."
        )
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(VERDICT_COLUMNS)
    for row in rows:
        passage_ids = row["cited_passage_ids"].split()
        pmcids = list(dict.fromkeys(pid.split(".")[0] for pid in passage_ids))
        writer.writerow([
            row["question_id"], row["claim"], " ".join(pmcids), " ".join(passage_ids),
            row["judge_verdict"], row["your_verdict"].strip().lower(), row["your_notes"].strip(),
        ])
    return out.getvalue()


def judge_agreement(
    verdict_files: Iterable[str], question_ids: Collection[str] | None = None
) -> dict:
    """Share of claims where the judge's verdict equals the owner's.

    Only rows where both are one of supported, partial, unsupported count,
    and, if ``question_ids`` is given, only rows for those questions (so a
    report counts only its own question set's verdicts).
    ``rate`` is ``None`` until the owner has rated at least one claim.
    """
    rated = agreed = 0
    for text in verdict_files:
        for row in csv.DictReader(io.StringIO(text)):
            if question_ids is not None and row.get("question_id") not in question_ids:
                continue
            mine = (row.get("your_verdict") or "").strip().lower()
            judge = (row.get("judge_verdict") or "").strip().lower()
            if mine in VERDICTS and judge in VERDICTS:
                rated += 1
                agreed += mine == judge
    return {"rated": rated, "agreed": agreed, "rate": agreed / rated if rated else None}
