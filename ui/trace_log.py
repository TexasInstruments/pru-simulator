"""Disk-backed trace sessions for the PRU simulator dashboard."""

from __future__ import annotations

import csv
import os
import re
import tempfile
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence


TRACE_RETENTION_SECONDS = 24 * 60 * 60
MAX_PAGE_SAMPLES = 50_000
_SESSION_ID_RE = re.compile(r"^[0-9a-f]{32}$")

TRACE_LOG_HEADER = (
    "step", "core", "mode",
    *(f"gpo{i}" for i in range(20)),
    *(f"gpi{i}" for i in range(20)),
    *(f"perif{i}_out" for i in range(3)),
    *(f"perif{i}_out_en" for i in range(3)),
    *(f"perif{i}_clk" for i in range(3)),
    "run_step", "captured_at_ms", "sequence",
)


class TraceLogError(RuntimeError):
    """Raised when a trace session cannot accept or persist a batch."""


def _bits(value: int, count: int) -> list[int]:
    return [(int(value) >> index) & 1 for index in range(count)]


def _packed_row(core: str, mode: str, packed: Sequence[int],
                captured_at_ms: float, sequence: int) -> list:
    if len(packed) < 7:
        raise TraceLogError("capture sample must contain seven packed values")
    step, r30, gpi, perif, oe, clk, run_step = packed[:7]
    return [
        int(step), str(core), str(mode),
        *_bits(r30, 20), *_bits(gpi, 20),
        *_bits(perif, 3), *_bits(oe, 3), *_bits(clk, 3),
        int(run_step if run_step is not None else step),
        float(captured_at_ms), int(sequence),
    ]


def _page_sample(row: dict[str, str]) -> dict:
    def integer(name: str) -> int:
        return int(row[name])

    gpo = sum(integer(f"gpo{i}") << i for i in range(20))
    gpi = sum(integer(f"gpi{i}") << i for i in range(20))
    perif = sum(integer(f"perif{i}_out") << i for i in range(3))
    oe = sum(integer(f"perif{i}_out_en") << i for i in range(3))
    clk = sum(integer(f"perif{i}_clk") << i for i in range(3))
    return {
        "step": integer("step"),
        "core": row["core"],
        "mode": row["mode"],
        "gpo_bits": gpo,
        "gpi_bits": gpi,
        "perif_bits": perif,
        "perif_oe_bits": oe,
        "perif_clk_bits": clk,
        "run_step": integer("run_step"),
        "captured_at_ms": float(row["captured_at_ms"]),
        "sequence": integer("sequence"),
    }


def _observed_values(packed: Sequence[int]) -> Iterable[tuple[str, int, int]]:
    if len(packed) < 7:
        raise TraceLogError("capture sample must contain seven packed values")
    _, r30, gpi, perif, oe, clk, _ = packed[:7]
    for pin, value in enumerate(_bits(r30, 20)):
        yield "gpo", pin, value
    for pin, value in enumerate(_bits(gpi, 20)):
        yield "gpi", pin, value
    for pin, value in enumerate(_bits(perif, 3)):
        yield "perif_out", pin, value
    for pin, value in enumerate(_bits(oe, 3)):
        yield "perif_oe", pin, value
    for pin, value in enumerate(_bits(clk, 3)):
        yield "perif_clk", pin, value


