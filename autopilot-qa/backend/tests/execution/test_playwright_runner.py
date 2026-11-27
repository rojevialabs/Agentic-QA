import json
import shutil
import uuid

import httpx
import pytest

from execution.playwright_runner import ServiceUnavailableError, parse_report, reset_all_services, run_script
from settings import BACKEND_DIR

TESTS_DIR = BACKEND_DIR.parent / "tests-generated"
SEED_MAP = BACKEND_DIR / "config" / "selector_map.v1.json"
HEADER = "const { test, expect } = require('../support/fixtures');\n"
PASSING = HEADER + "test('ok', async ({ page }) => {\n  await page.setContent('<p id=\"x\">hi</p>');\n  await expect(page.locator('#x')).toHaveText('hi');\n});\n"
FAILING = PASSING.replace("toHaveText('hi')", "toHaveText('bye', { timeout: 500 })")
URLS = {"auth": "http://a.test", "catalog": "http://c.test"}


@pytest.fixture
def scratch():
    directory = TESTS_DIR / f"_pytest_{uuid.uuid4().hex[:8]}"
    directory.mkdir()
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


async def run(scratch, tmp_path, code):
    script = scratch / "case.spec.js"
    script.write_text(code, encoding="utf-8")
    return await run_script(script, tests_dir=TESTS_DIR, selector_map_file=SEED_MAP,
                            artifacts_dir=tmp_path / "attempt1", service_urls=URLS)


async def test_passing_script(scratch, tmp_path):
    outcome = await run(scratch, tmp_path, PASSING)
    assert (outcome.status, outcome.error, outcome.failure) == ("pass", None, None)
    assert outcome.duration_ms > 0


async def test_failing_script_returns_the_error_and_captured_artifacts(scratch, tmp_path):
    outcome = await run(scratch, tmp_path, FAILING)
    assert outcome.status == "fail"
    assert "bye" in outcome.error and "\x1b" not in outcome.error
    assert outcome.failure["dom_path"].endswith("dom.html")
    assert (tmp_path / "attempt1" / "dom.html").exists()
    assert (tmp_path / "attempt1" / "stdout.json").exists()


async def test_a_script_that_does_not_load_fails_cleanly(scratch, tmp_path):
    outcome = await run(scratch, tmp_path, HEADER + "test('broken', async () => {\n")
    assert outcome.status == "fail" and outcome.error


def test_parse_report_walks_nested_suites():
    report = {"stats": {"expected": 0, "unexpected": 1}, "errors": [], "suites": [{"specs": [], "suites": [{"specs": [
        {"tests": [{"results": [{"duration": 42, "error": {"message": "\x1b[31mboom\x1b[39m"},
                                 "stdout": [{"text": "log line\n"}], "stderr": []}]}]}]}]}]}
    assert parse_report(report) == (False, 42, "boom", ["log line"])


async def test_reset_calls_every_service():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    await reset_all_services(URLS, transport=httpx.MockTransport(handler))
    assert seen == ["http://a.test/api/__reset", "http://c.test/api/__reset"]


@pytest.mark.parametrize("respond", ["refuse", "500"])
async def test_reset_names_the_unavailable_service(respond):
    def handler(request):
        if request.url.host == "c.test":
            if respond == "refuse":
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(500)
        return httpx.Response(200, json={"ok": True})

    with pytest.raises(ServiceUnavailableError, match="catalog service not available at http://c.test") as info:
        await reset_all_services(URLS, transport=httpx.MockTransport(handler))
    assert info.value.service == "catalog"
