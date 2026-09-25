"""Run the simple SSI firmware images with a functional R5F model."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

try:
    from .simulator_adapter import SsiSimulation, firmware_paths
except ImportError:  # direct ``python3 simulate_ssi.py`` invocation
    SSI_TOOLS = Path(__file__).resolve().parent
    if str(SSI_TOOLS) not in sys.path:
        sys.path.insert(0, str(SSI_TOOLS))
    from simulator_adapter import SsiSimulation, firmware_paths


SSI_ROOT = Path(__file__).resolve().parents[1]
SIMULATOR_ROOT = SSI_ROOT.parents[1]
CONFIG_PATH = SSI_ROOT / "ssi_test" / "ssi_hardware_config.h"
PROFILE_PATH = SSI_ROOT / "include" / "ssi_build_config.json"


def _abi(profile: dict[str, int], name: str) -> int:
    return int(profile.get("abi", {}).get(name, {
        "SSI_PRU0_POSITION_OFF": 0x00,
        "SSI_PRU1_PERIOD_OFF": 0x00,
        "SSI_PRU1_ARM_READY_OFF": 0x0C,
        "SSI_PRU1_STOP_OFF": 0x10,
        "SSI_PRU1_TIMER_READY_OFF": 0x14,
        "SSI_PRU1_TIMER_DONE_OFF": 0x18,
        "SSI_PRU1_READER_DONE_OFF": 0x20,
        "SSI_PRU1_TIMER_MISSED_OFF": 0x24,
        "SSI_PRU1_TIMER_MAX_LATE_OFF": 0x28,
        "SSI_PRU1_TIMER_EMITTED_OFF": 0x2C,
        "SSI_PRU1_READER_FRAMES_OFF": 0x40,
        "SSI_PRU1_READER_RAW_LO_OFF": 0x44,
        "SSI_PRU1_READER_RAW_HI_OFF": 0x48,
        "SSI_PRU1_READER_POSITION_OFF": 0x4C,
        "SSI_PRU1_READER_ERRORS_OFF": 0x50,
        "SSI_PRU1_READER_ABORTS_OFF": 0x54,
    }[name]))


class HostModel:
    """Idealized nonblocking R5 publisher called after each simulator step."""

    def __init__(
        self,
        sim: SsiSimulation,
        initial: int,
        step: int,
        mask: int,
        *,
        record_events: bool = True,
        record_position_stores: bool = True,
    ):
        self.sim = sim
        self.record_events = bool(record_events)
        self.record_position_stores = bool(record_position_stores)
        self.step_value = int(step)
        self.mask = int(mask)
        self.last = 0
        self.pending = int(initial) & self.mask
        self.published = 0
        self.skipped = 0
        self.block_until_cycle = 0
        self.position_stores: list[dict] = []

    def poll(self) -> None:
        """Consume a changed PERIOD exactly once and publish one scalar value."""
        if self.sim.wall_cycle < self.block_until_cycle:
            return
        period = self.sim.read32("dram1", _abi(self.sim.profile, "SSI_PRU1_PERIOD_OFF"))
        if period == self.last:
            return
        delta = (period - self.last) & 0xFFFF_FFFF
        self.skipped += delta - 1
        self.sim.write32(
            "dram0",
            _abi(self.sim.profile, "SSI_PRU0_POSITION_OFF"),
            self.pending,
        )
        self.published += 1
        self.pending = (self.pending + self.step_value) & self.mask
        self.last = period
        if self.record_events and self.record_position_stores:
            self.position_stores.append({
                "cycle": self.sim.cycle,
                "period": period,
                "position": (self.pending - self.step_value) & self.mask,
            })

    @property
    def opportunities(self) -> int:
        """Return observed periods as published plus skipped opportunities."""
        return self.published + self.skipped


def _read_word(sim: SsiSimulation, profile: dict[str, int], name: str) -> int:
    return sim.read32("dram1", _abi(profile, name))


def _run_direct(
    profile: dict[str, int],
    iterations: int,
    *,
    host_block_cycles: int = 0,
) -> dict:
    if not 1 <= iterations <= int(profile["SSI_ITERATIONS"]):
        raise ValueError("iterations must be in the range 1..SSI_ITERATIONS")

    sim = SsiSimulation(profile)
    emulator, reader, timer = firmware_paths()
    sim.load(emulator, reader, timer)
    position_mask = int(profile["SSI_POSITION_MASK"])
    host = HostModel(
        sim,
        initial=int(profile["SSI_INITIAL_POSITION"]),
        step=int(profile["SSI_POSITION_STEP"]),
        mask=position_mask,
    )
    # Seed the scalar before ARM_READY, matching the hardware startup order.
    sim.write32("dram0", _abi(profile, "SSI_PRU0_POSITION_OFF"), host.pending)

    startup_limit = 50_000
    for _ in range(startup_limit):
        sim.step(1)
        host.poll()
        if _read_word(sim, profile, "SSI_PRU1_TIMER_READY_OFF") == 1:
            break
    else:
        raise RuntimeError(f"timer did not become ready: {sim.status()}")

    host.block_until_cycle = sim.wall_cycle + max(0, int(host_block_cycles))
    sim.write32("dram1", _abi(profile, "SSI_PRU1_ARM_READY_OFF"), 1)

    stop_sent = False
    max_cycles = sim.wall_cycle + iterations * int(profile["SSI_PERIOD_TICKS"]) + 2_000_000
    while sim.wall_cycle < max_cycles:
        sim.step(1)
        host.poll()
        if host.published >= iterations and not stop_sent:
            sim.write32("dram1", _abi(profile, "SSI_PRU1_STOP_OFF"), 1)
            stop_sent = True
        timer_done = _read_word(sim, profile, "SSI_PRU1_TIMER_DONE_OFF") == 1
        reader_done = _read_word(sim, profile, "SSI_PRU1_READER_DONE_OFF") == 1
        if stop_sent and timer_done and reader_done:
            break
    else:
        raise RuntimeError(f"SSI run did not terminate: {sim.status()}")

    # Compare every complete reader frame with the position observed at the
    # corresponding actual PRU0 LBCO event. This is a functional wire check;
    # the simulator does not model R5 cache/bus ordering or hardware IO timing.
    import_path = SSI_ROOT / "tools" / "generate_config.py"
    spec = importlib.util.spec_from_file_location("ssi_generate_for_run", import_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import the SSI config generator")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    mismatches = []
    for event in sim.events():
        if event.get("kind") != "frame":
            continue
        latched = event.get("latched_position")
        expected = None if latched is None else generator.pack_position_frame(
            latched, int(profile.get("SSI_ERROR_VALUE", 0)), profile
        )
        if expected != event["raw"]:
            mismatches.append({"event": event, "expected": expected})

    events = sim.events()
    timer_emitted = _read_word(sim, profile, "SSI_PRU1_TIMER_EMITTED_OFF")
    timer_missed = _read_word(sim, profile, "SSI_PRU1_TIMER_MISSED_OFF")
    result = {
        "opportunities": host.opportunities,
        "published": host.published,
        "skipped": host.skipped,
        "frames": _read_word(sim, profile, "SSI_PRU1_READER_FRAMES_OFF"),
        "data_mismatches": len(mismatches),
        "deadline_model": "functional_host_no_r5_bus_model",
        "source_hashes": sim.source_hashes,
        "simulator_revision": sim.simulator_revision(),
        "timer_emitted": timer_emitted,
        "timer_missed": timer_missed,
        "timer_max_late": _read_word(sim, profile, "SSI_PRU1_TIMER_MAX_LATE_OFF"),
        "reader_aborts": _read_word(sim, profile, "SSI_PRU1_READER_ABORTS_OFF"),
        "emulator_frames": sim.read32("dram0", 0x0C),
        "emulator_resyncs": sim.read32("dram0", 0x10),
        "emulator_aborts": sim.read32("dram0", 0x14),
        "final_position": _read_word(sim, profile, "SSI_PRU1_READER_POSITION_OFF"),
        "final_errors": _read_word(sim, profile, "SSI_PRU1_READER_ERRORS_OFF"),
        "final_raw_lo": _read_word(sim, profile, "SSI_PRU1_READER_RAW_LO_OFF"),
        "final_raw_hi": _read_word(sim, profile, "SSI_PRU1_READER_RAW_HI_OFF"),
        "simulator_wall_cycles": sim.wall_cycle,
        "simulator_iep_ticks": sim.cycle,
        "event_count": len(events),
        "capture": events[:2000],
    }
    if mismatches:
        result["mismatch_examples"] = mismatches[:8]
    return result


async def _call_mcp(iterations: int) -> dict:
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        raise RuntimeError("MCP SDK is not installed; install the project dependencies from requirements.txt") from exc

    server = SIMULATOR_ROOT / "mcp_server" / "server.py"
    env = os.environ.copy()
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(server)],
        cwd=str(SIMULATOR_ROOT),
        env=env,
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await asyncio.wait_for(session.initialize(), timeout=15)
            tools = await asyncio.wait_for(session.list_tools(), timeout=15)
            names = {tool.name for tool in tools.tools}
            if "pru_ssi_simple_run" not in names:
                raise RuntimeError("MCP server did not advertise pru_ssi_simple_run")
            # The actual three-core firmware run is instruction-by-instruction
            # and therefore takes several minutes at 100000 opportunities on
            # this functional simulator. Keep the transport timeout above the
            # measured direct-run duration instead of turning a valid run into
            # an MCP-only timeout.
            tool_timeout = max(300, int(iterations * 0.01) + 60)
            response = await asyncio.wait_for(
                session.call_tool("pru_ssi_simple_run", {"iterations": iterations}),
                timeout=tool_timeout,
            )
            if getattr(response, "isError", False):
                raise RuntimeError(str(response))
            payloads = [item.text for item in response.content if hasattr(item, "text")]
            if not payloads:
                raise RuntimeError("MCP tool returned no structured text result")
            return json.loads(payloads[-1])


def run(profile: dict[str, int], iterations: int, use_mcp: bool = False) -> dict:
    """Run actual SSI firmware directly or through the MCP transport."""
    if use_mcp:
        return asyncio.run(_call_mcp(iterations))
    return _run_direct(profile, iterations)


def load_default_profile() -> dict[str, int]:
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=100_000)
    parser.add_argument("--mcp", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(load_default_profile(), args.iterations, use_mcp=args.mcp)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
