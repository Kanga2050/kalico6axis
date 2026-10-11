# 3D model of the machine for web clients
#
# [model] holds the root (fixed) part and global file settings, and each
# [model_part <name>] adds a part joined to a parent part.  Klippy only
# checks and publishes the tree; clients load the files and move the
# parts from live status values.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import math
import os
import re

JOINTS = {
    "fixed": "fixed",
    "linear": "linear",
    "prismatic": "linear",
    "rotary": "rotary",
    "revolute": "rotary",
}
SHORT_SOURCES = (
    "x", "y", "z", "e", "a", "b", "c",
    "machine_x", "machine_y", "machine_z",
)
SOURCE_RE = re.compile(r"^[a-z_][\w ]*:[a-z_]\w*(\.[\w ]+)*$")
COLOR_RE = re.compile(r"^#?[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")


def _color(config, option):
    val = config.get(option, None)
    if val is None:
        return None
    # '#' starts a comment in Klipper configs, so colours are written without it
    if not COLOR_RE.match(val.strip()):
        raise config.error(
            "Option '%s' in section '%s' must be a hex colour without '#',"
            " like e8620c" % (option, config.get_name())
        )
    return "#" + val.strip().lstrip("#").lower()


def _file(config, printer, required=True):
    path = config.get("file") if required else config.get("file", None)
    if path is None:
        return None
    path = path.strip().replace("\\", "/")
    if path.startswith("/") or ".." in path.split("/"):
        raise config.error(
            "Option 'file' in section '%s' must be a path inside the config"
            " folder" % (config.get_name(),)
        )
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".glb", ".gltf", ".stl", ".obj"):
        raise config.error(
            "Option 'file' in section '%s' must be a .glb, .gltf, .stl or"
            " .obj file" % (config.get_name(),)
        )
    cfgdir = os.path.dirname(printer.get_start_args()["config_file"])
    if not os.path.exists(os.path.join(cfgdir, path)):
        logging.warning("model: %s not found in %s", path, cfgdir)
    return path


class Model:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.error = config.error
        self.root = {
            "name": "root",
            "file": _file(config, self.printer, required=False),
            "color": _color(config, "color"),
        }
        self.up_axis = config.getchoice("up_axis", ["z", "y"], "z")
        self.unit_scale = config.getfloat("unit_scale", 1.0, above=0.0)
        self.offset = config.getfloatlist("offset", (0.0, 0.0, 0.0), count=3)
        self.workpiece = config.get("workpiece", None)
        self.parts = {}
        self.printer.register_event_handler("klippy:connect", self._check)

    def add_part(self, part):
        if part["name"] in self.parts or part["name"] == "root":
            raise self.error(
                "model_part '%s' defined twice" % (part["name"],)
            )
        self.parts[part["name"]] = part

    def _ordered(self):
        out, done = [], {"root"}
        pending = dict(self.parts)
        while pending:
            ready = [p for p in pending.values() if p["parent"] in done]
            if not ready:
                missing = sorted(set(p["parent"] for p in pending.values()))
                raise self.error(
                    "model_part parent not found or circular: %s"
                    % (", ".join(missing),)
                )
            for p in sorted(ready, key=lambda p: p["name"]):
                out.append(p)
                done.add(p["name"])
                del pending[p["name"]]
        return out

    def _check(self):
        self.order = self._ordered()
        if self.workpiece is not None and self.workpiece not in self.parts:
            raise self.error(
                "[model] workpiece '%s' is not a model_part" % (self.workpiece,)
            )

    def get_status(self, eventtime):
        order = getattr(self, "order", None) or self._ordered()
        return {
            "up_axis": self.up_axis,
            "unit_scale": self.unit_scale,
            "offset": list(self.offset),
            "workpiece": self.workpiece,
            "root": dict(self.root),
            "parts": [dict(p) for p in order],
        }


class ModelPart:
    def __init__(self, config):
        printer = config.get_printer()
        model = printer.load_object(config, "model")
        name = config.get_name().split(None, 1)[1]
        joint = config.getchoice("joint", JOINTS, "fixed")
        part = {
            "name": name,
            "file": _file(config, printer),
            "parent": config.get("parent", "root").strip(),
            "joint": joint,
            "color": _color(config, "color"),
            "visible": config.getboolean("visible", True),
        }
        if joint != "fixed":
            axis = config.getfloatlist("axis", count=3)
            length = math.sqrt(sum(v * v for v in axis))
            if not length:
                raise config.error(
                    "Option 'axis' in section '%s' must not be zero"
                    % (config.get_name(),)
                )
            source = config.get("source").strip()
            if source.lower() in SHORT_SOURCES:
                source = source.lower()
            elif not SOURCE_RE.match(source):
                raise config.error(
                    "Option 'source' in section '%s' must be one of %s, or"
                    " '<printer object>:<field>[.key]' such as"
                    " 'manual_stepper tilt:position'"
                    % (config.get_name(), ", ".join(SHORT_SOURCES))
                )
            part.update(
                {
                    "axis": [v / length for v in axis],
                    "origin": list(
                        config.getfloatlist("origin", (0.0, 0.0, 0.0), count=3)
                    ),
                    "source": source,
                    "scale": config.getfloat("scale", 1.0),
                    "home": config.getfloat("home", 0.0),
                    "minimum": config.getfloat("minimum", None),
                    "maximum": config.getfloat("maximum", None),
                }
            )
        model.add_part(part)


def load_config(config):
    return Model(config)
