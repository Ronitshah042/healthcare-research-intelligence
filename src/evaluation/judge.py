"""LLM-as-judge: does each cited claim follow from the passages it cites?

The judge prompt lives in ``eval/judge_prompt.txt`` so it is reviewable and
versioned with the results. Judge replies are cached like answers.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from generation.cache import AnswerCache
from generation.llm import LLMClient, LLMError, LLMRateLimitedError
from generation.rag import Answer, neutralise


VERDICTS = ("supported", "partial", "unsupported")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
_CITATION_NUMBER = re.compile(r"\[(\d+)\]")
_JSON_OBJECT = re.compile(r"\{[^{}]*\}")


@dataclass(frozen=True)
class ClaimVerdict:
    claim: str
    cited: tuple[int, ...]
    # One of VERDICTS, or "unjudged" when the judge gave no usable verdict.
    verdict: str
    reason: str


def extract_claims(text: str) -> list[tuple[str, tuple[int, ...]]]:
    """Sentences that carry at least one ``[n]`` citation, with their numbers."""
    claims = []
    for sentence in _SENTENCE_END.split(text):
        numbers = tuple(dict.fromkeys(int(n) for n in _CITATION_NUMBER.findall(sentence)))
        cleaned = sentence.strip(" *-\t")
        if numbers and cleaned:
            claims.append((cleaned, numbers))
    return claims


def build_judge_input(answer: Answer) -> tuple[str, list[tuple[str, tuple[int, ...]]]]:
    """The judge's user message, and the claims it numbers."""
    claims = extract_claims(answer.text)
    cited = sorted({n for _, numbers in claims for n in numbers if 1 <= n <= len(answer.evidence)})
    # Each passage once, however many claims cite it: keeps the request small.
    passages = "\n\n".join(
        f'<evidence id="{number}">\n'
        f"{neutralise(answer.evidence[number - 1].chunk.text)}\n</evidence>"
        for number in cited
    )
    lines = "\n".join(
        f"Claim {index} (cites {' '.join(f'[{n}]' for n in numbers)}): {neutralise(claim)}"
        for index, (claim, numbers) in enumerate(claims, start=1)
    )
    return f"Passages:\n\n{passages}\n\nClaims:\n{lines}", claims


def parse_verdicts(reply: str, claim_count: int) -> dict[int, tuple[str, str]]:
    """Read the judge's JSON list; ignore anything malformed.

    A reply cut off mid-list still yields its complete ``{...}`` items.
    """
    start, end = reply.find("["), reply.rfind("]")
    try:
        items = json.loads(reply[start : end + 1]) if 0 <= start < end else None
    except ValueError:
        items = None
    if not isinstance(items, list):
        items = []
        for text in _JSON_OBJECT.findall(reply):
            try:
                items.append(json.loads(text))
            except ValueError:
                continue
    found: dict[int, tuple[str, str]] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        number, verdict = item.get("claim"), str(item.get("verdict", "")).lower()
        if isinstance(number, int) and 1 <= number <= claim_count and verdict in VERDICTS:
            found.setdefault(number, (verdict, str(item.get("reason", ""))[:300]))
    return found


def judge_answer(
    answer: Answer, *, llm: LLMClient, cache: AnswerCache, prompt: str
) -> tuple[ClaimVerdict, ...]:
    """Rate every cited claim of a non-abstained answer.

    A rate-limited judge raises ``LLMRateLimitedError`` so the caller can stop
    judging; any other failure leaves the claims "unjudged".
    """
    if answer.abstained:
        return ()
    user, claims = build_judge_input(answer)
    if not claims:
        return ()
    key = "judge:" + hashlib.sha256((prompt + "\0" + user).encode()).hexdigest()
    reply = cache.get(key)
    if reply is None:
        try:
            reply = llm.generate(prompt, user)
        except LLMRateLimitedError:
            raise
        except LLMError:
            reply = None
        else:
            cache.put(key, reply)
    verdicts = parse_verdicts(reply.text, len(claims)) if reply else {}
    return tuple(
        ClaimVerdict(claim, numbers, *verdicts.get(index, ("unjudged", "")))
        for index, (claim, numbers) in enumerate(claims, start=1)
    )
