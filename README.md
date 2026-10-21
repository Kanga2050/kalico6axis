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
| `install/` | `publish.sh`, which builds the branches and release printers install | – |
| `slicer/` | `split5.py`, the 5-axis slicer, and `slice5.py`, a cube test slicer, for the A/C trunnion | – |

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
source: machine_z             # what drives the joint (see below)
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
| | `workpiece` | none | Part the print sits on; the G-code viewer's toolpath moves with it |
| `[model_part name]` | `file` | required | 3D file for this part |
| | `parent` | root | Part this one rides on |
| | `joint` | `fixed` | `fixed`, `linear` or `rotary` |
| | `axis` | required if moving | Direction of travel, or axis of rotation |
| | `origin` | `0, 0, 0` | A point on the rotation axis (rotary only) |
| | `source` | required if moving | One of the sources below |
| | `scale` | `1` | mm (or degrees) per unit of the source |
| | `home` | `0` | Source value at which the part sits where it was exported |
| | `minimum`, `maximum` | none | Clamp the joint |
| | `color` | from file | Hex colour without `#` (a `#` starts a comment in Klipper configs), like `e8620c`; 8 digits add transparency, like `00b9cd40` |
| | `visible` | `true` | Hide a part; its children still show |

Sources:

| Source | Value |
|---|---|
| `machine_x`, `machine_y`, `machine_z` | Physical toolhead position, worked out from the steps the motors have made |
| `a`, `b`, `c` | Rotary axis angles, from the rotary motors' steps |
| `x`, `y`, `z`, `e` | G-code position. On a machine with rotary axes this is the position on the part, so it only matches the toolhead while every rotary axis is at 0 |
| `<object>:<field>[.key]` | Any printer status value, like `manual_stepper tilt:position` or `motion_report:live_steppers.stepper_z1` |

Moving parts should use `machine_*` and `a`/`b`/`c`: a G-code move or a rotation can drive several motors at once, and Kalico works that out, so the model follows what the motors actually do. In the G-code viewer the same sources are worked out from the file instead: the G-code position and A/B/C at that line are turned into machine coordinates with the pivots the kinematics reports.

Files must be inside the config folder. Kalico checks the tree and publishes it as the `model` printer object; Mainsail loads the files from the config folder and moves the parts in the browser:

- **G-code viewer:** the whole printer animates along with the toolpath while scrubbing, simulating or tracking a print.
- **Dashboard:** a 3D Printer panel, placed below the console, follows the live printer and shows the file being printed on the bed, up to where the printer is.
- **Toolhead panel:** a jog row for each rotary axis the kinematics has (A and C here), below X, Y and Z. The middle button returns the axis to 0.

Both use the G-code viewer's own scene, so they look the same as the original viewer. In either view, click it and use W A S D to move across, Space and Shift to move up and down (5 to 150 mm/s, faster when zoomed out); the fullscreen button fills the screen, and Esc leaves.

## Digital twin

The twin runs the real Kalico, Moonraker and Mainsail on a PC with Docker Desktop, using any normal printer config. Each `[mcu]` is replaced by a simulated MCU that serves the data dictionary of a real firmware build for that chip, so pins and commands are checked exactly as on the printer. It emulates steppers (including high-precision stepping), endstops and probes (homing and probing trigger at consistent positions), TMC drivers over UART and SPI, ADC thermistors with a thermal model (MPC values from the config are used when present), PWM, neopixels and accelerometers. Plugins that are not installed (for example ShakeTune) get stand-ins so the config still loads.

```
twin\twin.ps1                    # pick a config folder; opens Mainsail at http://localhost:8080
twin\twin.ps1 C:\path\to\config
twin\twin.ps1 -Stop
twin\twin.ps1 C:\path\to\config -Port 8081 -NoBrowser
```

On Linux, `twin/twin.sh <config folder>` does the same.

Settings that only the twin should use go in `twin.cfg` in the config folder. The twin includes it after `printer.cfg` (above the SAVE_CONFIG block), so it overrides the printer's own settings; the printer never reads it. This repo's `config/twin.cfg` turns on the trunnion, which is not wired on the printer yet: `corexy_ac` kinematics, `printer/rotary.cfg` with spare pins, and the tilt and rotary joints of the model. So the twin runs as the full 5-axis machine.

If the twin can't tell an MCU's chip from its serial path (for example a CAN or `/dev/ttyAMA0` MCU), add `twin.json` to the config folder: `{"mcus": {"mcu": {"chip": "stm32f446xx", "serial": "/dev/serial/by-id/usb-Klipper_stm32f446xx_twin"}}}`, keyed by the `[mcu]` section name (`"mcu"`, `"mcu ebb"` and so on).

The config is copied before it is used, so the original is never changed. State the printer keeps outside its config folder (such as `~/variables.cfg` from `[save_variables]`) can go in `config/twin_home/`. The first start builds the firmware dictionaries, about a minute per MCU type, and caches them.

