from __future__ import annotations
import queue
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import AppConfig
from serial_reader import SerialReader
from rpm_processor import RPMProcessor
from controller import ControllerOutput
from joystick_reader import JoystickReader
from gui import App


def main() -> None:
    config_path = Path(__file__).parent.parent / "config.json"

    if not config_path.exists():
        config = AppConfig()
        config.save(str(config_path))
    else:
        config = AppConfig.load(str(config_path))

    rpm_queue: queue.Queue[float | None] = queue.Queue()
    reader     = SerialReader(config.serial_port, config.baud_rate, rpm_queue)
    processor  = RPMProcessor(config)
    controller = ControllerOutput()
    joystick   = JoystickReader(max(0, config.joystick_index))

    app = App(config, str(config_path), reader, processor, controller, rpm_queue, joystick)
    app.mainloop()

    reader.stop()
    joystick.stop()
    controller.reset()


if __name__ == "__main__":
    main()
