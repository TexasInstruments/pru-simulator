# PRU Core Speed Selector (UI) — Design

**Date:** 2026-07-20
**Status:** Approved

## 1. Overview

Add a UI control to pick the PRU core clock speed from a fixed set of values:
**200, 225, 250, 300, 333 MHz**. Selecting a value updates `pru_clock_mhz`
(PRU0 + RTU0, per `simulator.py`) and `pru1_clock_mhz` (PRU1) to the same
value in the active config file, and reloads the simulator.

This selector always syncs all three cores to the same speed. The existing
PRU0→PRU1 perif clock-drift demo ([[2026-07-18-pru1-perif-drift-design]])
is unaffected: it sets `pru1_clock_mhz` to a deliberately offset value
(e.g. `200.1`) as a separate, manual step — via the config modal or by
editing `memory.cfg` directly — done *after* picking a base speed from this
dropdown. The dropdown itself has no drift-specific logic; it simply always
writes the same value to both keys.

## 2. Backend (`ui/server.py`)

Two new endpoints, next to the existing `/config` GET/PUT
(`ui/server.py:112-180`):

```python
ALLOWED_CLOCK_MHZ = {200, 225, 250, 300, 333}

@app.get("/config/clock_speed")
async def get_clock_speed():
    return {"mhz": sim._pru_clock_mhz}

@app.put("/config/clock_speed")
async def put_clock_speed(request: Request):
    global sim
    body = await request.json()
    mhz = body.get("mhz")
    if mhz not in ALLOWED_CLOCK_MHZ:
        return JSONResponse({"error": f"mhz must be one of {sorted(ALLOWED_CLOCK_MHZ)}"}, status_code=400)
    async with _config_lock:
        try:
            with open(config_path, "r") as f:
                text = f.read()
            text = _set_ini_value(text, "device", "pru_clock_mhz", str(mhz))
            text = _set_ini_value(text, "device", "pru1_clock_mhz", str(mhz))
            with open(config_path, "w") as f:
                f.write(text)
            sim = Simulator(config_path=config_path)
            _history["pru0"].clear()
            _history["rtu0"].clear()
            _history["pru1"].clear()
            return {"ok": True}
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)
```

- `_set_ini_value(text, section, key, value)` is a small regex-based helper:
  finds `key\s*=.*` within the named `[section]` block and replaces it in
  place; if the key is absent, appends `key = value` as a new line at the end
  of that section. This is deliberately **not** `configparser.write()` —
  round-tripping through `configparser` would reformat/strip comments from
  the ini file. Current `.cfg` files have no comments, but the helper avoids
  the risk regardless.
- Rebuild-and-clear-history logic is identical to `PUT /config`
  (`ui/server.py:174-177`); no shared helper extraction needed since it's
  three lines duplicated once — not worth abstracting for a single call site.
- `ALLOWED_CLOCK_MHZ` is the single source of truth for valid speeds; the
  frontend dropdown's option list must match it (kept in sync manually since
  there's no shared config between Python and static JS in this project).

## 3. Frontend (`ui/static/index.html`, `ui/static/app.js`)

**HTML** — new `<select id="pru-speed-select">` in `#controls`, immediately
after `#core-select` (`index.html:1258-1262`):

```html
<select id="pru-speed-select" title="PRU core clock speed (PRU0/RTU0/PRU1)">
  <option value="200">200 MHz</option>
  <option value="225">225 MHz</option>
  <option value="250">250 MHz</option>
  <option value="300">300 MHz</option>
  <option value="333">333 MHz</option>
</select>
```

Styled via the existing shared rule for `#core-select, #mc-partner-select`
(`index.html:59-60`) — add `#pru-speed-select` to that selector list.

**JS** (`app.js`, near the config-modal block at `app.js:1909-1954`):

```js
const pruSpeedSelect = document.getElementById("pru-speed-select");

async function loadClockSpeed() {
  try {
    const res = await fetch("/config/clock_speed");
    const data = await res.json();
    pruSpeedSelect.value = String(Math.round(data.mhz));
  } catch (e) { /* leave dropdown at last-known value */ }
}
loadClockSpeed(); // on page load

pruSpeedSelect.addEventListener("change", async () => {
  pruSpeedSelect.disabled = true;
  try {
    const res  = await fetch("/config/clock_speed", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mhz: Number(pruSpeedSelect.value) }),
    });
    const data = await res.json();
    if (data.ok) {
      flashStatus("RELOADED", "reloaded");
      refreshMemory();
      loadRegions();
    } else {
      alert(data.error || "Failed to set clock speed");
      loadClockSpeed(); // revert dropdown to actual value
    }
  } catch (e) {
    alert(String(e));
    loadClockSpeed();
  } finally {
    pruSpeedSelect.disabled = false;
  }
});
```

- Reuses the exact post-reload sequence the config-modal save already uses
  (`app.js:1940-1944`): `flashStatus`, `refreshMemory()`, `loadRegions()`.
- Also call `loadClockSpeed()` after the config-modal's own save
  (`app.js:1933-1954` `btnConfigSave` handler success branch), since a raw
  config-text edit could change `pru_clock_mhz` behind the dropdown's back.

## 4. Testing

New test file `tests/test_clock_speed_endpoint.py` using FastAPI's
`TestClient` against `ui/server.py`:

- `PUT /config/clock_speed {"mhz": 250}` → 200, `{"ok": true}`; reread the
  config file and assert both `pru_clock_mhz` and `pru1_clock_mhz` are `250`.
- `PUT /config/clock_speed {"mhz": 275}` (not in the allowed set) → 400,
  config file unchanged.
- `GET /config/clock_speed` after a successful PUT reflects the new value.
- Config file that starts with no `pru1_clock_mhz` key at all → PUT inserts
  it (covers the `_set_ini_value` append path).

## 5. Risks / Notes

- Changing speed rebuilds the whole `Simulator` (registers/memory/undo
  history cleared) — identical, accepted behavior to today's config-modal
  save; no new risk introduced.
- This selector always overwrites `pru1_clock_mhz`. If a user has an active
  drift-demo config (PRU1 offset) and then touches this dropdown, the drift
  is silently reset to match PRU0/RTU0 — this is intentional per the
  approved design, not a bug. The drift demo re-applies its own offset
  afterward as a manual step, same as it does today.