Tested with this repo's config, both as the printer is now and as a 5-axis machine with `twin.cfg`: homing, quad gantry level, probing, heating, a full print through `PRINT_START` and `PRINT_END`, A/C jogging, and a 5-axis test print that tilts A to 30° and turns C through 270°. Also tested with a stock Ender-3 V2 config (homing and heating).

Kalico only homes with every rotary axis at 0. If the motors were turned off with the table tilted, level it and run `SET_KINEMATIC_POSITION A=0 C=0`, then `G28`.

Limits: there are no real sensor readings, so endstops and probes trigger at the same spot every time, temperatures follow a simple model, and accelerometer data is flat.

## 5-axis test slicer

`slicer/slice5.py` slices a cube for the trunnion. Layers stay flat; along every wall the table tilts so the nozzle leans toward the wall's outside, so A holds the tilt and C turns a full circle each wall loop (alternate loops turn back, so C never winds up). G-code X Y Z are part coordinates and `corexy_ac` moves the table and gantry to match.

```
python slicer/slice5.py cube5.gcode --size 20 --tilt 20 --layer 0.3
python slicer/slice5.py solid.gcode --size 15 --tilt 25 --infill 1
```

`--infill 1` makes the cube solid: each layer is filled with straight lines with the nozzle leaning across them, and the lines turn 90° every layer, so C also swings between layers.

It writes the object outline for adaptive meshing and the layer count, so Mainsail shows layer progress. Tested on the twin: a 20 mm cube at 20° (67 layers) prints through `PRINT_START` to `PRINT_END`, the motors' positions match the kinematics to under 0.01 mm, and the G-code viewer and 3D Printer panel show the table tilting and turning under the toolpath.

## 5-axis slicer

`slicer/split5.py` slices any closed STL for the trunnion. Instead of deforming the model, it splits it with plane cuts into chunks that each print without supports from their own build direction. The first chunk grows on the bed; every other chunk grows on a cut face of what was printed before it, with its layers parallel to that face, so the table tilts it to face the nozzle.

```
python slicer/split5.py part.stl part.gcode
python slicer/split5.py part.stl part.gcode --method overhang --lean 10 --layer 0.25
```