class TraceLogSession:
    """One append-only CSV and its lightweight in-memory indexes."""

    def __init__(self, path: Path, session_id: str, owner: str,
                 now: Callable[[], float]) -> None:
        self.path = path
        self.session_id = session_id
        self.owner = owner
        self._now = now
        self.started_at = datetime.fromtimestamp(
            now(), timezone.utc
        ).isoformat()
        self.active = True
        self.sample_count = 0
        self._next_sequence = 0
        self._batch_index: list[dict[str, int]] = []
        self._observed: set[tuple[str, str, str, int]] = set()
        self._last_values: dict[tuple[str, str, str, int], int] = {}
        self._last_modes: dict[str, str] = {}
        self._lock = threading.RLock()
        try:
            self._file = path.open("w", encoding="utf-8", newline="")
            self._writer = csv.writer(self._file, lineterminator="\n")
            self._writer.writerow(TRACE_LOG_HEADER)
            self._file.flush()
        except OSError as exc:
            raise TraceLogError(f"could not create trace log: {exc}") from exc

    def state(self) -> dict:
        with self._lock:
            return {
                "active": self.active,
                "session_id": self.session_id,
                "sample_count": self.sample_count,
                "started_at": self.started_at,
                "download_url": None,
            }

    def append(self, batches: Sequence[dict]) -> dict:
        with self._lock:
            if not self.active:
                raise TraceLogError("trace log session is already finalized")

            prepared = []
            for batch in batches:
                core = str(batch.get("core", "pru0"))
                mode = str(batch.get("mode", "gpio"))
                samples = list(batch.get("samples", []))
                times = list(batch.get("captured_at_ms", []))
                for index, packed in enumerate(samples):
                    if len(packed) < 7:
                        raise TraceLogError(
                            "capture sample must contain seven packed values"
                        )
                    step = packed[6] if packed[6] is not None else packed[0]
                    captured_at_ms = times[index] if index < len(times) else step
                    prepared.append({
                        "core": core,
                        "mode": mode,
                        "packed": packed,
                        "run_step": int(step),
                        "captured_at_ms": float(captured_at_ms),
                    })

            prepared.sort(key=lambda item: (item["run_step"], item["core"]))
            if not prepared:
                return {"sequences": [], "sample_count": self.sample_count}

            first_sequence = self._next_sequence
            rows = []
            for item in prepared:
                sequence = self._next_sequence
                self._next_sequence += 1
                rows.append(_packed_row(
                    item["core"], item["mode"], item["packed"],
                    item["captured_at_ms"], sequence,
                ))

            try:
                offset = self._file.tell()
                self._writer.writerows(rows)
                self._file.flush()
            except (OSError, ValueError, csv.Error) as exc:
                self.active = False
                try:
                    self._file.close()
                except OSError:
                    pass
                raise TraceLogError(f"could not write trace log: {exc}") from exc

            self._batch_index.append({
                "offset": offset,
                "row_count": len(rows),
                "first_sequence": first_sequence,
                "last_sequence": self._next_sequence - 1,
            })
            for item in prepared:
                core = item["core"]
                mode = item["mode"]
                self._last_modes[core] = mode
                for signal_type, pin, value in _observed_values(item["packed"]):
                    key = (core, mode, signal_type, pin)
                    previous = self._last_values.get(key)
                    if previous is not None and previous != value:
                        self._observed.add(key)
                    self._last_values[key] = value
            self.sample_count = self._next_sequence
            return {
                "sequences": list(range(first_sequence, self._next_sequence)),
                "sample_count": self.sample_count,
            }

    def finalize(self) -> dict:
        with self._lock:
            if self.active:
                try:
                    self._file.flush()
                    self._file.close()
                except OSError as exc:
                    self.active = False
                    raise TraceLogError(f"could not finalize trace log: {exc}") from exc
                self.active = False
            return self.state()

    def observed_channels(self) -> list[dict]:
        with self._lock:
            return [
                {"core": core, "mode": mode, "type": signal_type, "pin": pin}
                for core, mode, signal_type, pin in sorted(self._observed)
            ]

    def last_mode(self, core: str) -> str | None:
        with self._lock:
            return self._last_modes.get(str(core))

    def _read_indexed_row(self, stream, entry: dict[str, int]):
        stream.seek(entry["offset"])
        reader = csv.reader(stream)
        for _ in range(entry["row_count"]):
            values = next(reader)
            yield dict(zip(TRACE_LOG_HEADER, values))

    def page(self, *, before: int | None = None, after: int | None = None,
             limit: int = 1000) -> dict:
        if (before is None) == (after is None):
            raise ValueError("provide exactly one of before or after")
        limit = int(limit)
        if limit < 1 or limit > MAX_PAGE_SAMPLES:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SAMPLES}")

        with self._lock:
            if self.active:
                self._file.flush()
            with self.path.open("r", encoding="utf-8", newline="") as stream:
                if before is not None:
                    candidates = deque(maxlen=limit + 1)
                    for entry in self._batch_index:
                        if entry["first_sequence"] >= int(before):
                            break
                        for row in self._read_indexed_row(stream, entry):
                            if int(row["sequence"]) < int(before):
                                candidates.append(row)
                    has_more = len(candidates) > limit
                    selected = list(candidates)[-limit:]
                else:
                    candidates = []
                    for entry in self._batch_index:
                        if entry["last_sequence"] <= int(after):
                            continue
                        for row in self._read_indexed_row(stream, entry):
                            if int(row["sequence"]) > int(after):
                                candidates.append(row)
                                if len(candidates) > limit:
                                    break
                        if len(candidates) > limit:
                            break
                    has_more = len(candidates) > limit
                    selected = candidates[:limit]

        samples = [_page_sample(row) for row in selected]
        first = samples[0]["sequence"] if samples else None
        last = samples[-1]["sequence"] if samples else None
        return {
            "session_id": self.session_id,
            "samples": samples,
            "first_sequence": first,
            "last_sequence": last,
            "before": (first - 1 if first is not None else before),
            "after": (last + 1 if last is not None else after),
            "has_more": has_more,
            "observed_channels": self.observed_channels(),
        }


