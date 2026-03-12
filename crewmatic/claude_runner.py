"""Claude CLI subprocess execution with semaphore limiting."""

import glob
import logging
import os
import subprocess
import threading

logger = logging.getLogger(__name__)


class CrewmaticError(Exception):
    """Base exception for crewmatic errors."""


class LLMTimeoutError(CrewmaticError):
    """LLM call timed out."""


class LLMCLIError(CrewmaticError):
    """Claude CLI returned a non-zero exit code."""


class LLMNotFoundError(CrewmaticError):
    """Claude CLI binary not found."""


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

        cmd = [
            "claude",
            "-p", user_message,
            "--system-prompt", system_prompt,
            "--model", model,
            "--no-session-persistence",
        ]

        if allowed_tools:
            cmd.extend(["--allowedTools", allowed_tools])
            if self.skip_permissions:
                cmd.append("--dangerously-skip-permissions")

        work_dir = cwd or self.cwd

        try:
            logger.debug("Waiting for Claude semaphore...")
            with self._semaphore:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    cwd=work_dir,
                    env=env,
                )
            if result.returncode != 0:
                error_msg = result.stderr[:500].strip()
                logger.error(f"Claude CLI error (exit {result.returncode}): {error_msg}")
                raise LLMCLIError(f"Claude CLI exit {result.returncode}: {error_msg}")
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            raise LLMTimeoutError(
                f"Claude didn't respond within {self.timeout // 60} minutes"
            )
        except FileNotFoundError:
            raise LLMNotFoundError(
                "Claude CLI not found. Install it: https://docs.anthropic.com/en/docs/claude-code"
            )
