"""Critical end-to-end scenario for the external Qt desktop application."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any, TypeVar

from dogtail.tree import root


T = TypeVar("T")


def wait_until(
    action: Callable[[], T],
    condition: Callable[[T], bool],
    *,
    timeout: float,
    description: str,
) -> T:
    """Poll a UI state until it is ready, avoiding a fixed synchronization sleep."""

    deadline = time.monotonic() + timeout
    last_value: T | None = None
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            last_value = action()
            if condition(last_value):
                return last_value
        except Exception as error:  # UI nodes may not exist during startup.
            last_error = error
        time.sleep(0.2)

    details = f" Last value: {last_value!r}."
    if last_error is not None:
        details += f" Last UI error: {last_error!r}."
    raise AssertionError(f"Timed out waiting for {description}.{details}")


def find_table(application: Any, preferred_name: str) -> Any:
    """Find the signals table by stable name, then fall back to its AT-SPI role."""

    try:
        return application.child(
            name=preferred_name, role_name="table", retry=False
        )
    except Exception:
        return application.find_child(
            lambda node: node.role_name in {"table", "tree table"}, retry=False
        )


def read_table(table: Any) -> list[list[str]]:
    """Read visible cells through the standard AT-SPI table interface."""

    rows: list[list[str]] = []
    for row_index in range(table.get_n_rows()):
        row: list[str] = []
        for column_index in range(table.get_n_columns()):
            cell = table.get_accessible_at(row_index, column_index)
            # Qt commonly exposes cell text as the accessible name. The text
            # fallback also supports implementations that expose a text object.
            value = (getattr(cell, "name", "") or "").strip()
            if not value:
                try:
                    value = (cell.text or "").strip()
                except Exception:
                    value = ""
            row.append(value)
        rows.append(row)
    return rows


def parse_number(raw_value: str) -> float:
    """Accept decimal point or comma, as both are common in a Russian locale."""

    return float(raw_value.strip().replace(" ", "").replace(",", "."))


def has_complete_signal_rows(rows: list[list[str]]) -> bool:
    """Return true only after all 10 rows contain usable values."""

    if len(rows) != 10 or any(len(row) < 5 for row in rows):
        return False

    for signal_id, name, raw_value, quality, timestamp, *_ in rows:
        if not all(field.strip() for field in (signal_id, name, quality, timestamp)):
            return False
        try:
            if not math.isfinite(parse_number(raw_value)):
                return False
        except ValueError:
            return False
    return True


def test_connect_displays_ten_valid_signals(running_system: dict[str, object]) -> None:
    """Connect to the simulator and verify the application's primary output."""

    timeout = float(running_system["timeout"])
    app_name = str(running_system["app_name"])
    table_name = str(running_system["table_name"])

    application = wait_until(
        lambda: next(
            (node for node in root.applications() if node.name == app_name), None
        ),
        lambda node: node is not None,
        timeout=timeout,
        description=f"AT-SPI application {app_name!r}",
    )
    connect_button = wait_until(
        lambda: application.child(
            name="Подключиться", role_name="push button", retry=False
        ),
        lambda node: node.showing and node.sensitive,
        timeout=timeout,
        description="enabled Connect button",
    )

    connect_button.click()

    table = wait_until(
        lambda: find_table(application, table_name),
        lambda node: node is not None,
        timeout=timeout,
        description="signals table",
    )
    rows = wait_until(
        lambda: read_table(table),
        has_complete_signal_rows,
        timeout=timeout,
        description="exactly 10 signal rows with five columns",
    )

    signal_ids: list[str] = []
    for row_number, row in enumerate(rows, start=1):
        signal_id, name, raw_value, quality, timestamp = row[:5]

        assert signal_id.strip(), f"Row {row_number}: signal ID is empty"
        signal_ids.append(signal_id.strip())
        assert name.strip(), f"Row {row_number}: signal name is empty"
        assert quality.strip(), f"Row {row_number}: quality is empty"
        assert timestamp.strip(), f"Row {row_number}: timestamp is empty"

        numeric_value = parse_number(raw_value)
        assert math.isfinite(numeric_value), (
            f"Row {row_number}: value must be finite, got {raw_value!r}"
        )

    assert len(set(signal_ids)) == 10, (
        f"Expected 10 unique signal IDs, got {signal_ids!r}"
    )
