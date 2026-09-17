"""Fixtures and command-line options for the external desktop application."""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass(frozen=True)
class ProcessLogs:
    """Paths to stdout and stderr captured for a launched process."""

    stdout: Path
    stderr: Path


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("signal monitor")
    group.addoption("--app-cmd", help="Command used to start the desktop app")
    group.addoption(
        "--simulator-cmd", help="Command used to start the TCP simulator"
    )
    group.addoption(
        "--simulator-port",
        type=int,
        default=2001,
        help="Local TCP port exposed by the simulator (default: 2001)",
    )
    group.addoption(
        "--a11y-app-name",
        help="Application name exposed through AT-SPI (for example, Signal Monitor)",
    )
    group.addoption(
        "--a11y-table-name",
        default="Таблица сигналов",
        help="Accessible table name; role-based lookup is used as a fallback",
    )
    group.addoption(
        "--startup-timeout",
        type=float,
        default=15.0,
        help="Maximum wait for process/UI startup in seconds (default: 15)",
    )


def _required_option(request: pytest.FixtureRequest, name: str) -> str:
    value = request.config.getoption(name)
    if not value:
        pytest.fail(f"Required option {name} was not provided", pytrace=False)
    return value


def _start_process(
    command: str,
    log_dir: Path,
    process_name: str,
    *,
    environment: dict[str, str] | None = None,
) -> tuple[subprocess.Popen[str], ProcessLogs]:
    """Start a process without a shell and redirect output to diagnostic files."""

    stdout_path = log_dir / f"{process_name}.stdout.log"
    stderr_path = log_dir / f"{process_name}.stderr.log"
    stdout_file = stdout_path.open("w", encoding="utf-8")
    stderr_file = stderr_path.open("w", encoding="utf-8")

    try:
        process = subprocess.Popen(
            shlex.split(command),
            stdout=stdout_file,
            stderr=stderr_file,
            text=True,
            env=environment,
            start_new_session=True,
        )
    except Exception:
        stdout_file.close()
        stderr_file.close()
        raise

    # Keep references so the files remain open while the child process writes.
    process._qa_log_files = (stdout_file, stderr_file)  # type: ignore[attr-defined]
    return process, ProcessLogs(stdout_path, stderr_path)


def _assert_still_running(
    process: subprocess.Popen[str], process_name: str, logs: ProcessLogs
) -> None:
    """Fail early when a child exits during its short initialization phase."""

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            pytest.fail(
                f"{process_name} exited with code {exit_code}; "
                f"see {logs.stdout} and {logs.stderr}",
                pytrace=False,
            )
        time.sleep(0.05)


def _is_tcp_port_listening(port: int) -> bool:
    """Check Linux TCP tables without opening a connection to the simulator."""

    expected_port = f"{port:04X}"
    for table_path in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            lines = table_path.read_text(encoding="ascii").splitlines()[1:]
        except OSError:
            continue

        for line in lines:
            fields = line.split()
            if len(fields) < 4:
                continue
            local_address, connection_state = fields[1], fields[3]
            local_port = local_address.rsplit(":", maxsplit=1)[-1]
            if connection_state == "0A" and local_port.upper() == expected_port:
                return True
    return False


def _wait_for_simulator(
    process: subprocess.Popen[str],
    logs: ProcessLogs,
    *,
    port: int,
    timeout: float,
) -> None:
    """Wait until the simulator listens, failing early if its process exits."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            pytest.fail(
                f"Simulator exited with code {exit_code}; "
                f"see {logs.stdout} and {logs.stderr}",
                pytrace=False,
            )
        if _is_tcp_port_listening(port):
            return
        time.sleep(0.05)

    pytest.fail(
        f"Simulator did not listen on local TCP port {port} within {timeout:g}s; "
        f"see {logs.stdout} and {logs.stderr}",
        pytrace=False,
    )


def _stop_process(process: subprocess.Popen[str]) -> None:
    """Terminate a child gracefully and always release its log handles."""

    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    for log_file in getattr(process, "_qa_log_files", ()):
        log_file.close()


@pytest.fixture
def running_system(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[dict[str, object]]:
    """Launch simulator first, then AUT, and tear both down after the test."""

    simulator_cmd = _required_option(request, "--simulator-cmd")
    app_cmd = _required_option(request, "--app-cmd")
    app_name = _required_option(request, "--a11y-app-name")
    simulator_port = request.config.getoption("--simulator-port")
    startup_timeout = request.config.getoption("--startup-timeout")

    simulator, simulator_logs = _start_process(
        simulator_cmd, tmp_path, "simulator"
    )
    app: subprocess.Popen[str] | None = None

    try:
        _wait_for_simulator(
            simulator,
            simulator_logs,
            port=simulator_port,
            timeout=startup_timeout,
        )

        app_environment = os.environ.copy()
        app_environment.update(
            {
                "QT_ACCESSIBILITY": "1",
                "QT_LINUX_ACCESSIBILITY_ALWAYS_ON": "1",
            }
        )
        app, app_logs = _start_process(
            app_cmd,
            tmp_path,
            "application",
            environment=app_environment,
        )
        _assert_still_running(app, "Application", app_logs)

        yield {
            "app_name": app_name,
            "table_name": request.config.getoption("--a11y-table-name"),
            "timeout": startup_timeout,
            "logs": {"application": app_logs, "simulator": simulator_logs},
        }
    finally:
        # Keep simulator cleanup guaranteed even if stopping the AUT raises.
        try:
            if app is not None:
                _stop_process(app)
        finally:
            _stop_process(simulator)
