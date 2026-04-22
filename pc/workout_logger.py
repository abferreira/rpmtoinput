from __future__ import annotations
import csv
import os
from datetime import datetime
from pathlib import Path

from profile_manager import sanitize_filename


class WorkoutLogger:
    FLUSH_EVERY = 50

    def __init__(self, username: str, log_dir: str) -> None:
        self._username = sanitize_filename(username)
        self._log_dir = log_dir
        self._file = None
        self._writer = None
        self._row_count = 0

    def start(self) -> str:
        os.makedirs(self._log_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"workout_{self._username}_{ts}.csv"
        path = os.path.join(self._log_dir, filename)
        self._file = open(path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow(["Timestamp", "RPM"])
        self._row_count = 0
        return path

    def log(self, rpm: float) -> None:
        if self._writer is None:
            return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._writer.writerow([ts, f"{rpm:.2f}"])
        self._row_count += 1
        if self._row_count % self.FLUSH_EVERY == 0:
            self._file.flush()

    def stop(self) -> None:
        if self._file:
            self._file.flush()
            self._file.close()
            self._file = None
            self._writer = None
            self._row_count = 0
