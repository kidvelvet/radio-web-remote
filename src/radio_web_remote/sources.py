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

# Icom's own PDFs from icomjapan.com/support/manual (download agreement
# accepted by the maintainer, 2026-11-13) or icomeurope.com. Read 2026-11-13.
# Page numbers are the ones printed on the page. The PDFs are not in the repo.
IC705_CIV = "Icom IC-705 CI-V Reference Guide (A7560-8EX-6, Jan. 2023)"
IC705_BASIC = "Icom IC-705 Basic Manual (A7560D-1EX-7, Sep. 2023)"
IC705_ADVANCED = "Icom IC-705 Advanced Manual (A7560-7EX-5, Apr. 2026)"
IC9700_CIV = "Icom IC-9700 CI-V Reference Guide (A7508-3EX-4, Mar. 2023)"
IC9700_BASIC = "Icom IC-9700 Basic Manual (A7508H-1EX-7, Jan. 2026)"
IC9700_ADVANCED = "Icom IC-9700 Advanced Manual (A7508-2EX-3, May 2026)"
