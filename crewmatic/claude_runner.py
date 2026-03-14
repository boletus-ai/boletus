"""Claude CLI subprocess execution with semaphore limiting."""

import glob
import logging
import os
import subprocess
import threading

from .llm import CrewmaticError, LLMTimeoutError, LLMCLIError, LLMNotFoundError  # noqa: F401

logger = logging.getLogger(__name__)


class ClaudeRunner:
    """Executes Claude CLI as subprocess with concurrency control."""

    def __init__(
        self,
        max_concurrent: int = 4,
        timeout: int = 600,
        cwd: str | None = None,
        skip_permissions: bool = True,
    ):
        self._semaphore = threading.Semaphore(max_concurrent)
        self.timeout = timeout
        self.cwd = cwd or os.getcwd()
        self.skip_permissions = skip_permissions

    def call(
        self,
        system_prompt: str,
        user_message: str,
        model: str = "sonnet",
        allowed_tools: str | None = None,
        cwd: str | None = None,
        env_overrides: dict | None = None,
        mcp_config: str | None = None,
    ) -> str:
        """Execute Claude CLI and return response.

        Args:
            system_prompt: Agent's system prompt.
            user_message: Full assembled prompt with context.
            model: Claude model to use (opus/sonnet/haiku).
            allowed_tools: Comma-separated tool names, or None for no tools.
            cwd: Working directory for the subprocess.
            env_overrides: Additional environment variables.

        Returns:
            Claude's response text.

        Raises:
            LLMTimeoutError: If Claude doesn't respond within timeout.
            LLMCLIError: If Claude CLI returns non-zero exit code.
            LLMNotFoundError: If Claude CLI is not installed.
        """
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)

        # Pass SSH_AUTH_SOCK for git operations
        if "SSH_AUTH_SOCK" not in env:
            socks = glob.glob("/tmp/ssh-*/agent.*")
            if socks:
                env["SSH_AUTH_SOCK"] = socks[0]

        if env_overrides:
            env.update(env_overrides)

        # Guard against ARG_MAX — Linux limit is ~2MB for total argv
        # If prompt is too large, truncate with a warning
        max_prompt_bytes = 1_500_000  # ~1.5MB, safe under 2MB ARG_MAX
        if len(user_message.encode("utf-8")) > max_prompt_bytes:
            logger.warning(
                f"Prompt too large ({len(user_message)} chars), truncating to fit ARG_MAX"
            )
            user_message = user_message[:max_prompt_bytes // 2] + \
                "\n\n... [CONTEXT TRUNCATED — prompt exceeded size limit]"

        cmd = [
            "claude",
            "-p", user_message,
            "--system-prompt", system_prompt,
            "--model", model,
            "--no-session-persistence",
        ]

        if mcp_config:
            cmd.extend(["--mcp-config", mcp_config])

        if allowed_tools:
            cmd.extend(["--allowedTools", allowed_tools])
            if self.skip_permissions:
                cmd.append("--dangerously-skip-permissions")

        work_dir = cwd or self.cwd

        try:
            logger.debug("Waiting for Claude semaphore...")
            with self._semaphore:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=work_dir,
                    env=env,
                )
                try:
                    stdout, stderr = proc.communicate(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
                    raise
            if proc.returncode != 0:
                error_msg = (stderr or "")[:500].strip()
                logger.error(f"Claude CLI error (exit {proc.returncode}): {error_msg}")
                raise LLMCLIError(f"Claude CLI exit {proc.returncode}: {error_msg}")
            return stdout.strip()
        except subprocess.TimeoutExpired:
            raise LLMTimeoutError(
                f"Claude didn't respond within {self.timeout // 60} minutes"
            )
        except FileNotFoundError:
            raise LLMNotFoundError(
                "Claude CLI not found. Install it: https://docs.anthropic.com/en/docs/claude-code"
            )
