"""Tier 1 definer verb machinery per WebSpec.

Maps HTTP methods to definer families and resolves friendly verb aliases
to (METHOD, canonical_definer) pairs.

See: /Users/preston/I-m-A-g-I-n-E/WebSpec/docs/http-methods/definer-verbs.md
"""
from dataclasses import dataclass
from typing import Optional


# Family tables from the WebSpec atlas (Tier 1)
DEFINER_FAMILIES: dict[str, list[str]] = {
    "POST":  ["CREATE", "SEND", "INVOKE", "TRIGGER", "UPLOAD"],
    "PUT":   ["REPLACE", "OVERWRITE", "SET"],
    "PATCH": ["MODIFY", "APPEND", "AMEND", "RENAME"],
}

# Canonical (default) definer per method when user provides method but no definer.
CANONICAL: dict[str, str] = {
    "POST":  "CREATE",
    "PUT":   "REPLACE",
    "PATCH": "MODIFY",
}

# Friendly verb → (METHOD, canonical definer).
# Curated from verb-atlas.yaml — one entry per commonly-used verb.
VERB_ATLAS: dict[str, tuple[str, Optional[str]]] = {
    # GET family — safe, idempotent reads
    "list": ("GET", None), "get": ("GET", None), "read": ("GET", None),
    "query": ("GET", None), "find": ("GET", None), "fetch": ("GET", None),
    "search": ("GET", None), "show": ("GET", None), "view": ("GET", None),
    "check": ("GET", None), "retrieve": ("GET", None),

    # POST family — create/send/invoke/trigger/upload
    "create": ("POST", "CREATE"), "submit": ("POST", "CREATE"),
    "register": ("POST", "CREATE"), "add": ("POST", "CREATE"),
    "send": ("POST", "SEND"), "notify": ("POST", "SEND"),
    "triage": ("POST", "SEND"), "dispatch": ("POST", "SEND"),
    "invoke": ("POST", "INVOKE"), "execute": ("POST", "INVOKE"),
    "run": ("POST", "INVOKE"), "orchestrate": ("POST", "INVOKE"),
    "delegate": ("POST", "INVOKE"), "call": ("POST", "INVOKE"),
    "trigger": ("POST", "TRIGGER"), "fork": ("POST", "TRIGGER"),
    "start": ("POST", "TRIGGER"), "spawn": ("POST", "TRIGGER"),
    "subscribe": ("POST", "TRIGGER"), "monitor": ("POST", "TRIGGER"),
    "upload": ("POST", "UPLOAD"),

    # PUT family — full replacement
    "put": ("PUT", "REPLACE"), "replace": ("PUT", "REPLACE"),
    "overwrite": ("PUT", "OVERWRITE"), "set": ("PUT", "SET"),
    "reset": ("PUT", "OVERWRITE"),

    # PATCH family — partial update
    "patch": ("PATCH", "MODIFY"), "update": ("PATCH", "MODIFY"),
    "modify": ("PATCH", "MODIFY"), "edit": ("PATCH", "MODIFY"),
    "change": ("PATCH", "MODIFY"), "assign": ("PATCH", "MODIFY"),
    "append": ("PATCH", "APPEND"), "amend": ("PATCH", "AMEND"),
    "rename": ("PATCH", "RENAME"),

    # DELETE family — removal
    "delete": ("DELETE", None), "remove": ("DELETE", None),
    "revoke": ("DELETE", None), "cancel": ("DELETE", None),
    "stop": ("DELETE", None), "unlock": ("DELETE", None),

    # HEAD family — compact metadata reads
    "head": ("HEAD", None), "peek": ("HEAD", None), "exists": ("HEAD", None),
    "summary": ("HEAD", None), "compact": ("HEAD", None),

    # OPTIONS family — discovery
    "options": ("OPTIONS", None), "schema": ("OPTIONS", None),
    "discover": ("OPTIONS", None), "help": ("OPTIONS", None),
    "capabilities": ("OPTIONS", None),
}

# HTTP methods that never take a definer.
SAFE_METHODS = frozenset({"GET", "DELETE", "HEAD", "OPTIONS"})


class DefinerError(Exception):
    """Base class for definer-verb errors."""


class UnknownVerbError(DefinerError):
    """Raised when an op is neither an HTTP method nor a known friendly verb."""


class WrongFamilyError(DefinerError):
    """Raised when an explicit definer doesn't match the method's family."""


class DefinerRequiredError(DefinerError):
    """Raised when a state-mutating method was called without a definer."""


@dataclass(frozen=True)
class DefinerResolution:
    """Result of resolving a user-facing op to (METHOD, definer)."""
    method: str                 # normalized HTTP method
    definer: Optional[str]      # canonical definer, or None for safe methods
    raw_op: str                 # what the caller passed


def resolve_op(op: str, definer: Optional[str] = None) -> DefinerResolution:
    """Resolve a user-facing op to a (METHOD, definer) pair.

    The `op` argument is either an HTTP method ("GET", "POST", ...) or a
    friendly verb alias ("list", "submit", "fork", ...). For state-mutating
    methods (POST/PUT/PATCH), a definer may be passed explicitly; otherwise
    the method's canonical definer is used.

    Args:
        op: HTTP method or friendly verb.
        definer: Optional explicit definer for state-mutating methods.

    Returns:
        DefinerResolution with normalized method and canonical definer.

    Raises:
        UnknownVerbError: op is not a known HTTP method or friendly verb.
        WrongFamilyError: explicit definer doesn't match the method's family.
    """
    op_upper = op.upper()

    # HTTP method path
    if op_upper in DEFINER_FAMILIES or op_upper in SAFE_METHODS:
        method = op_upper
        if method in DEFINER_FAMILIES:
            if definer is not None:
                definer_upper = definer.upper()
                if definer_upper not in DEFINER_FAMILIES[method]:
                    raise WrongFamilyError(
                        f"Definer '{definer_upper}' is not in {method} family "
                        f"{DEFINER_FAMILIES[method]}"
                    )
                return DefinerResolution(method=method, definer=definer_upper, raw_op=op)
            return DefinerResolution(method=method, definer=CANONICAL[method], raw_op=op)
        # Safe method: definer ignored even if passed
        return DefinerResolution(method=method, definer=None, raw_op=op)

    # Friendly verb path
    verb = op.lower()
    if verb in VERB_ATLAS:
        method, canonical_definer = VERB_ATLAS[verb]
        return DefinerResolution(method=method, definer=canonical_definer, raw_op=op)

    raise UnknownVerbError(f"Unknown op: '{op}'. Not an HTTP method or known verb.")


def validate_definer(method: str, definer: Optional[str]) -> bool:
    """Validate a definer for a method.

    Safe methods (GET/DELETE/HEAD/OPTIONS) return True regardless — they
    ignore definers. State-mutating methods require the definer to be in
    their family.

    Args:
        method: HTTP method (case-insensitive).
        definer: Definer to validate, or None.

    Returns:
        True if the combination is valid.
    """
    method_upper = method.upper()
    if method_upper not in DEFINER_FAMILIES:
        return True  # Safe methods ignore definers
    if definer is None:
        return False
    return definer.upper() in DEFINER_FAMILIES[method_upper]
