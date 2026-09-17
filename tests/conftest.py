"""Fixtures and command-line options for the external desktop application."""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import pytest

from signal_monitor_qa.support import is_tcp_port_listening


@dataclass(frozen=True)
class ProcessLogs:
    """Paths to stdout and stderr captured for a launched process."""

    stdout: Path
    stderr: Path


@dataclass(frozen=True)
class ManagedProcess:
    """A child process together with its diagnostics and owned file handles."""

    process: subprocess.Popen[str]
    logs: ProcessLogs
    stdout_file: TextIO
    stderr_file: TextIO


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("signal monitor")
    group.addoption("--app-cmd", help="Command used to start the desktop app")
    group.addoption("--simulator-cmd", help="Command used to start the TCP simulator")
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
    group.addoption(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts"),
        help="Directory for application and simulator logs (default: artifacts)",
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
) -> ManagedProcess:
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

    return ManagedProcess(
        process=process,
        logs=ProcessLogs(stdout_path, stderr_path),
        stdout_file=stdout_file,
        stderr_file=stderr_file,
    )


def _assert_still_running(managed_process: ManagedProcess, process_name: str) -> None:
    """Fail early when a child exits during its short initialization phase."""

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        exit_code = managed_process.process.poll()
        if exit_code is not None:
            pytest.fail(
                f"{process_name} exited with code {exit_code}; "
                f"see {managed_process.logs.stdout} and {managed_process.logs.stderr}",
                pytrace=False,
            )
        time.sleep(0.05)


def _wait_for_simulator(
    managed_process: ManagedProcess,
    *,
    port: int,
    timeout: float,
) -> None:
    """Wait until the simulator listens, failing early if its process exits."""

    if timeout <= 0:
        pytest.fail("Startup timeout must be positive", pytrace=False)

    deadline = time.monotonic() + timeout
    while True:
        exit_code = managed_process.process.poll()
        if exit_code is not None:
            pytest.fail(
                f"Simulator exited with code {exit_code}; "
                f"see {managed_process.logs.stdout} and "
                f"{managed_process.logs.stderr}",
                pytrace=False,
            )
        if is_tcp_port_listening(port):
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.05, remaining))

    pytest.fail(
        f"Simulator did not listen on local TCP port {port} within {timeout:g}s; "
        f"see {managed_process.logs.stdout} and {managed_process.logs.stderr}",
        pytrace=False,
    )


def _stop_process(managed_process: ManagedProcess) -> None:
    """Terminate a child gracefully and always release its log handles."""

    try:
        if managed_process.process.poll() is None:
            managed_process.process.terminate()
            try:
                managed_process.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                managed_process.process.kill()
                managed_process.process.wait(timeout=5)
    finally:
        managed_process.stdout_file.close()
        managed_process.stderr_file.close()


@pytest.fixture
def artifacts_dir(request: pytest.FixtureRequest) -> Path:
    """Create a stable, per-test directory for diagnostic process logs."""

    root = request.config.getoption("--artifacts-dir")
    safe_name = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in request.node.nodeid
    )
    path = root / safe_name
    path.mkdir(parents=True, exist_ok=True)
    for log_name in (
        "application.stdout.log",
        "application.stderr.log",
        "simulator.stdout.log",
        "simulator.stderr.log",
    ):
        (path / log_name).unlink(missing_ok=True)
    return path


@pytest.fixture
def running_system(
    request: pytest.FixtureRequest, artifacts_dir: Path
) -> Iterator[dict[str, object]]:
    """Launch simulator first, then AUT, and tear both down after the test."""

    simulator_cmd = _required_option(request, "--simulator-cmd")
    app_cmd = _required_option(request, "--app-cmd")
    app_name = _required_option(request, "--a11y-app-name")
    simulator_port = request.config.getoption("--simulator-port")
    startup_timeout = request.config.getoption("--startup-timeout")
    if not 1 <= simulator_port <= 65535:
        pytest.fail(
            f"Simulator port must be in range 1..65535, got {simulator_port}",
            pytrace=False,
        )
    if startup_timeout <= 0:
        pytest.fail("Startup timeout must be positive", pytrace=False)

    simulator = _start_process(simulator_cmd, artifacts_dir, "simulator")
    app: ManagedProcess | None = None

    try:
        _wait_for_simulator(
            simulator,
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
        app = _start_process(
            app_cmd,
            artifacts_dir,
            "application",
            environment=app_environment,
        )
        _assert_still_running(app, "Application")

        yield {
            "app_name": app_name,
            "table_name": request.config.getoption("--a11y-table-name"),
            "timeout": startup_timeout,
            "logs": {"application": app.logs, "simulator": simulator.logs},
            "artifacts_dir": artifacts_dir,
        }
    finally:
        # Keep simulator cleanup guaranteed even if stopping the AUT raises.
        try:
            if app is not None:
                _stop_process(app)
        finally:
            _stop_process(simulator)
