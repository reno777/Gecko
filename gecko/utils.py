import re


# Regions that count as "USA preferred"
_USA_PATTERNS = [
    re.compile(r"\(USA\)", re.IGNORECASE),           # exact USA only
    re.compile(r"\(USA,", re.IGNORECASE),            # USA, Europe / USA, Japan / etc.
    re.compile(r",\s*USA\)", re.IGNORECASE),         # Europe, USA
]

_REJECT_REGIONS = re.compile(
    r"\((Europe|Japan|Korea|Australia|Germany|France|Spain|Italy|Netherlands|Brazil|China|Taiwan|Russia)[^)]*\)",
    re.IGNORECASE,
)


def region_score(title: str) -> int:
    """
    Returns a sort key for region preference (lower = better).
    0 = exact (USA) only
    1 = USA + other regions
    2 = no region tag (untagged)
    99 = non-USA region (should be filtered out unless no USA exists)
    """
    if re.search(r"\(USA\)", title, re.IGNORECASE):
        return 0
    for pat in _USA_PATTERNS[1:]:
        if pat.search(title):
            return 1
    if _REJECT_REGIONS.search(title):
        return 99
    return 2


def is_usa(title: str) -> bool:
    return region_score(title) < 99


def parse_game_list(path: str) -> list[str]:
    """Read a .txt file of game names, one per line. Strips blanks and comments."""
    with open(path) as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    return lines
