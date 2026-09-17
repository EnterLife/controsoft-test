"""Small deterministic helpers shared by the E2E test and unit tests."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TypeVar


T = TypeVar("T")


def wait_until(
    action: Callable[[], T],
    condition: Callable[[T], bool],
    *,
    timeout: float,
    description: str,
    interval: float = 0.2,
    retry_exceptions: tuple[type[Exception], ...] = (),
) -> T:
    """Poll until a condition succeeds and report the last observable state."""

    if timeout <= 0:
        raise ValueError("Wait timeout must be positive")
    if interval <= 0:
        raise ValueError("Wait interval must be positive")

    deadline = time.monotonic() + timeout
    last_value: T | None = None
    last_error: Exception | None = None

    while True:
        try:
            last_value = action()
        except retry_exceptions as error:
            last_error = error
        else:
            if condition(last_value):
                return last_value

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(interval, remaining))

    details = f" Last value: {last_value!r}."
    if last_error is not None:
        details += f" Last retryable error: {last_error!r}."
    raise AssertionError(f"Timed out waiting for {description}.{details}")


def read_table(table: Any) -> list[list[str]]:
    """Read visible cells through the Dogtail/AT-SPI table interface."""

    rows: list[list[str]] = []
    for row_index in range(table.get_n_rows()):
        row: list[str] = []
        for column_index in range(table.get_n_columns()):
            cell = table.get_accessible_at(row_index, column_index)
            value = (getattr(cell, "name", "") or "").strip()
            if not value:
                value = (getattr(cell, "text", "") or "").strip()
            row.append(value)
        rows.append(row)
    return rows


def parse_number(raw_value: str) -> float:
    """Accept decimal point or comma, as both are common in a Russian locale."""

    return float(raw_value.strip().replace(" ", "").replace(",", "."))


def has_complete_signal_rows(rows: list[list[str]]) -> bool:
    """Return true only after all 10 rows contain complete, unique signal data."""

    if len(rows) != 10 or any(len(row) < 5 for row in rows):
        return False

    signal_ids: list[str] = []
    for signal_id, name, raw_value, quality, timestamp, *_ in rows:
        normalized_id = signal_id.strip()
        if not all(
            field.strip() for field in (normalized_id, name, quality, timestamp)
        ):
            return False
        try:
            if not math.isfinite(parse_number(raw_value)):
                return False
        except ValueError:
            return False
        signal_ids.append(normalized_id)
    return len(set(signal_ids)) == 10


def parse_listening_tcp_ports(table_text: str) -> set[int]:
    """Extract LISTEN ports from Linux ``/proc/net/tcp*`` content."""

    listening_ports: set[int] = set()
    for line in table_text.splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[3] != "0A":
            continue
        local_address = fields[1]
        if ":" not in local_address:
            continue
        try:
            listening_ports.add(int(local_address.rsplit(":", maxsplit=1)[-1], 16))
        except ValueError:
            continue
    return listening_ports


def is_tcp_port_listening(
    port: int,
    table_paths: Iterable[Path] = (Path("/proc/net/tcp"), Path("/proc/net/tcp6")),
) -> bool:
    """Check Linux TCP tables without opening a connection to the simulator."""

    if not 1 <= port <= 65535:
        raise ValueError(f"TCP port must be in range 1..65535, got {port}")

    for table_path in table_paths:
        try:
            table_text = table_path.read_text(encoding="ascii")
        except OSError:
            continue
        if port in parse_listening_tcp_ports(table_text):
            return True
    return False
