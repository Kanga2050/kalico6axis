# 5-Axis Printer

Everything that runs my 5-axis printer (a Voron 2.4 350 with an A/C trunnion bed), in one repo:

| Folder | What it is | Upstream |
|---|---|---|
| `kalico/` | Kalico firmware, `rotation-support` branch, with extra status for the rotary axes | [KalicoCrew/kalico](https://github.com/KalicoCrew/kalico) |
| `moonraker/` | Moonraker with a `twin` component that relays the 6-axis motion queue and serves the twin | [Arksine/moonraker](https://github.com/Arksine/moonraker) |
| `fluidd/` | Fluidd with A and C position fields and a Twin button on the toolhead card | [fluidd-core/fluidd](https://github.com/fluidd-core/fluidd) |
| `mainsail/` | Mainsail with A and C move-to fields and a Twin button | [mainsail-crew/mainsail](https://github.com/mainsail-crew/mainsail) |
| `twin/` | Digital twin web app (three.js) | – |
| `config/` | Printer and Moonraker config, plus `printer/rotary.cfg` for the trunnion | – |
| `web/` | Built Fluidd and Mainsail, ready to serve | – |
| `install/` | `install.sh` for the Pi | – |

## Digital twin

The twin shows the printer in 3D from the Fusion CAD: frame, Z gantry, Y beam, X carriage, tilt cradle and rotary platter, each moving the way the real machine does.

- **Live:** connect to the printer (for example `192.168.86.31`). It subscribes to Moonraker and follows the toolhead, A and C, temperatures and the job. With the forked Kalico and Moonraker it receives the planned motion queue for all six axes, so motion is smooth and exact. With stock firmware it falls back to the 4 Hz status.
- **Simulate:** open a G-code file. The app plans it with a JavaScript port of Kalico's lookahead and the `corexy_ac` kinematics, then plays it back with the toolpath building on the rotating bed. No printer needed.

All planning, kinematics and rendering run in the browser; the Pi only forwards data.

```
cd twin
npm install
npm run dev      # http://localhost:5173
npm test         # kinematics checked against kalico/klippy/kinematics/corexy_ac.py
npm run build    # -> twin/dist, served by Moonraker at :7125/twin/index.html
```

The 3D model is in `twin/public/model` (one GLB per moving group, colours from the CAD) with the pivot axes in `manifest.json`. Machine limits and pivots are in `twin/public/machine.json`; when connected, the pivots and limits from the printer's config are used instead.

## Firmware additions

`kalico/`:
- `motion_report` reports `live_pose` (X Y Z A B C) and `live_machine_position`, and has a `motion_report/dump_trapq_pose` endpoint with the full pose.
- `corexy_ac` reports `machine_position` and the rotary pivots and limits.
- `gcode_move` reports `extra_axes` and `gcode_extra_axes`.

`moonraker/moonraker/components/twin.py`: `[twin]` section; endpoints `/server/twin/info` and `/server/twin/subscribe`; websocket notification `notify_twin_trapq_batch`.

## Installing on the printer

On the Pi:

```
curl -O https://raw.githubusercontent.com/Kanga2050/5-axis-printer/main/install/install.sh
bash install.sh kalico moonraker fluidd mainsail twin
```

Each part is optional. Old folders are kept as `<name>.orig-<date>`.

`config/printer/rotary.cfg` still needs the trunnion driver pins; see the notes at the top of that file.
