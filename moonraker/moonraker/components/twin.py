# Digital twin support for 5-axis printers running Kalico corexy_ac
#
# Forwards the planned 6-axis motion queue (motion_report/dump_trapq_pose)
# to websocket clients and serves the twin web app.  All kinematics and
# rendering run in the browser; Moonraker only relays data.
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import logging
import pathlib
from ..common import RequestType
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import WebRequest
    from .klippy_apis import KlippyAPI
    from .klippy_connection import KlippyConnection

TRAPQ_ENDPOINT = "motion_report/dump_trapq_pose"
STATUS_OBJECTS = {
    "toolhead": ["axis_minimum", "axis_maximum", "extra_axes"],
    "configfile": ["settings"],
}


class Twin:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.app_path = pathlib.Path(
            config.get("path", "~/printer-stack/twin/dist")
        ).expanduser().resolve()
        self.streaming = False
        self.header: Optional[List[str]] = None
        self.batches_sent = 0
        app = self.server.lookup_component("application")
        if self.app_path.is_dir():
            app.register_static_file_handler("/twin", str(self.app_path))
        else:
            logging.info(f"Twin: app path {self.app_path} not found")
        kconn: KlippyConnection = self.server.lookup_component(
            "klippy_connection"
        )
        kconn.register_remote_method(
            "twin_trapq_batch", self._on_trapq_batch, need_klippy_reg=False
        )
        self.server.register_notification("twin:trapq_batch", "twin_trapq_batch")
        self.server.register_endpoint(
            "/server/twin/info", RequestType.GET, self._handle_info
        )
        self.server.register_endpoint(
            "/server/twin/subscribe", RequestType.POST, self._handle_subscribe
        )
        self.server.register_event_handler(
            "server:klippy_disconnect", self._on_klippy_disconnect
        )

    def _on_klippy_disconnect(self) -> None:
        self.streaming = False
        self.header = None

    def _on_trapq_batch(self, data: Optional[List[Any]] = None, **kwargs) -> None:
        if not data:
            return
        self.batches_sent += 1
        self.server.send_event("twin:trapq_batch", {"data": data})

    async def _start_stream(self) -> None:
        if self.streaming:
            return
        kapis: KlippyAPI = self.server.lookup_component("klippy_apis")
        result = await kapis._send_klippy_request(
            TRAPQ_ENDPOINT,
            {"name": "toolhead", "response_template": {"method": "twin_trapq_batch"}},
        )
        if isinstance(result, dict):
            self.header = result.get("header")
        self.streaming = True

    async def _handle_subscribe(self, web_request: WebRequest) -> Dict[str, Any]:
        await self._start_stream()
        return {"streaming": self.streaming, "header": self.header}

    async def _handle_info(self, web_request: WebRequest) -> Dict[str, Any]:
        kapis: KlippyAPI = self.server.lookup_component("klippy_apis")
        status = await kapis.query_objects(STATUS_OBJECTS, None)
        rotary: Dict[str, Any] = {}
        if status:
            settings = status.get("configfile", {}).get("settings", {})
            for axis in ("a", "c"):
                sec = settings.get(f"rotary_{axis}")
                if sec is not None:
                    rotary[axis] = {
                        "pivot": sec.get("pivot"),
                        "position_min": sec.get("position_min"),
                        "position_max": sec.get("position_max"),
                    }
            kin = settings.get("printer", {}).get("kinematics")
        else:
            kin = None
        return {
            "app_url": "/twin/index.html" if self.app_path.is_dir() else None,
            "kinematics": kin,
            "rotary_axes": rotary,
            "streaming": self.streaming,
            "batches_sent": self.batches_sent,
        }


def load_component(config: ConfigHelper) -> Twin:
    return Twin(config)
