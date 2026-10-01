"""Interactive Tk viewer for exported PRU SSI Signal Graph CSV files.

Run from the repository root with:
    python pru-simulator/tools/view_ssi_trace.py path/to/trace.csv
"""

from __future__ import annotations

import argparse
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

try:
    from tools.plot_ssi_trace import decode_ssi_frames, load_trace
except ModuleNotFoundError:  # direct execution from inside pru-simulator/
    from plot_ssi_trace import decode_ssi_frames, load_trace


def series_for_core(rows: list[dict], core: str, clock_column: str, data_column: str):
    """Return every exported step, clock value, and data value for one core."""
    step_of = lambda row: int(row["_step"] if "_step" in row else row["step"])
    selected = sorted((row for row in rows if row["core"] == core), key=step_of)
    steps = [step_of(row) for row in selected]
    clock = [int(row[clock_column]) for row in selected]
    data = [int(row[data_column]) for row in selected]
    return steps, clock, data


def zoom_limits(left: float, right: float, cursor: float, factor: float):
    """Return a zoomed range centered at cursor, clamped to the trace."""
    if factor >= 1:
        return left, right
    width = right - left
    new_width = min(width, width * factor)
    new_left = cursor - new_width / 2
    new_right = new_left + new_width
    if new_left < left:
        new_left, new_right = left, left + new_width
    if new_right > right:
        new_right, new_left = right, right - new_width
    return new_left, new_right


