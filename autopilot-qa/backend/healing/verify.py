"""Checks a proposed locator against a DOM snapshot in headless Chromium (roadmap G10)."""
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

_MATCH_INFO = """els => els.map(e => ({
  tag: e.tagName.toLowerCase(),
  attrs: Object.fromEntries([...e.attributes].map(a => [a.name, a.value])),
  text: e.textContent.trim().replace(/\\s+/g, ' '),
}))"""


@dataclass(frozen=True)
class LocatorCheck:
    locator: str | None
    scope_matches: int
    matches: tuple[dict[str, Any], ...] = ()
    error: str | None = None

    @property
    def count(self) -> int:
        return len(self.matches)

    @property
    def unique(self) -> bool:
        return self.error is None and self.count == 1


class DomVerifier:
    """One headless Chromium for many checks. JavaScript is disabled: we inspect markup, not behaviour."""

    async def __aenter__(self) -> "DomVerifier":
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch()
        context = await self._browser.new_context(java_script_enabled=False)
        self._page = await context.new_page()
        self._loaded: str | None = None
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._browser.close()
        await self._playwright.stop()

    async def _load(self, html: str) -> None:
        if html != self._loaded:
            await self._page.set_content(html)
            self._loaded = html

    async def scope_counts(self, html: str, locator: str, scope_selector: str) -> list[int]:
        """How many elements the locator matches inside each container matching the scope, in DOM order."""
        await self._load(html)
        counts = []
        for container in await self._page.locator(scope_selector).all():
            try:
                counts.append(await container.locator(locator).count())
            except PlaywrightError:
                counts.append(0)
        return counts

    async def check(self, html: str, locator: str | None, *, scope_selector: str | None = None,
                    scope_text: str | None = None) -> LocatorCheck:
        if not locator:
            return LocatorCheck(locator, 0, error="no locator proposed")
        await self._load(html)
        scope = self._page.locator(scope_selector, has_text=scope_text) if scope_selector else self._page.locator("html")
        scope_matches = await scope.count()
        if not scope_matches:
            return LocatorCheck(locator, 0, error="scope not found")
        try:
            found = await scope.first.locator(locator).evaluate_all(_MATCH_INFO)
        except PlaywrightError as exc:
            return LocatorCheck(locator, scope_matches, error=str(exc).splitlines()[0])
        return LocatorCheck(locator, scope_matches, tuple(found))
