"""Grounding facts about ShopLite for LLM prompts (roadmap G13), taken from the target platform README."""
import re

from settings import BACKEND_DIR

PLATFORM_README = BACKEND_DIR.parents[1] / "target-platform" / "README.md"


def load_platform_context(sections: tuple[str, ...] = ("Services", "UI text", "Test data"), path=PLATFORM_README) -> str:
    """Return the requested level-2 sections of the README, in the order given."""
    found: dict[str, str] = {}
    for chunk in re.split(r"(?m)^## ", path.read_text(encoding="utf-8"))[1:]:
        title = chunk.splitlines()[0].strip()
        found[title] = "## " + chunk.strip()
    missing = [s for s in sections if s not in found]
    if missing:
        raise ValueError(f"platform README has no section(s): {', '.join(missing)}")
    return "\n\n".join(found[s] for s in sections)