- `--method overhang` cuts where overhangs meet the body, trying planes through each overhang's root at 30° to 90° from the current build direction. `--method convex` cuts between the parts of a convex decomposition (CoACD), using the plane of each contact patch and the same tilts. `--method hybrid` (default) plans with both and with a mix of the two candidates, each once preferring cuts that free the most at once and once preferring small cuts that free one feature, and keeps the plan with the least overhang left, then the fewest chunks.
- `--overhang` is the steepest overhang left uncut (default 50° from vertical), `--max-tilt` the largest chunk tilt (default 90°).
- Inside a chunk the nozzle leans `--lean` degrees (default 15) toward each wall's outside and across infill lines, building up over `--ramp` mm from the chunk's base, so A and C move while printing. Wall loops pick the direction that turns C back toward 0, so C stays within about one turn.
- Every pose is checked against the real toolhead and gantry beam from `config/models` (carriage and beam sampled into height columns; the beam only across the carriage's width, because the `beam` group in the CAD reaches 3 mm above the nozzle along its whole length, which would hit the trunnion's side holders below Z 29 and can't be the real beam) and against the platter, the tilt cradle, everything printed before and the layers already printed in the chunk. Where the lean would hit, it is reduced for that path, or the other A solution (−A with C + 180°) is used; chunk directions that hit even without lean are not used.
- Between chunks the nozzle retracts, rises above everything that will turn under the head, turns the table in 5° steps with the head held still, then comes down along the new tool axis. `BED_MESH_CLEAR` is sent before the first tilted chunk.

Pivots, A limits and machine limits come from `config/printer/rotary.cfg` and the stepper configs. G-code X Y Z are part coordinates, as for `slice5.py`, and the output has the same `PRINT_START`, object and layer lines.

`slicer/examples/` has test models (`make_examples.py` writes them) and their G-code:

| Model | Chunks | Layers | Largest tilt while printing | Kalico print time |
|---|---|---|---|---|
| `tbracket` (post with a side arm) | 2: the post, then the arm on a 45° cut | 250 | 60° | 2.1 h |
| `lbracket` (post with an arm toward the front) | 2: the post, then the arm on a 45° cut | 272 | 60° | 2.1 h |
| `ybranch` (trunk with two arms 30° above horizontal) | 3: the trunk, then each arm on a 30° cut | 252 | 45° | 1.6 h |
| `tallmushroom` (stem with a wide cap) | 2: the stem, then the cap on a 45° cut | 260 | 60° | 2.1 h |
| `tree` (holed base, trunk, six arms around it at six heights, a shelf and a cap) | 4: the base and trunk, then three cuts tilted toward three sides | 428 | 60° | 4.9 h |

Each has no overhang left, the chunks add up to the model's volume, no pose hits the printer, and Kalico accepts the whole file with `twin.cfg` (from `PRINT_START` to `PRINT_END`, no refused moves). Load one in the twin's Mainsail to see the table tilt and turn under the toolpath.

`arch.stl` and `mushroom.stl` are test models without G-code, because the arch's bridge and the low mushroom's cap stay as overhangs (the slicer warns about them). A single cut can't free the bridge without lifting a pillar off the bed, and a 20 mm high cap is too close to the platter to tilt for, so the toolhead would hit the bed. The tall mushroom shows the same cap printed on a 45° cut once there is room.

## Tests

```
cd mainsail; npx vitest run                     # Mainsail: model joints, rotary maths, G-code A/B/C index
docker exec printer-twin /opt/klippy-env/bin/python /opt/printer/tests/test_sim.py
```

```
python -m pytest slicer/tests                    # split5: maths vs corexy_ac, decomposition, toolpaths, G-code limits
SPLIT5_DICTS=mcu=stm32f446.dict,nhk=rp2040.dict python -m pytest slicer/tests -k kalico
```

The slicer tests check its tool center point maths against `corexy_ac` itself, that each test model splits into the expected chunks with no overhang left and the same volume, that toolpaths stay inside their chunk, and that the G-code stays inside the machine and the A limits. With firmware dictionaries (`twin/sim/build_dict.py stm32f446xx stm32f446.dict`), the last test runs the G-code through Kalico in batch mode with this repo's config and `twin.cfg`, from `PRINT_START` to `PRINT_END`.

The Mainsail tests check the browser's machine-position maths against Kalico's `corexy_ac` formula. The twin tests compare the simulated MCU with Kalico's own code: step timing, TMC UART frames, thermistor tables and config reading.

## Firmware additions (`kalico/`)

- `[model]` / `[model_part]` (`klippy/extras/model.py`).
- `[z_floor]` (`klippy/extras/z_floor.py`): once Z is homed, refuses any move that ends below `z`, except straight lifts. G28's own homing moves are exempt, so a Z endstop pin below the floor still homes, and every G28 ends by lifting Z to `z + clearance` (default 10 mm); probing is not exempt.
- `motion_report` reports `live_steppers` (each motor's position from its step history) and, from those, `live_machine_position` and `live_rotary_position`. It also reports `live_pose` (the G-code X Y Z A B C).
- `corexy_ac` reports `machine_position` and the rotary pivots and limits; `gcode_move` reports `extra_axes`.
- `temperature_host` can read a stand-in file in the twin.
- Fixes for `corexy_ac`: quad gantry level and Z tilt use only the Z motors (the X/Y motors also follow Z once the table tilts), and a probe no longer reports "triggered prior to movement" because the X/Y motors it also stops stayed still.

## Installing on the printer

Printers install three published pieces, which `install/publish.sh` builds from this repo after each change:

| Piece | Where | Installed at |
|---|---|---|
| Kalico | branch `kalico` (Kalico `rotation-support` plus the changes in `kalico/`) | `~/klipper` |
| Moonraker | branch `moonraker` (upstream Moonraker plus the changes in `moonraker/`) | `~/moonraker` |
| Mainsail | release asset `mainsail.zip` (built from `web/mainsail`) | `~/mainsail` |

On a KIAUH Pi, point the existing folders at them:

```
cd ~/klipper && git remote set-url origin https://github.com/Kanga2050/kalico6axis.git && git fetch origin kalico && git checkout -B kalico origin/kalico
cd ~/moonraker && git remote set-url origin https://github.com/Kanga2050/kalico6axis.git && git fetch origin moonraker && git checkout -B moonraker origin/moonraker
```

Then in `moonraker.conf`, so the update manager follows this repo:

```
[update_manager klipper]
origin: https://github.com/Kanga2050/kalico6axis.git
primary_branch: kalico

[update_manager moonraker]
origin: https://github.com/Kanga2050/kalico6axis.git
primary_branch: moonraker

[update_manager mainsail]
type: web
channel: stable
repo: Kanga2050/kalico6axis
path: ~/mainsail
```

Mainsail then installs from the update manager. In `~/kiauh/kiauh.cfg`, the Klipper and Moonraker `repositories` are `https://github.com/Kanga2050/kalico6axis, kalico` and `https://github.com/Kanga2050/kalico6axis, moonraker`.

For the 3D model, copy `config/printer/model.cfg` and `config/models/` into the printer's config folder and add `[include printer/model.cfg]`.

`config/printer/rotary.cfg` still needs the trunnion driver pins; see the notes at the top of that file. When it is wired, copy the `[printer]`, `[include printer/rotary.cfg]` and `[model_part]` lines from `config/twin.cfg` into the printer's config with the real pins.