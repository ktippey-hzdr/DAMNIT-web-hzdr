"""Hold CLAUDE.md's match-quality ladder to ``hzdr_nexus.MATCH_RANK``.

The line was written when there were six ranks. Three identity and
disambiguation ranks were added in code and the doc kept listing six, with
four of them numbered wrongly, and nothing noticed. The ladder decides which
of two competing matches wins a shot, so a reader working from the doc would
reason about the wrong order.
"""

import re
from operator import itemgetter
from pathlib import Path

from damnit_api.metadata.hzdr_nexus import MATCH_RANK

CLAUDE_MD = Path(__file__).resolve().parents[2] / "CLAUDE.md"


def _documented_ladder() -> list[tuple[str, int]]:
    text = CLAUDE_MD.read_text(encoding="utf-8")
    heading = text.index("### Match quality ranks")
    line = next(
        candidate for candidate in text[heading:].splitlines()[1:] if candidate.strip()
    )
    return [(name, int(rank)) for name, rank in re.findall(r"`(\w+)` \((\d+)\)", line)]


def test_claude_md_lists_every_match_rank_in_order():
    documented = _documented_ladder()
    in_code = sorted(MATCH_RANK.items(), key=itemgetter(1))
    assert documented == in_code, (
        "CLAUDE.md 'Match quality ranks' disagrees with hzdr_nexus.MATCH_RANK; "
        f"documented {documented}, code {in_code}"
    )
