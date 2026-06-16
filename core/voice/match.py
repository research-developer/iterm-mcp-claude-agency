"""Classify a transcript into a typed Action against the current options.

Resolution order: control phrases (repeat/drilldown/regenerate) ->
leading ordinal/number -> keyword/fuzzy label match -> freeform/nomatch.
The agent owns everything downstream of this.
"""
import re
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from core.voice.models import Action, Option

_ORDINALS = {
    "one": 1, "first": 1, "two": 2, "second": 2,
    "three": 3, "third": 3, "four": 4, "fourth": 4,
}
_REPEAT = ("repeat", "say again", "what were they", "what are they")
# Multi-word triggers only — single words like "none"/"deeper"/"expand"
# collide with ordinary speech ("I want none", "deeper meaning"), so they
# are intentionally excluded.
_REGEN = ("none of these", "none of those", "something else",
          "different options", "other options")
_DRILL = ("drill down", "go deeper", "dig deeper", "tell me more",
          "more on", "more about")

# A leading selector: optional filler ("the"/"option"/"number"/"choice"),
# then the first real token. Only this anchored token may pick an option,
# so a trailing noun ("the banana one") never selects option 1.
_LEAD = re.compile(r"^(?:the |option |number |choice )*(\w+)")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).strip()


def _has_phrase(t: str, phrase: str) -> bool:
    return re.search(r"\b" + re.escape(phrase) + r"\b", t) is not None


def _leading_number(t: str) -> Optional[int]:
    m = _LEAD.match(t)
    if not m:
        return None
    tok = m.group(1)
    if tok.isdigit():
        return int(tok)
    return _ORDINALS.get(tok)


def _best_label(t: str, options: List[Option]) -> Tuple[Optional[str], float]:
    """Best fuzzy match of ``t`` against each option's label AND spoken text."""
    best_id, best = None, 0.0
    for opt in options:
        for text in (opt.label, opt.spoken):
            lab = _norm(text)
            if not lab:
                continue
            tokens_t, tokens_l = set(t.split()), set(lab.split())
            overlap = len(tokens_t & tokens_l) / max(1, len(tokens_l))
            ratio = SequenceMatcher(None, t, lab).ratio()
            score = max(overlap, ratio)
            if score > best:
                best_id, best = opt.id, score
    return best_id, best


def classify(transcript: str, options: List[Option]) -> Action:
    t = _norm(transcript)
    if not t:
        return Action("nomatch", transcript=transcript)

    if any(_has_phrase(t, p) for p in _REPEAT):
        return Action("repeat", transcript=transcript, confidence=1.0)

    for p in _DRILL:
        if _has_phrase(t, p):
            target, score = _best_label(_norm(t.replace(p, " ")), options)
            return Action("drilldown", transcript=transcript,
                          value=(target if score >= 0.5 else None), confidence=0.9)

    for p in _REGEN:
        if _has_phrase(t, p):
            direction = _norm(t.replace(p, " "))
            return Action("regenerate", transcript=transcript,
                          value=(direction or None), confidence=0.9)

    num = _leading_number(t)
    if num is not None and 1 <= num <= len(options):
        return Action("select", transcript=transcript,
                      value=options[num - 1].id, confidence=1.0)

    best_id, score = _best_label(t, options)
    if best_id is not None and score >= 0.6:
        return Action("select", transcript=transcript, value=best_id,
                      confidence=score)

    return Action("freeform", transcript=transcript, confidence=0.3)
