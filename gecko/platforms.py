from dataclasses import dataclass, field


@dataclass(frozen=True)
class Platform:
    name: str
    # Formats natively available on ROM sites for this platform
    native_formats: tuple[str, ...]
    # Conversion edges: {desired_fmt: source_fmt_that_can_produce_it}
    conversions: dict[str, str] = field(default_factory=dict)
    # Default preferred format when --format is omitted
    default_format: str = ""


PLATFORMS: dict[str, Platform] = {
    "gamecube": Platform(
        name="gamecube",
        native_formats=("iso", "rvz", "gcz"),
        conversions={"iso": "rvz"},
        default_format="rvz",
    ),
}


def get_platform(name: str) -> Platform:
    key = name.lower()
    if key not in PLATFORMS:
        supported = ", ".join(sorted(PLATFORMS))
        raise ValueError(f"Unknown platform '{name}'. Supported: {supported}")
    return PLATFORMS[key]


def revision_priority(title: str) -> int:
    """
    Sort key for ROM titles by revision preference.
    Priority: Rev 1 (0) > Rev 0 (1) > no revision (2) > Rev 2+ (n+1)

    Pass as: sorted(titles, key=revision_priority)
    """
    import re

    match = re.search(r"\(Rev (\d+)\)", title, re.IGNORECASE)
    if not match:
        return 2
    rev = int(match.group(1))
    if rev == 1:
        return 0
    if rev == 0:
        return 1
    # Rev 2, 3, ... sorted after, preserving relative order
    return rev + 1
