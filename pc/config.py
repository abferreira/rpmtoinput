from __future__ import annotations
import json
from dataclasses import dataclass, asdict, field


@dataclass
class AppConfig:
    serial_port:      str   = "COM3"
    baud_rate:        int   = 115200
    num_magnets:      int   = 1
    rpm_multiplier:   float = 1.0
    min_rpm:          float = 0.0
    max_rpm:          float = 120.0
    dead_zone_rpm:    float = 5.0
    output_axis:      str   = "right_trigger"
    smoothing_factor: int   = 5
    joystick_index:   int   = -1     # -1 = passthrough disabled
    joystick_type:    str   = "xbox360"  # "xbox360" or "seriesx"

    VALID_AXES: tuple = field(default=(
        "right_trigger", "left_trigger",
        "left_stick_y", "right_stick_y",
        "left_stick_x", "right_stick_x",
    ), init=False, repr=False, compare=False)

    @classmethod
    def load(cls, path: str) -> "AppConfig":
        with open(path, "r") as f:
            data = json.load(f)
        obj = cls()
        for key, val in data.items():
            if hasattr(obj, key) and key != "VALID_AXES":
                setattr(obj, key, val)
        return obj

    def save(self, path: str) -> None:
        d = {k: v for k, v in asdict(self).items() if k != "VALID_AXES"}
        with open(path, "w") as f:
            json.dump(d, f, indent=4)
