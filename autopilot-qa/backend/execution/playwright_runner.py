"""Runs generated Playwright scripts against the target platform and parses the results (spec §4.4, §6.8)."""
import asyncio
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

ANSI = re.compile(r"\x1b\[[0-9;]*m")
URL_ENV = {"auth": "AUTH_SERVICE_URL", "catalog": "CATALOG_SERVICE_URL", "cart": "CART_SERVICE_URL",
           "order": "ORDER_SERVICE_URL", "payment": "PAYMENT_SERVICE_URL"}


class ServiceUnavailableError(RuntimeError):
    def __init__(self, service: str, url: str, detail: str) -> None:
        super().__init__(f"{service} service not available at {url}: {detail}")
        self.service, self.url = service, url


async def reset_all_services(urls: dict[str, str], *, transport: httpx.AsyncBaseTransport | None = None) -> None:
    """Restore every service's seed data so runs never drift (spec §6.8)."""
    async with httpx.AsyncClient(timeout=5.0, transport=transport) as client:
        for service, url in urls.items():
            try:
                response = await client.post(f"{url}/api/__reset")
            except httpx.HTTPError as exc:
                raise ServiceUnavailableError(service, url, str(exc) or type(exc).__name__) from exc
            if response.status_code != 200:
                raise ServiceUnavailableError(service, url, f"HTTP {response.status_code}")


@dataclass
class RunOutcome:
    status: Literal["pass", "fail"]
    duration_ms: int
    error: str | None = None
    logs: list[str] = field(default_factory=list)
    failure: dict[str, Any] | None = None
    artifacts_dir: Path | None = None


def parse_report(report: dict[str, Any]) -> tuple[bool, int, str | None, list[str]]:
    """Reduce a Playwright JSON report to (passed, duration_ms, first error, log lines)."""
    results: list[dict[str, Any]] = []

    def walk(suite: dict[str, Any]) -> None:
        for spec in suite.get("specs", []):
            for test in spec.get("tests", []):
                results.extend(test.get("results", []))
        for child in suite.get("suites", []):
            walk(child)

    for suite in report.get("suites", []):
        walk(suite)
    errors = [ANSI.sub("", e.get("message", "")) for e in report.get("errors", [])]
    errors += [ANSI.sub("", r["error"].get("message", "")) for r in results if r.get("error")]
    logs = [entry.get("text", "").rstrip() for r in results for stream in ("stdout", "stderr") for entry in r.get(stream, [])]
    stats = report.get("stats", {})
    passed = not errors and stats.get("unexpected", 0) == 0 and stats.get("expected", 0) >= 1
    return passed, int(sum(r.get("duration", 0) for r in results)), (errors[0] if errors else None), [l for l in logs if l]


def playwright_cli(tests_dir: Path) -> Path:
    cli = tests_dir / "node_modules" / "@playwright" / "test" / "cli.js"
    if not cli.exists():
        raise FileNotFoundError(f"{cli} is missing; run `npm install` in {tests_dir}")
    return cli


async def run_script(script_path: Path, *, tests_dir: Path, selector_map_file: Path, artifacts_dir: Path,
                     service_urls: dict[str, str], timeout_s: int = 300) -> RunOutcome:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "APQA_SELECTOR_MAP": str(selector_map_file), "APQA_ARTIFACTS_DIR": str(artifacts_dir),
           "FORCE_COLOR": "0", **{URL_ENV[s]: url for s, url in service_urls.items() if s in URL_ENV}}
    relative = Path(script_path).resolve().relative_to(tests_dir.resolve()).as_posix()
    command = ["node", str(playwright_cli(tests_dir)), "test", relative, "--reporter=json",
               f"--output={(artifacts_dir / 'playwright').as_posix()}"]

    started = time.perf_counter()
    try:
        proc = await asyncio.to_thread(subprocess.run, command, cwd=tests_dir, env=env, capture_output=True,
                                       text=True, encoding="utf-8", errors="replace", timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return RunOutcome("fail", timeout_s * 1000, error=f"script timed out after {timeout_s}s", artifacts_dir=artifacts_dir)
    wall_ms = int((time.perf_counter() - started) * 1000)
    (artifacts_dir / "stdout.json").write_text(proc.stdout, encoding="utf-8")
    (artifacts_dir / "stderr.txt").write_text(proc.stderr, encoding="utf-8")

    try:
        report = json.loads(proc.stdout[proc.stdout.index("{"):])
    except ValueError:
        detail = ANSI.sub("", proc.stderr.strip())[:500]
        return RunOutcome("fail", wall_ms, error=f"no JSON report (exit {proc.returncode}): {detail}", artifacts_dir=artifacts_dir)
    passed, duration_ms, error, logs = parse_report(report)
    failure_file = artifacts_dir / "failure.json"
    failure = json.loads(failure_file.read_text(encoding="utf-8")) if (not passed and failure_file.exists()) else None
    return RunOutcome("pass" if passed else "fail", duration_ms or wall_ms, error, logs, failure, artifacts_dir)
