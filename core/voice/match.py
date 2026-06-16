"""Classify a transcript into a typed Action against the current options.

Resolution order: control phrases (repeat/drilldown/regenerate) ->
leading ordinal -> keyword/fuzzy label match -> freeform/nomatch.
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
_REGEN = ("none of these", "something else", "different options",
          "other options", "none")
_DRILL = ("drill down", "go deeper", "deeper", "expand",
          "tell me more", "more on", "more about")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).strip()


def _leading_number(t: str) -> Optional[int]:
    m = re.match(r"(?:option |number |choice )?(\d+)\b", t)
    if m:
        return int(m.group(1))
    for word, n in _ORDINALS.items():
        if re.search(r"\b" + word + r"\b", t):
            return n
    return None


def _best_label(t: str, options: List[Option]) -> Tuple[Optional[str], float]:
    best_id, best = None, 0.0
    for opt in options:
        lab = _norm(opt.label)
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

    if any(p in t for p in _REPEAT):
        return Action("repeat", transcript=transcript, confidence=1.0)

    for p in _DRILL:
        if p in t:
            target, score = _best_label(t.replace(p, " "), options)
            return Action("drilldown", transcript=transcript,
                          value=(target if score >= 0.5 else None), confidence=0.9)

    for p in _REGEN:
        if p in t:
            direction = t.replace(p, " ").strip()
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
