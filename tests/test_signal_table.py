"""Critical end-to-end scenario for the external Qt desktop application."""

from __future__ import annotations

import math
from typing import Any

import pytest
from dogtail.tree import SearchError, root

from signal_monitor_qa.support import (
    has_complete_signal_rows,
    parse_number,
    read_table,
    wait_until,
)

pytestmark = [pytest.mark.smoke, pytest.mark.ui, pytest.mark.linux]


def find_table(application: Any, preferred_name: str) -> Any:
    """Find the signals table by stable name, then fall back to its AT-SPI role."""

    try:
        return application.child(name=preferred_name, role_name="table", retry=False)
    except SearchError:
        return application.find_child(
            lambda node: node.role_name in {"table", "tree table"}, retry=False
        )


def test_connect_displays_ten_valid_signals(running_system: dict[str, object]) -> None:
    """Connect to the simulator and verify the application's primary output."""

    timeout = float(running_system["timeout"])
    app_name = str(running_system["app_name"])
    table_name = str(running_system["table_name"])

    # Wait for the external Qt application and its enabled connection control.
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
        retry_exceptions=(SearchError,),
    )

    # Reproduce the user action that starts the TCP data flow.
    connect_button.click()

    # Wait for one complete snapshot instead of asserting against a partially
    # populated table while asynchronous signal updates are still arriving.
    table = wait_until(
        lambda: find_table(application, table_name),
        lambda node: node is not None,
        timeout=timeout,
        description="signals table",
        retry_exceptions=(SearchError,),
    )
    rows = wait_until(
        lambda: read_table(table),
        has_complete_signal_rows,
        timeout=timeout,
        description="exactly 10 signal rows with five columns",
    )

    # Verify the user-visible contract without binding the test to changing
    # sine/random values produced by the simulator.
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
