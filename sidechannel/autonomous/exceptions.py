"""Custom exception hierarchy for the autonomous task execution system.

Exception design principles:
- AutonomousError is the base for all autonomous subsystem exceptions
- Subclasses map to natural failure domains: git, verification, quality gates, task execution
- All exceptions support 'from e' chaining for full traceability
- Retryable vs non-retryable is explicit in the hierarchy
- Exceptions preserve structured context (task_id, project_path, etc.)
"""

from typing import Optional


class AutonomousError(Exception):
    """Base exception for all autonomous system errors."""

    def __init__(self, message: str, *, task_id: Optional[int] = None):
        self.task_id = task_id
        super().__init__(message)


# --- Task execution errors ---


class TaskExecutionError(AutonomousError):
    """A task failed during execution (catch-all for the execute() method)."""


class TaskContextError(AutonomousError):
    """Failed to build task context (learnings, story, PRD lookup)."""


# --- Git operation errors ---


class GitOperationError(AutonomousError):
    """Base for git-related failures (checkpoint, commit, diff)."""


class GitCheckpointError(GitOperationError):
    """Failed to create a pre-task git checkpoint."""


class GitCommitError(GitOperationError):
    """Failed to commit task changes."""


class GitDiffError(GitOperationError):
    """Failed to retrieve git diff."""


# --- Verification errors ---


class VerificationError(AutonomousError):
    """Base for verification agent failures."""


class VerificationTimeoutError(VerificationError):
    """Verification agent timed out (infrastructure failure, fail-open)."""


class VerificationRunnerError(VerificationError):
    """Verification Claude runner crashed (infrastructure failure, fail-open)."""


class VerificationParseError(VerificationError):
    """Could not parse verification agent output (infrastructure failure, fail-open)."""


# --- Quality gate errors ---


class QualityGateError(AutonomousError):
    """Base for quality gate runner failures."""


class TestExecutionError(QualityGateError):
    """Test suite execution failed unexpectedly (not a test failure, but a crash)."""


class TypecheckExecutionError(QualityGateError):
    """Type checker execution failed unexpectedly."""


class LintExecutionError(QualityGateError):
    """Linter execution failed unexpectedly."""


class ToolDetectionError(QualityGateError):
    """Failed to detect or read config for a quality gate tool."""


# --- Loop / worker errors ---


class LoopError(AutonomousError):
    """Error in the autonomous processing loop."""


class WorkerError(AutonomousError):
    """A task worker encountered an unrecoverable error."""


class StaleTaskRecoveryError(AutonomousError):
    """Failed to recover stale IN_PROGRESS tasks."""


class NotificationError(AutonomousError):
    """Failed to send a user notification (non-critical)."""
