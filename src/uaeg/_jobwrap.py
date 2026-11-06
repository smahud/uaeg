"""Background job wrapper (internal helper; not part of the public API).

The JobManager spawns this shim as the session leader of a detached process
group. The shim forks the real command as its child and, critically, writes
``pid`` and ``exit`` files next to the job's output files. Because the shim
is independent of the UAEG server process:

- the job survives UAEG restarts (AT-11) and HTTP client disconnects,
- the exit code is recorded even if UAEG was down when the command finished,
- after a VPS reboot the missing ``exit`` file lets the server distinguish
  "interrupted" from "completed" (PRD FR-009).

Usage (internal):
    python -m uaeg._jobwrap <job_dir> -- <command...>
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 4 or sys.argv[2] != "--":
        print("usage: python -m uaeg._jobwrap <job_dir> -- <command...>", file=sys.stderr)
        return 125

    job_dir = Path(sys.argv[1])
    command = sys.argv[3:]
    if not command:
        print("jobwrap: empty command", file=sys.stderr)
        return 125

    stdout_path = job_dir / "stdout"
    stderr_path = job_dir / "stderr"

    pid = os.fork()
    if pid == 0:
        # Child: become the command. Same process group as the shim, so
        # cancelling the job (killpg) reliably terminates the whole tree.
        try:
            out_fd = os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            err_fd = os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            devnull = os.open(os.devnull, os.O_RDONLY)
            os.dup2(devnull, 0)
            os.dup2(out_fd, 1)
            os.dup2(err_fd, 2)
            if devnull > 2:
                os.close(devnull)
            if out_fd > 2:
                os.close(out_fd)
            if err_fd > 2:
                os.close(err_fd)
            os.execvp(command[0], command)
        except Exception as exc:  # noqa: BLE001 - must never propagate
            sys.stderr.write(f"jobwrap: exec failed: {exc}\n")
            os._exit(127)

    # Parent (shim): record the command pid, then wait and record the exit.
    try:
        (job_dir / "pid").write_text(f"{pid}\n", encoding="ascii")
    except OSError:
        pass

    try:
        _, status = os.waitpid(pid, 0)
        exit_code = os.waitstatus_to_exitcode(status)
    except ChildProcessError:
        exit_code = -1
    except KeyboardInterrupt:
        exit_code = 130

    try:
        (job_dir / "exit").write_text(f"{exit_code}\n", encoding="ascii")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
