"""Native execution subsystem: synchronous shell execution, background jobs
and persistent PTY sessions."""

from .jobs import JobError, JobManager
from .pty import SessionError, SessionManager
from .runner import ExecResult, run_command

__all__ = [
    "ExecResult",
    "JobError",
    "JobManager",
    "SessionError",
    "SessionManager",
    "run_command",
]
