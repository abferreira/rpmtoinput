from __future__ import annotations
import threading
import time
from dataclasses import dataclass, field
from typing import List, Tuple

_pygame_available = False
try:
    import pygame
    _pygame_available = True
except ImportError:
    pass


@dataclass
class JoystickState:
    axes:    List[float]          = field(default_factory=list)
    buttons: List[bool]           = field(default_factory=list)
    hats:    List[Tuple[int, int]] = field(default_factory=list)


def list_joysticks() -> List[str]:
    if not _pygame_available:
        return []
    if not pygame.get_init():
        pygame.init()
    pygame.joystick.init()
    names = []
    for i in range(pygame.joystick.get_count()):
        j = pygame.joystick.Joystick(i)
        j.init()
        names.append(j.get_name())
    return names


class JoystickReader:
    def __init__(self, device_index: int = 0) -> None:
        self._index = device_index
        self._lock = threading.Lock()
        self._state: JoystickState | None = None
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def available(self) -> bool:
        return _pygame_available

    @property
    def connected(self) -> bool:
        return self._running and self._state is not None

    def start(self) -> None:
        if not _pygame_available or self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def get_state(self) -> JoystickState | None:
        with self._lock:
            s = self._state
            if s is None:
                return None
            return JoystickState(
                axes=list(s.axes),
                buttons=list(s.buttons),
                hats=list(s.hats),
            )

    def _run(self) -> None:
        if not pygame.get_init():
            pygame.init()
        pygame.joystick.init()

        if self._index >= pygame.joystick.get_count():
            self._running = False
            return

        joy = pygame.joystick.Joystick(self._index)
        joy.init()

        while self._running:
            pygame.event.pump()
            axes    = [joy.get_axis(i)   for i in range(joy.get_numaxes())]
            buttons = [bool(joy.get_button(i)) for i in range(joy.get_numbuttons())]
            hats    = [joy.get_hat(i)    for i in range(joy.get_numhats())]
            with self._lock:
                self._state = JoystickState(axes=axes, buttons=buttons, hats=hats)
            time.sleep(0.005)  # ~200 Hz

        joy.quit()
        with self._lock:
            self._state = None
