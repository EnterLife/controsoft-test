from __future__ import annotations

from pathlib import Path

import pytest

from signal_monitor_qa import support
from signal_monitor_qa.support import (
    has_complete_signal_rows,
    is_tcp_port_listening,
    parse_listening_tcp_ports,
    parse_number,
    read_table,
    wait_until,
)


def valid_rows() -> list[list[str]]:
    return [
        [str(index), f"Signal {index}", f"{index},5", "GOOD", "2026-09-17 10:00:00"]
        for index in range(10)
    ]


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [("12.5", 12.5), ("12,5", 12.5), ("1 234,5", 1234.5), ("-1", -1.0)],
)
def test_parse_number_accepts_supported_formats(
    raw_value: str, expected: float
) -> None:
    assert parse_number(raw_value) == expected


def test_parse_number_rejects_non_numeric_value() -> None:
    with pytest.raises(ValueError):
        parse_number("not-a-number")


def test_read_table_uses_name_and_text_fallback() -> None:
    class Cell:
        def __init__(self, *, name: str = "", text: str = "") -> None:
            self.name = name
            self.text = text

    class Table:
        cells = [[Cell(name="1"), Cell(text="Temperature")]]

        def get_n_rows(self) -> int:
            return len(self.cells)

        def get_n_columns(self) -> int:
            return len(self.cells[0])

        def get_accessible_at(self, row: int, column: int) -> Cell:
            return self.cells[row][column]

    assert read_table(Table()) == [["1", "Temperature"]]


def test_complete_rows_require_ten_unique_signals() -> None:
    rows = valid_rows()

    assert has_complete_signal_rows(rows)
    assert not has_complete_signal_rows(rows[:9])

    rows[-1][0] = rows[0][0]
    assert not has_complete_signal_rows(rows)


@pytest.mark.parametrize("invalid_value", ["", "NaN", "Inf", "unknown"])
def test_complete_rows_reject_invalid_values(invalid_value: str) -> None:
    rows = valid_rows()
    rows[0][2] = invalid_value

    assert not has_complete_signal_rows(rows)


def test_parse_listening_tcp_ports_uses_only_listen_state() -> None:
    table = """\
  sl  local_address rem_address   st tx_queue rx_queue
   0: 0100007F:07D1 00000000:0000 0A 00000000:00000000
   1: 0100007F:1F90 0100007F:ABCD 01 00000000:00000000
malformed row
"""

    assert parse_listening_tcp_ports(table) == {2001}


def test_is_tcp_port_listening_reads_available_tables(tmp_path: Path) -> None:
    missing_table = tmp_path / "missing"
    tcp_table = tmp_path / "tcp"
    tcp_table.write_text(
        "sl local_address rem_address st\n0: 00000000:07D1 00000000:0000 0A\n",
        encoding="ascii",
    )

    assert is_tcp_port_listening(2001, (missing_table, tcp_table))
    assert not is_tcp_port_listening(2002, (missing_table, tcp_table))


def test_is_tcp_port_listening_rejects_invalid_port() -> None:
    with pytest.raises(ValueError, match="1..65535"):
        is_tcp_port_listening(0, ())


def test_wait_until_retries_declared_error(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = iter((RuntimeError("not ready"), "ready"))

    def action() -> str:
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(support.time, "sleep", lambda _: None)

    assert (
        wait_until(
            action,
            lambda value: value == "ready",
            timeout=1,
            interval=0.01,
            description="ready value",
            retry_exceptions=(RuntimeError,),
        )
        == "ready"
    )


def test_wait_until_reports_last_value(monkeypatch: pytest.MonkeyPatch) -> None:
    timestamps = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(support.time, "monotonic", lambda: next(timestamps))
    monkeypatch.setattr(support.time, "sleep", lambda _: None)

    with pytest.raises(AssertionError, match="Last value: 'starting'"):
        wait_until(
            lambda: "starting",
            lambda value: value == "ready",
            timeout=0.5,
            interval=0.1,
            description="ready value",
        )


def test_wait_until_does_not_hide_unexpected_error() -> None:
    def action() -> bool:
        raise KeyError("programming error")

    with pytest.raises(KeyError, match="programming error"):
        wait_until(
            action,
            bool,
            timeout=1,
            description="successful action",
            retry_exceptions=(RuntimeError,),
        )


@pytest.mark.parametrize(("timeout", "interval"), [(0, 0.1), (1, 0)])
def test_wait_until_rejects_non_positive_limits(
    timeout: float, interval: float
) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        wait_until(
            lambda: True,
            bool,
            timeout=timeout,
            interval=interval,
            description="valid limits",
        )
