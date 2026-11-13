"""Citations for every protocol fact the code relies on.

Each capability-table entry carries a Source. tests/unit/test_capabilities.py
fails if any entry lacks one, so a command cannot be added without saying
where it came from. docs/civ-reference.md is the human-readable index.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Source:
    document: str
    page: str
    note: str = ""
    # False until checked against the manufacturer's own PDF (not a
    # third-party reproduction) or on hardware. Listed in docs/civ-reference.md.
    verified_primary: bool = False

    def __str__(self) -> str:
        s = f"{self.document}, p. {self.page}"
        return f"{s} ({self.note})" if self.note else s


# Read 2026-11-13 from third-party reproductions of the Icom PDF
# (manualslib.com manual 1106166, libble.eu manual 828087). Page numbers are
# the manual's own. TODO(verify): re-check against Icom's PDF.
IC7300_FULL_MANUAL = "Icom IC-7300 Full Manual, section 19 Control Command"
