from __future__ import annotations
import queue
import threading
import time

import serial
import serial.tools.list_ports


class SerialReader:
    def __init__(self, port: str, baud: int, rpm_queue: "queue.Queue[float | None]") -> None:
        self._port = port
        self._baud = baud
        self._queue = rpm_queue
        self._serial: serial.Serial | None = None
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._serial = serial.Serial(self._port, self._baud, timeout=1)
        time.sleep(2)  # wait for Arduino reset after DTR toggle
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._serial and self._serial.is_open:
            self._serial.close()

    def send_magnets(self, count: int) -> None:
        if self._serial and self._serial.is_open:
            self._serial.write(f"MAGNETS:{count}\n".encode())

    def _read_loop(self) -> None:
        try:
            while self._running:
                line = self._serial.readline().decode(errors="ignore").strip()
                if line.startswith("RPM:"):
                    try:
                        self._queue.put_nowait(float(line[4:]))
                    except (ValueError, queue.Full):
                        pass
        except serial.SerialException:
            self._queue.put_nowait(None)  # sentinel: connection lost

    @staticmethod
    def detect_port(baud: int = 115200, timeout: float = 2.0) -> str | None:
        for info in serial.tools.list_ports.comports():
            port = info.device
            try:
                with serial.Serial(port, baud, timeout=timeout) as s:
                    time.sleep(2)  # wait for Arduino reset
                    s.reset_input_buffer()
                    s.write(b"PING\n")
                    deadline = time.time() + timeout
                    while time.time() < deadline:
                        line = s.readline().decode(errors="ignore").strip()
                        if line == "PONG:RPM_SENSOR":
                            return port
            except (serial.SerialException, OSError):
                continue
        return None