class TraceViewer:
    def __init__(self, root: tk.Tk, csv_path: str | Path | None = None):
        self.root = root
        self.root.title("PRU SSI Trace Viewer")
        self.root.geometry("1280x820")
        self.rows: list[dict] = []
        self.frames: list[dict] = []
        self.csv_path: Path | None = None
        self.trace_left = 0
        self.trace_right = 1
        self.drag_start: tuple[float, float] | None = None

        controls = ttk.Frame(root, padding=6)
        controls.pack(fill="x")
        ttk.Button(controls, text="Open CSV", command=self.open_csv).pack(side="left")
        ttk.Button(controls, text="Reset view", command=self.reset_view).pack(side="left", padx=(6, 0))
        ttk.Label(controls, text="Frame:").pack(side="left", padx=(14, 4))
        self.frame_combo = ttk.Combobox(controls, state="readonly", width=32)
        self.frame_combo.pack(side="left")
        self.frame_combo.bind("<<ComboboxSelected>>", self.select_frame)
        self.status = ttk.Label(controls, text="Open an exported CSV")
        self.status.pack(side="left", padx=12)

        self.figure, self.axis = plt.subplots(figsize=(12, 7))
        self.figure.subplots_adjust(left=0.07, right=0.99, bottom=0.08, top=0.92)
        self.canvas = FigureCanvasTkAgg(self.figure, master=root)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        NavigationToolbar2Tk(self.canvas, root, pack_toolbar=False).pack(fill="x")
        self.canvas.mpl_connect("scroll_event", self.on_scroll)
        self.canvas.mpl_connect("button_press_event", self.on_press)
        self.canvas.mpl_connect("motion_notify_event", self.on_drag)
        self.canvas.mpl_connect("button_release_event", self.on_release)

        if csv_path:
            self.load(csv_path)

    def open_csv(self):
        path = filedialog.askopenfilename(
            title="Open PRU trace CSV", filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if path:
            try:
                self.load(path)
            except (OSError, ValueError) as exc:
                messagebox.showerror("Cannot open trace", str(exc))

    def load(self, path: str | Path):
        self.csv_path = Path(path)
        self.rows = load_trace(self.csv_path)
        self.frames = decode_ssi_frames([row for row in self.rows if row["core"] == "pru1"])
        steps = [int(row.get("_step", row["step"])) for row in self.rows]
        self.trace_left, self.trace_right = min(steps), max(steps)
        labels = [f"{i + 1}: 0x{int(frame['value']):03X} ({frame['start']}..{frame['end']})"
                  for i, frame in enumerate(self.frames)]
        self.frame_combo["values"] = ["Full trace"] + labels
        self.frame_combo.current(0)
        self.status["text"] = f"{len(self.rows)} rows Â· {len(self.frames)} SSI frames Â· every row plotted"
        self.draw()

    def draw(self):
        self.axis.clear()
        rows_by_core = {}
        for row in self.rows:
            rows_by_core.setdefault(row["core"], []).append(row)
        lane_specs = [
            ("pru1", "gpo0", "PRU1 GPO0 clock", "tab:blue", 0),
            ("pru0", "gpi16", "PRU0 GPI16 clock", "tab:orange", 1),
            ("pru0", "gpo0", "PRU0 GPO0 data", "tab:green", 2),
            ("pru1", "gpi8", "PRU1 GPI8 data", "tab:red", 3),
        ]
        for core, column, label, color, offset in lane_specs:
            core_rows = rows_by_core.get(core, [])
            if not core_rows or column not in core_rows[0]:
                continue
            steps, _, values = series_for_core(
                self.rows, core,
                "gpo0" if core == "pru1" else "gpi16",
                column,
            )
            self.axis.step(steps, [value + offset for value in values], where="post",
                           color=color, label=label, linewidth=1.1)
        self.axis.set_xlim(self.trace_left, self.trace_right)
        self.axis.set_ylim(-0.25, 4.25)
        self.axis.set_yticks([0.5, 1.5, 2.5, 3.5], ["PRU1 CLK", "PRU0 CLK", "PRU0 DATA", "PRU1 DATA"])
        self.axis.set_xlabel("simulator step")
        self.axis.set_title(self.csv_path.name if self.csv_path else "PRU SSI trace")
        self.axis.grid(alpha=0.2)
        self.axis.legend(loc="upper right", ncol=2)
        self.canvas.draw_idle()

    def reset_view(self):
        if self.rows:
            self.trace_left, self.trace_right = self._data_limits()
            self.frame_combo.current(0)
            self.draw()

    def _data_limits(self):
        steps = [int(row.get("_step", row["step"])) for row in self.rows]
        return min(steps), max(steps)

    def select_frame(self, _event=None):
        index = self.frame_combo.current() - 1
        if index < 0:
            self.reset_view()
            return
        frame = self.frames[index]
        padding = max(100, (int(frame["end"]) - int(frame["start"])) // 8)
        data_left, data_right = self._data_limits()
        self.trace_left = max(data_left, int(frame["start"]) - padding)
        self.trace_right = min(data_right, int(frame["end"]) + padding)
        self.draw()

    def on_scroll(self, event):
        if event.inaxes is not self.axis or event.xdata is None:
            return
        left, right = self.axis.get_xlim()
        factor = 1 / 1.5 if event.button == "up" else 1.5
        if factor < 1:
            left, right = zoom_limits(left, right, event.xdata, factor)
        else:
            data_left, data_right = self._data_limits()
            width = min(data_right - data_left, (right - left) * factor)
            fraction = (event.xdata - left) / max(right - left, 1)
            left = max(data_left, event.xdata - width * fraction)
            right = min(data_right, left + width)
            left = max(data_left, right - width)
        self.trace_left, self.trace_right = left, right
        self.draw()

    def on_press(self, event):
        if event.inaxes is self.axis and event.button == 1 and event.xdata is not None:
            self.drag_start = (event.xdata, self.axis.get_xlim()[0])

    def on_drag(self, event):
        if self.drag_start is None or event.inaxes is not self.axis or event.xdata is None:
            return
        start_x, start_left = self.drag_start
        left, right = self.axis.get_xlim()
        delta = start_x - event.xdata
        width = right - left
        data_left, data_right = self._data_limits()
        new_left = max(data_left, min(data_right - width, start_left + delta))
        self.trace_left, self.trace_right = new_left, new_left + width
        self.draw()

    def on_release(self, event):
        if event.button == 1:
            self.drag_start = None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", nargs="?", type=Path)
    args = parser.parse_args()
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print("Tkinter could not start. Install Python with Tcl/Tk support, then retry.")
        print(f"Details: {exc}")
        return 2
    TraceViewer(root, args.csv)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
