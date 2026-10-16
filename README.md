# 5-Axis Printer

Everything that runs my 5-axis printer (a Voron 2.4 350 with an A/C trunnion bed), in one repo:

| Folder | What it is | Upstream |
|---|---|---|
| `kalico/` | Kalico firmware, `rotation-support` branch, plus the `[model]` sections and rotary status | [KalicoCrew/kalico](https://github.com/KalicoCrew/kalico) |
| `moonraker/` | Moonraker (unchanged; pinned for the twin) | [Arksine/moonraker](https://github.com/Arksine/moonraker) |
| `mainsail/` | Mainsail with the 3D printer model in the G-code viewer and a 3D Printer dashboard panel | [mainsail-crew/mainsail](https://github.com/mainsail-crew/mainsail) |
| `web/mainsail/` | That Mainsail, built and ready to serve | – |
| `config/` | My printer config, with `printer/model.cfg` and the coloured CAD parts in `models/` | – |
| `twin/` | The digital twin: the whole stack running on a PC with simulated MCUs | – |
| `install/` | `install.sh` for the Pi | – |

## 3D model of the printer

The printer config describes the machine as coloured 3D files joined by fixed, linear or rotary joints, each joint driven by a kinematic value:

```
[model]
file: models/frame.glb        # fixed root part
up_axis: z                    # z for CAD/STL exports, y for standard glTF
offset: -2, -42, -127         # file coordinates -> machine coordinates (mm)

[model_part gantry]
file: models/gantry.glb
joint: linear                 # fixed, linear or rotary
axis: 0, 0, 1
source: z                     # x y z e a b c, machine_x/y/z, or <object>:<field>[.index]
home: 3.0                     # value at which the part sits where it was exported

[model_part tilt]
file: models/tilt.glb
parent: trunnion
joint: rotary
axis: 1, 0, 0
origin: 175, 175, 5           # a point on the axis
source: a
```

All options:

| Section | Option | Default | Meaning |
|---|---|---|---|
| `[model]` | `file` | none | Root part, fixed to the machine |
| | `up_axis` | `z` | `z` for CAD/STL exports, `y` for standard glTF |
| | `unit_scale` | `1` | File units to mm (`1000` for files in metres) |
| | `offset` | `0, 0, 0` | Added to every file's coordinates to place it in machine coordinates |
| | `color` | from file | Colour of the root part |
| `[model_part name]` | `file` | required | 3D file for this part |
| | `parent` | root | Part this one rides on |
| | `joint` | `fixed` | `fixed`, `linear` (`prismatic`) or `rotary` (`revolute`) |
| | `axis` | required if moving | Direction of travel, or axis of rotation |
| | `origin` | `0, 0, 0` | A point on the rotation axis (rotary only) |
| | `source` | required if moving | `x y z e a b c`, `machine_x/y/z`, or `<object>:<field>[.index]` like `manual_stepper tilt:position` |
| | `scale` | `1` | mm (or degrees) per unit of the source |
| | `home` | `0` | Source value at which the part sits where it was exported |
| | `minimum`, `maximum` | none | Clamp the joint |
| | `color` | from file | Hex colour without `#` (a `#` starts a comment in Klipper configs), like `e8620c`; 8 digits add transparency, like `00b9cd40` |
| | `visible` | `true` | Hide a part; its children still show |

Files must be inside the config folder. Kalico checks the tree and publishes it as the `model` printer object; Mainsail loads the files from the config folder and moves the parts in the browser:

- **G-code viewer:** the whole printer animates along with the toolpath while scrubbing, simulating or tracking a print.
- **Dashboard:** a 3D Printer panel next to the webcam and console follows the live printer.

Both use the G-code viewer's own scene, so they look the same as the original viewer.

## Digital twin

The twin runs the real Kalico, Moonraker and Mainsail on a PC with Docker Desktop, using any normal printer config. Each `[mcu]` is replaced by a simulated MCU that serves the data dictionary of a real firmware build for that chip, so pins and commands are checked exactly as on the printer. It emulates steppers (including high-precision stepping), endstops and probes (homing and probing trigger at consistent positions), TMC drivers over UART and SPI, ADC thermistors with a thermal model (MPC values from the config are used when present), PWM, neopixels and accelerometers. Plugins that are not installed (for example ShakeTune) get stand-ins so the config still loads.

```
twin\twin.ps1                    # pick a config folder; opens Mainsail at http://localhost:8080
twin\twin.ps1 C:\path\to\config
twin\twin.ps1 -Stop
twin\twin.ps1 C:\path\to\config -Port 8081 -NoBrowser
```

On Linux, `twin/twin.sh <config folder>` does the same.

If the twin can't tell an MCU's chip from its serial path (for example a CAN or `/dev/ttyAMA0` MCU), add `twin.json` to the config folder: `{"mcus": {"mcu": {"chip": "stm32f446xx", "serial": "/dev/serial/by-id/usb-Klipper_stm32f446xx_twin"}}}`, keyed by the `[mcu]` section name (`"mcu"`, `"mcu ebb"` and so on).

The config is copied before it is used, so the original is never changed. State the printer keeps outside its config folder (such as `~/variables.cfg` from `[save_variables]`) can go in `config/twin_home/`. The first start builds the firmware dictionaries, about a minute per MCU type, and caches them.

Tested with this repo's config (homing, quad gantry level, probing, heating, and a full print through `PRINT_START` and `PRINT_END`) and with a stock Ender-3 V2 config (homing and heating).

Limits: there are no real sensor readings, so endstops and probes trigger at the same spot every time, temperatures follow a simple model, and accelerometer data is flat.

## Tests

```
cd mainsail; npx vitest run                     # Mainsail, including the printer model joints
docker exec printer-twin /opt/klippy-env/bin/python /opt/printer/tests/test_sim.py
```

The twin tests compare the simulated MCU with Kalico's own code: step timing, TMC UART frames, thermistor tables and config reading.

## Firmware additions (`kalico/`)

- `[model]` / `[model_part]` (`klippy/extras/model.py`).
- `motion_report` reports `live_pose` (X Y Z A B C) and `live_machine_position`, with a `motion_report/dump_trapq_pose` endpoint.
- `corexy_ac` reports `machine_position` and the rotary pivots and limits; `gcode_move` reports `extra_axes`.
- `temperature_host` can read a stand-in file in the twin.

## Installing on the printer

On the Pi:

```
curl -O https://raw.githubusercontent.com/Kanga2050/kalico6axis/main/install/install.sh
bash install.sh kalico mainsail
```

Each part is optional. Old folders are kept as `<name>.orig-<date>`. Copy `config/printer/model.cfg` and `config/models/` into the printer's config folder and add `[include printer/model.cfg]`.

`config/printer/rotary.cfg` still needs the trunnion driver pins; see the notes at the top of that file.
