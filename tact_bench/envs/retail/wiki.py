# Copyright Sierra

import os
import re
from datetime import date, datetime
from typing import Optional

FOLDER_PATH = os.path.dirname(__file__)

with open(os.path.join(FOLDER_PATH, "wiki.md"), "r") as f:
    WIKI = f.read()

# The one date-keyed rule in the policy is the 30-day return window, so the
# "Today's date is ..." line in wiki.md is a benchmark parameter with real
# consequences: it decides whether a task's return is in or out of window, and
# therefore whether that task's ground truth is reachable at all. Parsing it here
# keeps every consumer — the generator that mints fixture dates, and any runtime
# check — reading the same value from the same place, instead of each keeping a
# hard-coded copy that silently drifts when this line is edited.
_TODAY_RE = re.compile(r"[Tt]oday'?s date is\s*(\d{4}-\d{2}-\d{2})")

# Used only when a policy document states no date at all.
FALLBACK_POLICY_DATE = date(2026, 4, 13)


def parse_policy_date(policy_text: str, default: Optional[date] = None) -> date:
    """The current date a policy document declares, or ``default``."""
    match = _TODAY_RE.search(policy_text or "")
    if match:
        try:
            return datetime.fromisoformat(match.group(1)).date()
        except ValueError:
            pass
    return default or FALLBACK_POLICY_DATE


#: The date the retail policy currently states.
POLICY_DATE = parse_policy_date(WIKI)