class TraceLogManager:
    """Own active sessions and retain finalized files for a short period."""

    def __init__(self, directory: str | os.PathLike | None = None,
                 now: Callable[[], float] = time.time) -> None:
        self.directory = Path(directory) if directory is not None else (
            Path(tempfile.gettempdir()) / "pru-simulator-traces"
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self._now = now
        self._sessions: dict[str, TraceLogSession] = {}
        self._owners: dict[str, str] = {}
        self._lock = threading.RLock()

    def start(self, owner: str) -> dict:
        with self._lock:
            current_id = self._owners.get(owner)
            if current_id is not None:
                raise TraceLogError("a trace log is already active for this WebSocket")
            session_id = uuid.uuid4().hex
            session = TraceLogSession(
                self.directory / f"trace-{session_id}.csv",
                session_id, owner, self._now,
            )
            self._sessions[session_id] = session
            self._owners[owner] = session_id
            return session.state()

    def append(self, owner: str, batches: Sequence[dict]) -> dict | None:
        with self._lock:
            session_id = self._owners.get(owner)
            if session_id is None:
                return None
            return self._sessions[session_id].append(batches)

    def is_active(self, owner: str) -> bool:
        with self._lock:
            return owner in self._owners

    def last_mode(self, owner: str, core: str) -> str | None:
        with self._lock:
            session_id = self._owners.get(owner)
            if session_id is None:
                return None
            return self._sessions[session_id].last_mode(core)

    def stop(self, owner: str) -> dict | None:
        with self._lock:
            session_id = self._owners.pop(owner, None)
            if session_id is None:
                return None
            return self._sessions[session_id].finalize()

    def disconnect(self, owner: str) -> dict | None:
        return self.stop(owner)

    def session(self, session_id: str) -> TraceLogSession:
        with self._lock:
            if not _SESSION_ID_RE.fullmatch(session_id):
                raise KeyError(session_id)
            try:
                return self._sessions[session_id]
            except KeyError:
                path = self.directory / f"trace-{session_id}.csv"
                if not path.is_file():
                    raise
                raise KeyError(session_id)

    def page(self, session_id: str, *, before: int | None = None,
             after: int | None = None, limit: int = 1000) -> dict:
        return self.session(session_id).page(before=before, after=after, limit=limit)

    def file_path(self, session_id: str) -> Path:
        session = self.session(session_id)
        return session.path

    def cleanup(self, now: float | None = None) -> None:
        cutoff = self._now() if now is None else float(now)
        with self._lock:
            for path in self.directory.glob("trace-*.csv"):
                try:
                    if cutoff - path.stat().st_mtime <= TRACE_RETENTION_SECONDS:
                        continue
                    session_id = path.stem.removeprefix("trace-")
                    if session_id in self._owners.values():
                        continue
                    path.unlink()
                    self._sessions.pop(session_id, None)
                except OSError:
                    continue
