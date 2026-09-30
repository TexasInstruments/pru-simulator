"""Tokens, models, wall-clock and interaction log for DEVELOPMENT_REPORT.md.

Reads the Claude Code transcripts of the development session:
  <projects>/<session>.jsonl                     main session
  <projects>/<session>/subagents/agent-*.jsonl   one file per subagent

  python3 source/pif_eth_100/session_stats.py                 # markdown to stdout
  python3 source/pif_eth_100/session_stats.py --json out.json

Token counting: assistant records with message.usage, de-duplicated by
message.id (a streamed reply is written as several records repeating the
usage; per id the max of each counter is kept). Wall-clock per transcript =
first to last record timestamp. The output is a snapshot at run time.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects" / "-home-thomas-GitHub-pru-simulator"
SESSION_ID = "8735618c-d49c-4b36-b197-901036049257"
SESSION_START = "2026-09-30T13:19:49+02:00"
USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens",
              "cache_read_input_tokens", "output_tokens")


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@dataclass
class TranscriptStats:
    name: str
    path: str
    first_ts: str | None = None
    last_ts: str | None = None
    first_prompt: str = ""
    usage: dict[str, dict[str, int]] = field(default_factory=dict)
    assistant_messages: int = 0

    @property
    def wall_s(self) -> float:
        if not self.first_ts or not self.last_ts:
            return 0.0
        return (_ts(self.last_ts) - _ts(self.first_ts)).total_seconds()

    def total(self, key: str) -> int:
        return sum(u.get(key, 0) for u in self.usage.values())


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content
                         if isinstance(c, dict) and c.get("type") == "text")
    return ""


def _is_tool_result(content) -> bool:
    return isinstance(content, list) and any(
        isinstance(c, dict) and c.get("type") == "tool_result" for c in content)


def read_transcript(path: Path, name: str) -> tuple[TranscriptStats, list[dict]]:
    """Return (stats, human messages [{"timestamp", "text"}])."""
    st = TranscriptStats(name=name, path=str(path))
    per_id: dict[str, tuple[str, dict[str, int]]] = {}
    human: list[dict] = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("timestamp")
            if isinstance(ts, str):
                if st.first_ts is None or _ts(ts) < _ts(st.first_ts):
                    st.first_ts = ts
                if st.last_ts is None or _ts(ts) > _ts(st.last_ts):
                    st.last_ts = ts
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            if rec.get("type") == "assistant" and isinstance(msg.get("usage"), dict):
                mid = msg.get("id") or rec.get("requestId") or rec.get("uuid")
                model = msg.get("model", "unknown")
                prev = per_id.get(mid, (model, dict.fromkeys(USAGE_KEYS, 0)))[1]
                per_id[mid] = (model, {k: max(prev[k], int(msg["usage"].get(k) or 0))
                                       for k in USAGE_KEYS})
            elif (rec.get("type") == "user" and not rec.get("isMeta")
                  and not _is_tool_result(msg.get("content"))):
                text = _text(msg.get("content")).strip()
                if text and not text.startswith("<"):      # skip tags/notifications
                    human.append({"timestamp": ts, "text": text})
                    if not st.first_prompt:
                        st.first_prompt = text
    for model, counts in per_id.values():
        if not any(counts.values()):
            continue                                        # synthetic/zero records
        agg = st.usage.setdefault(model, dict.fromkeys(USAGE_KEYS, 0))
        for k in USAGE_KEYS:
            agg[k] += counts[k]
    st.assistant_messages = len(per_id)
    return st, human


def collect(projects_dir: Path = PROJECTS_DIR, session_id: str = SESSION_ID):
    projects_dir = Path(projects_dir)
    main, human = read_transcript(projects_dir / f"{session_id}.jsonl", "main")
    agents = []
    for p in sorted((projects_dir / session_id / "subagents").glob("agent-*.jsonl")):
        st, _ = read_transcript(p, p.stem.removeprefix("agent-"))
        agents.append(st)
    agents.sort(key=lambda s: _ts(s.first_ts) if s.first_ts else datetime.max.astimezone())
    return main, agents, human


def render_markdown(main: TranscriptStats, agents: list[TranscriptStats],
                    human: list[dict]) -> str:
    everyone = [main, *agents]
    last = max((s.last_ts for s in everyone if s.last_ts), key=_ts, default="-")
    out = [f"Session start {SESSION_START}; transcript cut-off {last} (UTC).", "",
           "### Tokens per model", "",
           "| Model | Input | Cache write | Cache read | Output | Total |",
           "|---|---:|---:|---:|---:|---:|"]
    totals: dict[str, dict[str, int]] = {}
    for s in everyone:
        for model, u in s.usage.items():
            t = totals.setdefault(model, dict.fromkeys(USAGE_KEYS, 0))
            for k in USAGE_KEYS:
                t[k] += u[k]
    for model, t in sorted(totals.items()):
        out.append(f"| {model} | {t['input_tokens']:,} | {t['cache_creation_input_tokens']:,} "
                   f"| {t['cache_read_input_tokens']:,} | {t['output_tokens']:,} "
                   f"| {sum(t.values()):,} |")
    out += ["", "### Per agent (wall-clock = first to last record)", "",
            "| Agent | Model(s) | Start (UTC) | Wall-clock | Total tokens | Task (first prompt, 90 chars) |",
            "|---|---|---|---:|---:|---|"]
    for s in everyone:
        models = ", ".join(sorted(s.usage)) or "-"
        tot = sum(s.total(k) for k in USAGE_KEYS)
        task = s.first_prompt.replace("\n", " ").replace("|", "/")[:90]
        out.append(f"| {s.name} | {models} | {s.first_ts or '-'} | {s.wall_s / 60:.1f} min "
                   f"| {tot:,} | {task} |")
    out += ["", "### Interaction log (user messages in the main session)", ""]
    for h in human:
        out.append(f"- **{h['timestamp']}** — {h['text']}")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="token/time stats for DEVELOPMENT_REPORT.md")
    ap.add_argument("--projects-dir", type=Path, default=PROJECTS_DIR)
    ap.add_argument("--session-id", default=SESSION_ID)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)
    main_st, agents, human = collect(args.projects_dir, args.session_id)
    print(render_markdown(main_st, agents, human))
    if args.json:
        args.json.write_text(json.dumps({
            "main": asdict(main_st) | {"wall_s": main_st.wall_s},
            "agents": [asdict(a) | {"wall_s": a.wall_s} for a in agents],
            "human": human}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
