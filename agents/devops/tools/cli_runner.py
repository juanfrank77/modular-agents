"""
agents/devops/tools/cli_runner.py
----------------------------------
Shared async CLI execution layer used by all DevOps tools.
Handles subprocess management, retry-once logic, and structured errors.

All tools import run_cli() — nothing else shells out directly.

Usage:
    from agents.devops.tools.cli_runner import run_cli, ToolError

    result = await run_cli(["gh", "pr", "list", "--json", "number,title"])
    # result.stdout, result.returncode, result.stderr
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass

from core.logger import get_logger

log = get_logger("devops.cli")


@dataclass
class CLIResult:
    stdout: str
    stderr: str
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class ToolError(Exception):
    """Raised after retry exhaustion or unrecoverable CLI failure."""

    def __init__(self, tool: str, command: list[str], stderr: str, returncode: int):
        self.tool = tool
        self.command = command
        self.stderr = stderr
        self.returncode = returncode
        super().__init__(
            f"[{tool}] command failed (rc={returncode}): {' '.join(command)}\n{stderr}"
        )


async def run_cli(
    args: list[str],
    tool_name: str = "",
    timeout: float = 30.0,
    retries: int = 1,
    env: dict[str, str] | None = None,
) -> CLIResult:
    """
    Run a CLI command asynchronously with retry-once logic.

    Args:
        args:       Full command as a list, e.g. ["gh", "pr", "list"]
        tool_name:  Label for logging (e.g. "github", "railway")
        timeout:    Seconds before the process is killed
        retries:    Number of retries after first failure (default 1)
        env:        Optional environment variable overrides

    Returns:
        CLIResult with stdout, stderr, returncode

    Raises:
        ToolError if all attempts fail
        ToolNotFoundError if the binary is not on PATH
    """
    binary = args[0]
    _assert_available(binary)

    label = tool_name or binary
    attempts = 1 + retries

    last_result: CLIResult | None = None

    for attempt in range(1, attempts + 1):
        log.info(
            "Running CLI command",
            event="cli_run",
            tool=label,
            cmd=" ".join(args),
            attempt=attempt,
        )

        try:
            with log.timer() as t:
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                try:
                    stdout_b, stderr_b = await asyncio.wait_for(
                        proc.communicate(), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.communicate()
                    raise ToolError(
                        label,
                        args,
                        f"Command timed out after {timeout}s",
                        returncode=-1,
                    )

            result = CLIResult(
                stdout=stdout_b.decode("utf-8", errors="replace").strip(),
                stderr=stderr_b.decode("utf-8", errors="replace").strip(),
                returncode=proc.returncode or 0,
            )

            log.info(
                "CLI command complete",
                event="cli_done",
                tool=label,
                returncode=result.returncode,
                duration_ms=t.ms,
            )

            if result.ok:
                return result

            last_result = result

            if attempt < attempts:
                log.warning(
                    "CLI command failed, retrying",
                    event="cli_retry",
                    tool=label,
                    attempt=attempt,
                    returncode=result.returncode,
                    stderr=result.stderr[:200],
                )
                await asyncio.sleep(1.5 * attempt)  # back off slightly

        except ToolError:
            raise
        except Exception as e:
            raise ToolError(label, args, str(e), returncode=-1) from e

    # All attempts exhausted
    assert last_result is not None
    raise ToolError(
        label,
        args,
        last_result.stderr or "Unknown error",
        last_result.returncode,
    )


def _assert_available(binary: str) -> None:
    """Raise a clear error if a required CLI tool is not on PATH."""
    if not shutil.which(binary):
        raise ToolError(
            binary,
            [binary],
            f"'{binary}' not found on PATH. "
            f"Install it and ensure it is accessible from this environment.",
            returncode=127,
        )
