from __future__ import annotations

try:
    import vgamepad as vg
    _VGAMEPAD_AVAILABLE = True
except (ImportError, OSError):
    _VGAMEPAD_AVAILABLE = False

try:
    from joystick_reader import JoystickState
except ImportError:
    JoystickState = None  # type: ignore[assignment,misc]


def _btn(name: str):
    return getattr(vg.XUSB_BUTTON, name) if _VGAMEPAD_AVAILABLE else None


# Buttons 0-9 are the same on both controller types
_SHARED_BUTTONS = [] if not _VGAMEPAD_AVAILABLE else [
    _btn("XUSB_GAMEPAD_A"),
    _btn("XUSB_GAMEPAD_B"),
    _btn("XUSB_GAMEPAD_X"),
    _btn("XUSB_GAMEPAD_Y"),
    _btn("XUSB_GAMEPAD_LEFT_SHOULDER"),
    _btn("XUSB_GAMEPAD_RIGHT_SHOULDER"),
    _btn("XUSB_GAMEPAD_BACK"),
    _btn("XUSB_GAMEPAD_START"),
    _btn("XUSB_GAMEPAD_LEFT_THUMB"),
    _btn("XUSB_GAMEPAD_RIGHT_THUMB"),
]


_HAT_DPAD = {} if not _VGAMEPAD_AVAILABLE else {
    (0,  1): _btn("XUSB_GAMEPAD_DPAD_UP"),
    (0, -1): _btn("XUSB_GAMEPAD_DPAD_DOWN"),
    (-1, 0): _btn("XUSB_GAMEPAD_DPAD_LEFT"),
    (1,  0): _btn("XUSB_GAMEPAD_DPAD_RIGHT"),
}


def _trigger_raw(axis_val: float) -> int:
    """Convert SDL trigger axis (-1=rest, 1=pressed) to 0..255."""
    return int((axis_val + 1) / 2 * 255)


class ControllerOutput:
    def __init__(self) -> None:
        self._pad = None
        self._available = False
        self.joystick_type = "xbox360"  # updated by GUI
        if _VGAMEPAD_AVAILABLE:
            try:
                self._pad = vg.VX360Gamepad()
                self._available = True
            except Exception:
                pass

    @property
    def available(self) -> bool:
        return self._available

    def set_axis(self, axis_name: str, normalized: float) -> None:
        if not self._available:
            return
        normalized = max(0.0, min(1.0, normalized))
        if axis_name in ("right_trigger", "left_trigger"):
            self._set_trigger(axis_name, normalized)
        else:
            self._set_stick(axis_name, normalized)

    def flush(self) -> None:
        if self._available:
            self._pad.update()

    def apply_passthrough(self, state: "JoystickState", skip_axis: str = "") -> None:
        if not self._available or state is None:
            return
        if self.joystick_type == "seriesx":
            self._apply_seriesx(state, skip_axis)
        else:
            self._apply_xbox360(state, skip_axis)

    # ------------------------------------------------------------------
    # Layout: Xbox 360  (LX=0, LY=1, LT=2, RX=3, RY=4, RT=5)
    #         10 buttons, d-pad via hat
    # ------------------------------------------------------------------
    def _apply_xbox360(self, state: "JoystickState", skip_axis: str = "") -> None:
        axes = state.axes
        lx = axes[0] if len(axes) > 0 else 0.0
        ly = -axes[1] if len(axes) > 1 else 0.0
        self._pad.left_joystick_float(x_value_float=lx, y_value_float=ly)

        if len(axes) > 2 and skip_axis != "left_trigger":
            self._pad.left_trigger(value=_trigger_raw(axes[2]))

        rx = axes[3] if len(axes) > 3 else 0.0
        ry = -axes[4] if len(axes) > 4 else 0.0
        self._pad.right_joystick_float(x_value_float=rx, y_value_float=ry)

        if len(axes) > 5 and skip_axis != "right_trigger":
            self._pad.right_trigger(value=_trigger_raw(axes[5]))

        self._apply_shared_buttons(state.buttons)

        if state.hats:
            hat = state.hats[0]
            for direction, dpad_btn in _HAT_DPAD.items():
                if hat == direction:
                    self._pad.press_button(button=dpad_btn)
                else:
                    self._pad.release_button(button=dpad_btn)

    # ------------------------------------------------------------------
    # Layout: Xbox Series S/X  (LX=0, LY=1, RX=2, RY=3, LT=4, RT=5)
    #         16 buttons — 0-9 shared, 10-11 unmappable, 12-15 = d-pad
    # ------------------------------------------------------------------
    def _apply_seriesx(self, state: "JoystickState", skip_axis: str = "") -> None:
        axes = state.axes
        lx = axes[0] if len(axes) > 0 else 0.0
        ly = -axes[1] if len(axes) > 1 else 0.0
        self._pad.left_joystick_float(x_value_float=lx, y_value_float=ly)

        rx = axes[2] if len(axes) > 2 else 0.0
        ry = -axes[3] if len(axes) > 3 else 0.0
        self._pad.right_joystick_float(x_value_float=rx, y_value_float=ry)

        if len(axes) > 4 and skip_axis != "left_trigger":
            self._pad.left_trigger(value=_trigger_raw(axes[4]))
        if len(axes) > 5 and skip_axis != "right_trigger":
            self._pad.right_trigger(value=_trigger_raw(axes[5]))

        self._apply_shared_buttons(state.buttons)

        # D-pad via hat (same as 360 — buttons 12-15 are unrelated)
        if state.hats:
            hat = state.hats[0]
            for direction, dpad_btn in _HAT_DPAD.items():
                if hat == direction:
                    self._pad.press_button(button=dpad_btn)
                else:
                    self._pad.release_button(button=dpad_btn)

    def _apply_shared_buttons(self, buttons: list) -> None:
        for i, btn in enumerate(_SHARED_BUTTONS):
            pressed = buttons[i] if i < len(buttons) else False
            if pressed:
                self._pad.press_button(button=btn)
            else:
                self._pad.release_button(button=btn)

    def reset(self) -> None:
        if not self._available:
            return
        self._pad.reset()
        self._pad.update()

    def _set_trigger(self, which: str, value: float) -> None:
        raw = int(value * 255)
        if which == "right_trigger":
            self._pad.right_trigger(value=raw)
        else:
            self._pad.left_trigger(value=raw)

    def _set_stick(self, which: str, value: float) -> None:
        fval = value
        if which == "left_stick_y":
            self._pad.left_joystick_float(x_value_float=0.0, y_value_float=fval)
        elif which == "right_stick_y":
            self._pad.right_joystick_float(x_value_float=0.0, y_value_float=fval)
        elif which == "left_stick_x":
            self._pad.left_joystick_float(x_value_float=fval, y_value_float=0.0)
        elif which == "right_stick_x":
            self._pad.right_joystick_float(x_value_float=fval, y_value_float=0.0)
