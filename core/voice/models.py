"""Data models for the voice layer.

Option: a single multiple-choice item the agent presents.
Action: the typed result the voice layer hands back to the agent.
"""
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class Option:
    """One multiple-choice option.

    Attributes:
        id: Stable identifier the agent uses to act on a selection.
        label: On-screen text.
        say: Optional spoken phrasing; falls back to ``label``.
    """

    id: str
    label: str
    say: Optional[str] = None

    @property
    def spoken(self) -> str:
        return self.say or self.label


@dataclass
class Action:
    """The voice layer's classified result.

    action is one of:
        select | repeat | regenerate | drilldown | freeform | nomatch | refused
    value carries the option id (select/drilldown), the spoken direction
    (regenerate), or a reason (refused).
    """

    action: str
    transcript: str = ""
    value: Optional[str] = None
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, object]:
        return {
            "action": self.action,
            "value": self.value,
            "transcript": self.transcript,
            "confidence": round(self.confidence, 3),
        }
