from __future__ import annotations
import collections
import csv
import glob
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox

import serial.tools.list_ports

from profile_manager import sanitize_filename
from workout_logger import WorkoutLogger

from config import AppConfig
from serial_reader import SerialReader
from rpm_processor import RPMProcessor
from controller import ControllerOutput
from joystick_reader import JoystickReader, list_joysticks


class App(tk.Tk):
    def __init__(
        self,
        config: AppConfig,
        config_path: str,
        reader: SerialReader,
        processor: RPMProcessor,
        controller: ControllerOutput,
        rpm_queue: "queue.Queue[float | None]",
        joystick: JoystickReader,
        *,
        manager,
        log_dir: str,
        profiles_path: str,
    ) -> None:
        super().__init__()
        self._config = config
        self._config_path = config_path
        self._reader = reader
        self._processor = processor
        self._controller = controller
        self._queue = rpm_queue
        self._joystick = joystick
        self._connected = False
        self._logging = False
        self._log_start_time: float | None = None
        self._session_max_rpm: float = 0.0
        self._session_rpm_sum: float = 0.0
        self._session_rpm_count: int = 0
        self._graph_history: collections.deque[float] = collections.deque(maxlen=300)
        self._profile: dict = {}
        self._manager = manager
        self._logger: WorkoutLogger | None = None
        self._log_dir = log_dir
        self._profiles_path = profiles_path

        self.title("RPM to Input")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self._poll()

    # -------------------------------------------------------------------------
    # UI construction
    # -------------------------------------------------------------------------

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        # --- Port row ---
        port_frame = ttk.LabelFrame(self, text="Serial Port")
        port_frame.grid(row=0, column=0, columnspan=2, sticky="ew", **pad)

        self._port_var = tk.StringVar(value=self._config.serial_port)
        self._port_menu = ttk.Combobox(port_frame, textvariable=self._port_var, width=12)
        self._port_menu.grid(row=0, column=0, padx=4, pady=4)
        self._refresh_ports()

        ttk.Button(port_frame, text="Refresh", command=self._refresh_ports).grid(row=0, column=1, padx=4)
        ttk.Button(port_frame, text="Auto-detect", command=self._auto_detect).grid(row=0, column=2, padx=4)
        self._connect_btn = ttk.Button(port_frame, text="Connect", command=self._toggle_connect)
        self._connect_btn.grid(row=0, column=3, padx=4)

        # --- Joystick passthrough ---
        joy_frame = ttk.LabelFrame(self, text="Joystick Passthrough")
        joy_frame.grid(row=1, column=0, columnspan=2, sticky="ew", **pad)

        ttk.Label(joy_frame, text="Device:", width=10, anchor="w").grid(row=0, column=0, padx=4, pady=4)

        self._joy_names: list[str] = []
        self._joy_var = tk.StringVar(value="None (disabled)")
        self._joy_menu = ttk.Combobox(joy_frame, textvariable=self._joy_var, state="readonly", width=32)
        self._joy_menu.grid(row=0, column=1, padx=4, pady=4)
        self._joy_menu.bind("<<ComboboxSelected>>", self._on_joy_selected)

        ttk.Button(joy_frame, text="Refresh", command=self._refresh_joysticks).grid(row=0, column=2, padx=4)

        ttk.Label(joy_frame, text="Type:", width=10, anchor="w").grid(row=1, column=0, padx=4, pady=4)
        self._joy_type_var = tk.StringVar(value=self._config.joystick_type)
        joy_type_menu = ttk.Combobox(
            joy_frame, textvariable=self._joy_type_var,
            values=["xbox360", "seriesx"], state="readonly", width=14,
        )
        joy_type_menu.grid(row=1, column=1, sticky="w", padx=4)
        joy_type_menu.bind("<<ComboboxSelected>>", self._on_joy_type_selected)
        ttk.Label(joy_frame, text="(xbox360 = 360/One,  seriesx = Series S/X)",
                  foreground="gray").grid(row=1, column=2, padx=4)

        self._joy_status_var = tk.StringVar(value="")
        ttk.Label(joy_frame, textvariable=self._joy_status_var, foreground="gray").grid(
            row=2, column=0, columnspan=3, padx=4, pady=2)

        self._refresh_joysticks()
        self._controller.joystick_type = self._config.joystick_type

        # --- Readout ---
        readout_frame = ttk.LabelFrame(self, text="Readout")
        readout_frame.grid(row=2, column=0, columnspan=2, sticky="ew", **pad)

        self._rpm_label = tk.Label(readout_frame, text="-- RPM", font=("Helvetica", 32, "bold"))
        self._rpm_label.grid(row=0, column=0, rowspan=2, padx=12, pady=4)

        self._out_label = tk.Label(readout_frame, text="Output: 0.000  (0)", font=("Helvetica", 14))
        self._out_label.grid(row=2, column=0, padx=12, pady=2)

        # --- Session stats (right column) ---
        stats_frame = tk.Frame(readout_frame)
        stats_frame.grid(row=0, column=1, rowspan=3, padx=12, pady=4, sticky="ns")

        self._stat_duration_var = tk.StringVar(value="Duration  --:--")
        self._stat_max_var      = tk.StringVar(value="Max RPM   --")
        self._stat_avg_var      = tk.StringVar(value="Avg RPM   --")

        for var in (self._stat_duration_var, self._stat_max_var, self._stat_avg_var):
            tk.Label(stats_frame, textvariable=var, font=("Helvetica", 12),
                     anchor="w", foreground="#888888").pack(anchor="w", pady=1)

        # --- RPM Graph ---
        graph_frame = ttk.LabelFrame(self, text="RPM Graph")
        graph_frame.grid(row=3, column=0, columnspan=2, sticky="ew", **pad)

        self._graph_w = 500
        self._graph_h = 120
        self._graph_canvas = tk.Canvas(
            graph_frame, width=self._graph_w, height=self._graph_h,
            bg="#1a1a1a", highlightthickness=0,
        )
        self._graph_canvas.pack(padx=4, pady=4)
        self._draw_graph()

        # --- Sliders ---
        slider_frame = ttk.LabelFrame(self, text="Settings")
        slider_frame.grid(row=4, column=0, columnspan=2, sticky="ew", **pad)

        self._sliders: dict[str, tk.Variable] = {}
        slider_defs = [
            ("num_magnets",      "Magnets",          1,    8,    1,   True),
            ("rpm_multiplier",   "RPM Multiplier",   0.1,  3.0,  0.1, False),
            ("max_rpm",          "Max RPM",          10,   300,  1,   True),
            ("dead_zone_rpm",    "Dead Zone RPM",    0,    30,   0.5, False),
            ("smoothing_factor", "Smoothing",        1,    20,   1,   True),
        ]
        for i, (key, label, lo, hi, res, is_int) in enumerate(slider_defs):
            var = tk.IntVar(value=int(getattr(self._config, key))) if is_int \
                  else tk.DoubleVar(value=float(getattr(self._config, key)))
            self._sliders[key] = var
            ttk.Label(slider_frame, text=label, width=16, anchor="w").grid(row=i, column=0, padx=4, pady=2)
            scale = tk.Scale(slider_frame, variable=var, from_=lo, to=hi, resolution=res,
                             orient="horizontal", length=280,
                             command=self._on_slider_change)
            scale.grid(row=i, column=1, padx=4)

        # --- Axis selector ---
        ttk.Label(slider_frame, text="Output Axis", width=16, anchor="w").grid(
            row=len(slider_defs), column=0, padx=4, pady=2)
        axes = ("right_trigger", "left_trigger", "left_stick_y", "right_stick_y",
                "left_stick_x", "right_stick_x")
        self._axis_var = tk.StringVar(value=self._config.output_axis)
        axis_menu = ttk.Combobox(slider_frame, textvariable=self._axis_var, values=axes,
                                 state="readonly", width=20)
        axis_menu.grid(row=len(slider_defs), column=1, sticky="w", padx=4)
        axis_menu.bind("<<ComboboxSelected>>", self._on_slider_change)

        # --- Profile frame ---
        profile_frame = ttk.LabelFrame(self, text="Profile")
        profile_frame.grid(row=5, column=0, columnspan=2, sticky="ew", **pad)

        # Row 0: selector + management buttons
        ttk.Label(profile_frame, text="Active:", anchor="w").grid(
            row=0, column=0, padx=(8, 2), pady=4)
        self._profile_var = tk.StringVar()
        self._profile_combo = ttk.Combobox(
            profile_frame, textvariable=self._profile_var, state="readonly", width=20
        )
        self._profile_combo.grid(row=0, column=1, padx=4, pady=4)
        self._profile_combo.bind("<<ComboboxSelected>>", self._on_profile_selected)
        self._refresh_profiles()

        ttk.Button(profile_frame, text="New Profile",
                   command=lambda: self._open_profile_form(None)).grid(
            row=0, column=2, padx=4)
        self._edit_btn = ttk.Button(profile_frame, text="Edit Profile",
                                    command=lambda: self._open_profile_form(self._profile),
                                    state="disabled")
        self._edit_btn.grid(row=0, column=3, padx=(4, 8))

        # Row 1: profile-action buttons (disabled until a profile is loaded)
        self._records_btn = ttk.Button(
            profile_frame, text="View Records", command=self._show_records, state="disabled"
        )
        self._records_btn.grid(row=1, column=0, columnspan=2, padx=8, pady=(0, 2), sticky="w")
        self._recalibrate_btn = ttk.Button(
            profile_frame, text="Re-Calibrate", command=self._on_recalibrate, state="disabled"
        )
        self._recalibrate_btn.grid(row=1, column=2, columnspan=2, padx=8, pady=(0, 2), sticky="w")

        # Row 2: logging controls
        self._log_btn = ttk.Button(
            profile_frame, text="Start Logging", command=self._toggle_logging, state="disabled"
        )
        self._log_btn.grid(row=2, column=0, columnspan=2, padx=8, pady=(2, 6), sticky="w")

        self._log_indicator_var = tk.StringVar(value="● Not logging")
        self._log_indicator = ttk.Label(
            profile_frame, textvariable=self._log_indicator_var, foreground="#888888"
        )
        self._log_indicator.grid(row=2, column=2, columnspan=2, padx=8, sticky="w")

        # --- Save button ---
        ttk.Button(self, text="Save Config", command=self._on_save).grid(
            row=6, column=0, columnspan=2, pady=6)

        # --- Status bar ---
        self._status_var = tk.StringVar(value="Disconnected")
        status_bar = ttk.Label(self, textvariable=self._status_var, relief="sunken", anchor="w")
        status_bar.grid(row=7, column=0, columnspan=2, sticky="ew", padx=4, pady=2)

        if not self._controller.available:
            self._status_var.set("WARNING: vgamepad/ViGEmBus not available — no controller output")

    # -------------------------------------------------------------------------
    # Graph
    # -------------------------------------------------------------------------

    def _draw_graph(self) -> None:
        c = self._graph_canvas
        w, h = self._graph_w, self._graph_h
        pad_l, pad_r, pad_t, pad_b = 36, 8, 8, 18
        plot_w = w - pad_l - pad_r
        plot_h = h - pad_t - pad_b

        c.delete("all")

        max_rpm = float(self._config.max_rpm) or 1.0

        # background grid lines
        for frac in (0.25, 0.5, 0.75, 1.0):
            y = pad_t + plot_h * (1.0 - frac)
            c.create_line(pad_l, y, w - pad_r, y, fill="#333333", dash=(2, 4))
            label = int(max_rpm * frac)
            c.create_text(pad_l - 4, y, text=str(label), fill="#888888",
                          font=("Helvetica", 7), anchor="e")

        # y-axis label
        c.create_text(8, h // 2, text="RPM", fill="#888888",
                      font=("Helvetica", 8), angle=90)

        # data line
        history = list(self._graph_history)
        if len(history) >= 2:
            n = len(history)
            x_step = plot_w / (self._graph_history.maxlen - 1)
            x_offset = pad_l + plot_w - (n - 1) * x_step

            points = []
            for i, rpm in enumerate(history):
                x = x_offset + i * x_step
                y = pad_t + plot_h * (1.0 - min(rpm, max_rpm) / max_rpm)
                points.extend([x, y])

            c.create_line(*points, fill="#00cc66", width=1.5, smooth=True)

        # border
        c.create_rectangle(pad_l, pad_t, w - pad_r, pad_t + plot_h,
                           outline="#444444", width=1)

    # -------------------------------------------------------------------------
    # Polling
    # -------------------------------------------------------------------------

    def _poll(self) -> None:
        # 1. Apply physical joystick passthrough (all axes + buttons)
        if self._joystick.connected:
            state = self._joystick.get_state()
            if state is not None:
                self._controller.apply_passthrough(state, skip_axis=self._config.output_axis)

        # 2. Drain RPM queue and overlay bike axis on top
        latest: float | None = None
        try:
            while True:
                val = self._queue.get_nowait()
                if val is None:
                    self._handle_disconnect()
                    self.after(20, self._poll)
                    return
                latest = val
        except queue.Empty:
            pass

        if latest is not None and self._connected:
            smoothed, normalized = self._processor.feed(latest)
            if self._logging:
                self._logger.log(smoothed)
                self._session_max_rpm = max(self._session_max_rpm, smoothed)
                self._session_rpm_sum += smoothed
                self._session_rpm_count += 1
            self._rpm_label.config(text=f"{smoothed:.1f} RPM")
            axis_int = int(normalized * 255) if "trigger" in self._config.output_axis \
                       else int(normalized * 32767)
            self._out_label.config(text=f"Output: {normalized:.3f}  ({axis_int})")
            self._controller.set_axis(self._config.output_axis, normalized)
            self._graph_history.append(smoothed)
            self._draw_graph()

        # 3. Update session stats display
        self._update_session_stats()

        # 4. Single flush pushes passthrough + RPM axis together
        self._controller.flush()

        self.after(20, self._poll)

    # -------------------------------------------------------------------------
    # Event handlers
    # -------------------------------------------------------------------------

    def _toggle_connect(self) -> None:
        if self._connected:
            self._on_disconnect()
        else:
            self._on_connect()

    def _on_connect(self) -> None:
        port = self._port_var.get().strip()
        if not port:
            self._status_var.set("No port selected.")
            return
        try:
            self._config.serial_port = port
            self._reader._port = port
            self._reader.start()
            self._connected = True
            self._connect_btn.config(text="Disconnect")
            if self._profile:
                self._log_btn.config(state="normal")
            self._status_var.set(f"Connected to {port}")
        except Exception as e:
            self._status_var.set(f"Error: {e}")

    def _on_disconnect(self) -> None:
        if self._logging and self._logger:
            self._logger.stop()
            self._logger = None
            self._logging = False
        self._log_start_time = None
        self._log_btn.config(state="disabled")
        self._log_indicator_var.set("● Not logging")
        self._log_indicator.config(foreground="#888888")
        self._reader.stop()
        self._processor.reset()
        self._controller.reset()
        self._connected = False
        self._connect_btn.config(text="Connect")
        self._rpm_label.config(text="-- RPM")
        self._out_label.config(text="Output: 0.000  (0)")
        self._status_var.set("Disconnected")
        self._graph_history.clear()
        self._draw_graph()

    def _handle_disconnect(self) -> None:
        if self._connected:
            self._on_disconnect()
            self._status_var.set("Connection lost.")

    def _on_slider_change(self, _event=None) -> None:
        self._config.num_magnets      = int(self._sliders["num_magnets"].get())
        self._config.rpm_multiplier   = float(self._sliders["rpm_multiplier"].get())
        self._config.max_rpm          = float(self._sliders["max_rpm"].get())
        self._config.dead_zone_rpm    = float(self._sliders["dead_zone_rpm"].get())
        self._config.smoothing_factor = int(self._sliders["smoothing_factor"].get())
        self._config.output_axis      = self._axis_var.get()
        self._processor.update_config(self._config)
        if self._connected:
            self._reader.send_magnets(self._config.num_magnets)

    def _on_save(self) -> None:
        self._config.save(self._config_path)
        self._status_var.set("Config saved.")

    def _refresh_ports(self) -> None:
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self._port_menu["values"] = ports
        if ports and self._port_var.get() not in ports:
            self._port_var.set(ports[0])

    def _auto_detect(self) -> None:
        self._status_var.set("Auto-detecting Arduino... (this takes a few seconds)")
        self.update()

        def _detect() -> None:
            port = SerialReader.detect_port(baud=self._config.baud_rate)
            self.after(0, lambda: self._on_detect_result(port))

        threading.Thread(target=_detect, daemon=True).start()

    def _on_detect_result(self, port: str | None) -> None:
        if port:
            self._port_var.set(port)
            self._status_var.set(f"Arduino found on {port}")
        else:
            self._status_var.set("Auto-detect: Arduino not found. Check connection and upload sketch.")

    def _refresh_joysticks(self) -> None:
        self._joy_names = list_joysticks()
        options = ["None (disabled)"] + [f"{i}: {n}" for i, n in enumerate(self._joy_names)]
        self._joy_menu["values"] = options

        saved = self._config.joystick_index
        if saved >= 0 and saved < len(self._joy_names):
            self._joy_var.set(f"{saved}: {self._joy_names[saved]}")
            self._apply_joystick_selection(saved)
        else:
            self._joy_var.set("None (disabled)")
            self._joy_status_var.set("No passthrough active." if not self._joy_names
                                     else "Select a device above to enable passthrough.")

    def _on_joy_type_selected(self, _event=None) -> None:
        self._config.joystick_type = self._joy_type_var.get()
        self._controller.joystick_type = self._config.joystick_type

    def _on_joy_selected(self, _event=None) -> None:
        selection = self._joy_var.get()
        if selection.startswith("None"):
            self._joystick.stop()
            self._config.joystick_index = -1
            self._joy_status_var.set("Passthrough disabled.")
        else:
            idx = int(selection.split(":")[0])
            self._apply_joystick_selection(idx)
            self._config.joystick_index = idx

    def _apply_joystick_selection(self, idx: int) -> None:
        self._joystick.stop()
        self._joystick._index = idx
        self._joystick._state = None
        self._joystick.start()
        name = self._joy_names[idx] if idx < len(self._joy_names) else f"device {idx}"
        self._joy_status_var.set(f"Passing through: {name}")

    def _refresh_profiles(self) -> None:
        self._manager.load()
        names = self._manager.get_names()
        self._profile_combo["values"] = names
        current = self._profile.get("name", "")
        if current in names:
            self._profile_var.set(current)
        else:
            self._profile_var.set("")

    def _on_profile_selected(self, _event=None) -> None:
        name = self._profile_var.get()
        profile = self._manager.get_profile(name)
        if profile is None:
            return
        self._profile = profile
        cal = profile.get("calibration") or {}
        if cal.get("max_rpm"):
            self._config.max_rpm = float(cal["max_rpm"])
            self._sliders["max_rpm"].set(int(self._config.max_rpm))
            self._processor.update_config(self._config)
        self._edit_btn.config(state="normal")
        self._records_btn.config(state="normal")
        self._recalibrate_btn.config(state="normal")
        if self._connected:
            self._log_btn.config(state="normal")
        self._status_var.set(f"Profile loaded: {name}")

    def _open_profile_form(self, profile: dict | None) -> None:
        is_edit = bool(profile)
        win = tk.Toplevel(self)
        win.title("Edit Profile" if is_edit else "New Profile")
        win.resizable(False, False)
        win.grab_set()
        pad = {"padx": 8, "pady": 4}

        info = (profile.get("info") or {}) if is_edit else {}

        fields_def = [
            ("Name",         "name",       profile["name"] if is_edit else "",  "str"),
            ("Sex",          "sex",         info.get("sex", ""),                "sex"),
            ("Age (years)",  "age",         str(info["age"]) if info.get("age") is not None else "", "int"),
            ("Weight (kg)",  "weight_kg",   str(info["weight_kg"]) if info.get("weight_kg") is not None else "", "float"),
            ("Height (cm)",  "height_cm",   str(info["height_cm"]) if info.get("height_cm") is not None else "", "float"),
        ]

        vars_: dict[str, tk.StringVar] = {}
        for i, (label, key, default, kind) in enumerate(fields_def):
            ttk.Label(win, text=label + ":", anchor="w", width=14).grid(
                row=i, column=0, sticky="w", **pad)
            var = tk.StringVar(value=default)
            vars_[key] = var
            if kind == "sex":
                w = ttk.Combobox(win, textvariable=var, values=["M", "F", "Other"],
                                 state="readonly", width=10)
            else:
                w = ttk.Entry(win, textvariable=var, width=22)
                if key == "name" and is_edit:
                    w.config(state="disabled")
            w.grid(row=i, column=1, sticky="w", **pad)

        def _save() -> None:
            name = vars_["name"].get().strip()
            if not name:
                messagebox.showwarning("Validation", "Name cannot be empty.", parent=win)
                return
            if not is_edit and self._manager.get_profile(name) is not None:
                messagebox.showwarning("Validation", f"Profile '{name}' already exists.", parent=win)
                return

            new_info: dict = {}
            sex = vars_["sex"].get().strip()
            if sex:
                new_info["sex"] = sex
            for key, kind in (("age", "int"), ("weight_kg", "float"), ("height_cm", "float")):
                raw = vars_[key].get().strip()
                if not raw:
                    continue
                try:
                    val = int(raw) if kind == "int" else float(raw)
                    if val <= 0:
                        raise ValueError
                    new_info[key] = val
                except ValueError:
                    label = next(l for l, k, *_ in fields_def if k == key)
                    messagebox.showwarning("Validation", f"{label} must be a positive number.", parent=win)
                    return

            if is_edit:
                self._manager.update_info(name, new_info)
                if self._profile.get("name") == name:
                    self._profile["info"] = new_info
            else:
                self._manager.add_profile(name, new_info)

            self._refresh_profiles()
            self._status_var.set(f"Profile {'updated' if is_edit else 'created'}: {name}")
            win.destroy()

        btn_frame = tk.Frame(win)
        btn_frame.grid(row=len(fields_def), column=0, columnspan=2, pady=8)
        ttk.Button(btn_frame, text="Save", command=_save).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Cancel", command=win.destroy).pack(side="left", padx=4)

    def _update_session_stats(self) -> None:
        import time
        if not self._logging or self._log_start_time is None:
            self._stat_duration_var.set("Duration  --:--")
            self._stat_max_var.set("Max RPM   --")
            self._stat_avg_var.set("Avg RPM   --")
            return
        elapsed = int(time.monotonic() - self._log_start_time)
        duration = f"{elapsed // 60}:{elapsed % 60:02d}"
        max_rpm = f"{self._session_max_rpm:.1f}" if self._session_rpm_count else "--"
        avg_rpm = (f"{self._session_rpm_sum / self._session_rpm_count:.1f}"
                   if self._session_rpm_count else "--")
        self._stat_duration_var.set(f"Duration  {duration}")
        self._stat_max_var.set(f"Max RPM   {max_rpm}")
        self._stat_avg_var.set(f"Avg RPM   {avg_rpm}")

    def _toggle_logging(self) -> None:
        import time
        if self._logging:
            if self._logger:
                self._logger.stop()
                self._logger = None
            self._logging = False
            self._log_start_time = None
            self._log_btn.config(text="Start Logging")
            self._log_indicator_var.set("● Not logging")
            self._log_indicator.config(foreground="#888888")
            self._status_var.set("Logging stopped.")
        else:
            if not self._profile:
                messagebox.showwarning("No Profile", "Select a profile before logging.")
                return
            self._logger = WorkoutLogger(self._profile["name"], self._log_dir)
            log_path = self._logger.start()
            self._logging = True
            self._log_start_time = time.monotonic()
            self._session_max_rpm = 0.0
            self._session_rpm_sum = 0.0
            self._session_rpm_count = 0
            self._log_btn.config(text="Stop Logging")
            self._log_indicator_var.set("● Logging")
            self._log_indicator.config(foreground="#00cc66")
            self._status_var.set(f"Logging to {os.path.basename(log_path)}")

    def _show_records(self) -> None:
        if not self._profile:
            return
        win = tk.Toplevel(self)
        win.title(f"Records — {self._profile['name']}")
        win.resizable(False, False)
        pad = {"padx": 10, "pady": 6}

        # --- Calibration section ---
        cal_frame = ttk.LabelFrame(win, text="Calibration")
        cal_frame.grid(row=0, column=0, sticky="ew", **pad)

        info = self._profile.get("info") or {}
        cal  = self._profile.get("calibration") or {}

        info_fields = [
            ("Sex",    info.get("sex", "—")),
            ("Age",    f"{info['age']} yrs"       if info.get("age")       is not None else "—"),
            ("Weight", f"{info['weight_kg']} kg"  if info.get("weight_kg") is not None else "—"),
            ("Height", f"{info['height_cm']} cm"  if info.get("height_cm") is not None else "—"),
        ]
        cal_fields = [
            ("Baseline RPM",  f"{cal['baseline_rpm']:.1f}"   if cal.get("baseline_rpm") is not None else "—"),
            ("Peak RPM",      f"{cal['max_rpm']:.1f}"        if cal.get("max_rpm")      is not None else "—"),
            ("Coast-down",    f"{cal['friction_time']:.2f}s" if cal.get("friction_time") is not None else "—"),
        ]
        fields = info_fields + cal_fields

        for i, (label, value) in enumerate(fields):
            ttk.Label(cal_frame, text=label + ":", anchor="w", width=14).grid(row=i, column=0, padx=6, pady=2, sticky="w")
            ttk.Label(cal_frame, text=value, anchor="w").grid(row=i, column=1, padx=6, pady=2, sticky="w")

        # --- Workout history section ---
        hist_frame = ttk.LabelFrame(win, text="Workout History  (Enter/double-click to view graph, Delete to remove)")
        hist_frame.grid(row=1, column=0, sticky="ew", **pad)

        columns = ("date", "duration", "avg_rpm", "peak_rpm")
        tree = ttk.Treeview(hist_frame, columns=columns, show="headings", height=10)
        tree.heading("date",     text="Date & Time")
        tree.heading("duration", text="Duration")
        tree.heading("avg_rpm",  text="Avg RPM")
        tree.heading("peak_rpm", text="Peak RPM")
        tree.column("date",     width=160, anchor="w")
        tree.column("duration", width=80,  anchor="center")
        tree.column("avg_rpm",  width=80,  anchor="center")
        tree.column("peak_rpm", width=80,  anchor="center")

        sb = ttk.Scrollbar(hist_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")

        log_dir = str(Path(self._profiles_path).parent / "workouts")
        safe_name = sanitize_filename(self._profile["name"])
        pattern = os.path.join(log_dir, f"workout_{safe_name}_*.csv")
        files = sorted(glob.glob(pattern), reverse=True)

        # iid → file path, used by delete/open handlers
        iid_to_path: dict[str, str] = {}

        if not files:
            tree.insert("", "end", values=("No workouts recorded", "", "", ""))
        else:
            for path in files:
                rows, rpms, first_ts, last_ts = _parse_workout_csv(path)
                if rows == 0:
                    continue
                avg = f"{sum(rpms) / len(rpms):.1f}" if rpms else "—"
                peak = f"{max(rpms):.1f}" if rpms else "—"
                duration = _format_duration(first_ts, last_ts, rows)
                date_str = first_ts if first_ts else os.path.basename(path)
                iid = tree.insert("", "end", values=(date_str, duration, avg, peak))
                iid_to_path[iid] = path

        def _on_delete(_event=None) -> None:
            sel = tree.selection()
            if not sel or sel[0] not in iid_to_path:
                return
            iid = sel[0]
            path = iid_to_path[iid]
            date_val = tree.item(iid, "values")[0]
            if not messagebox.askyesno(
                "Delete Workout",
                f"Delete the workout recorded on:\n{date_val}\n\nThis cannot be undone.",
                parent=win,
            ):
                return
            try:
                os.remove(path)
            except OSError as e:
                messagebox.showerror("Error", f"Could not delete file:\n{e}", parent=win)
                return
            del iid_to_path[iid]
            tree.delete(iid)

        def _on_open(_event=None) -> None:
            sel = tree.selection()
            if not sel or sel[0] not in iid_to_path:
                return
            path = iid_to_path[sel[0]]
            date_val = tree.item(sel[0], "values")[0]
            _open_workout_graph(win, path, date_val)

        tree.bind("<Delete>",       _on_delete)
        tree.bind("<Return>",       _on_open)
        tree.bind("<Double-1>",     _on_open)

        ttk.Button(win, text="Close", command=win.destroy).grid(row=2, column=0, pady=8)

    def _on_recalibrate(self) -> None:
        if not self._profile:
            return
        if self._connected:
            messagebox.showwarning("Re-Calibrate", "Disconnect from serial first.")
            return
        calibration_script = str(Path(__file__).parent / "calibration.py")
        cmd = [
            sys.executable, calibration_script,
            self._config.serial_port, str(self._config.baud_rate),
            self._profile["name"], self._profiles_path,
            str(self._config.num_magnets),
        ]
        messagebox.showinfo(
            "Re-Calibrate",
            "A calibration console will open.\n"
            "Follow the instructions there, then return here.",
        )
        flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
        proc = subprocess.Popen(cmd, creationflags=flags)
        self.after(500, lambda: self._wait_for_calibration(proc))

    def _wait_for_calibration(self, proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            self.after(500, lambda: self._wait_for_calibration(proc))
            return
        if proc.returncode != 0:
            self._status_var.set("Calibration cancelled or failed.")
            return
        self._manager.load()
        updated = self._manager.get_profile(self._profile["name"])
        if updated:
            self._profile.update(updated)
            cal = updated.get("calibration") or {}
            if cal.get("max_rpm"):
                self._config.max_rpm = float(cal["max_rpm"])
                self._sliders["max_rpm"].set(int(self._config.max_rpm))
                self._processor.update_config(self._config)
        self._status_var.set("Calibration complete. Profile updated.")

    def _on_close(self) -> None:
        if self._connected:
            self._on_disconnect()
        self.destroy()


def _open_workout_graph(parent: tk.Toplevel, path: str, title: str) -> None:
    points: list[tuple[float, float]] = []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    from datetime import datetime
                    ts = datetime.strptime(row["Timestamp"], "%Y-%m-%d %H:%M:%S")
                    rpm = float(row["RPM"])
                    points.append((ts.timestamp(), rpm))
                except (ValueError, KeyError):
                    pass
    except OSError as e:
        messagebox.showerror("Error", f"Could not read file:\n{e}", parent=parent)
        return

    if not points:
        messagebox.showinfo("No Data", "This workout file contains no plottable data.", parent=parent)
        return

    win = tk.Toplevel(parent)
    win.title(f"Workout — {title}")
    win.resizable(False, False)

    W, H = 620, 260
    PAD_L, PAD_R, PAD_T, PAD_B = 48, 16, 16, 36

    canvas = tk.Canvas(win, width=W, height=H, bg="#1a1a1a", highlightthickness=0)
    canvas.pack(padx=8, pady=8)

    t0 = points[0][0]
    elapsed = [p[0] - t0 for p in points]
    rpms = [p[1] for p in points]
    max_t = max(elapsed) or 1.0
    max_rpm = max(rpms) or 1.0

    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_T - PAD_B

    def px(t: float) -> float:
        return PAD_L + t / max_t * plot_w

    def py(rpm: float) -> float:
        return PAD_T + plot_h * (1.0 - min(rpm, max_rpm) / max_rpm)

    # Grid lines — RPM (horizontal)
    for frac in (0.25, 0.5, 0.75, 1.0):
        y = PAD_T + plot_h * (1.0 - frac)
        canvas.create_line(PAD_L, y, W - PAD_R, y, fill="#333333", dash=(2, 4))
        canvas.create_text(PAD_L - 4, y, text=str(int(max_rpm * frac)),
                           fill="#888888", font=("Helvetica", 7), anchor="e")

    # Grid lines — time (vertical), ~5 marks
    num_t_marks = min(5, int(max_t // 60) + 1) if max_t >= 60 else 5
    for i in range(1, num_t_marks + 1):
        t_mark = max_t * i / num_t_marks
        x = px(t_mark)
        canvas.create_line(x, PAD_T, x, PAD_T + plot_h, fill="#333333", dash=(2, 4))
        secs = int(t_mark)
        label = f"{secs // 60}:{secs % 60:02d}"
        canvas.create_text(x, PAD_T + plot_h + 10, text=label,
                           fill="#888888", font=("Helvetica", 7))

    # Axis labels
    canvas.create_text(8, H // 2, text="RPM", fill="#888888",
                       font=("Helvetica", 8), angle=90)
    canvas.create_text(W // 2, H - 6, text="Time (m:ss)",
                       fill="#888888", font=("Helvetica", 8))

    # Data line
    coords: list[float] = []
    for t, rpm in zip(elapsed, rpms):
        coords.extend([px(t), py(rpm)])
    if len(coords) >= 4:
        canvas.create_line(*coords, fill="#00cc66", width=1.5, smooth=True)

    # Border
    canvas.create_rectangle(PAD_L, PAD_T, W - PAD_R, PAD_T + plot_h,
                             outline="#444444", width=1)

    ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 8))


def _parse_workout_csv(path: str) -> tuple[int, list[float], str, str]:
    """Return (row_count, rpm_values, first_timestamp, last_timestamp)."""
    rpms: list[float] = []
    first_ts = ""
    last_ts = ""
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    rpms.append(float(row["RPM"]))
                except (ValueError, KeyError):
                    pass
                ts = row.get("Timestamp", "")
                if ts:
                    if not first_ts:
                        first_ts = ts
                    last_ts = ts
    except OSError:
        pass
    return len(rpms), rpms, first_ts, last_ts


def _format_duration(first_ts: str, last_ts: str, row_count: int) -> str:
    if first_ts and last_ts and first_ts != last_ts:
        try:
            from datetime import datetime
            fmt = "%Y-%m-%d %H:%M:%S"
            delta = datetime.strptime(last_ts, fmt) - datetime.strptime(first_ts, fmt)
            total = int(delta.total_seconds())
            return f"{total // 60}m {total % 60:02d}s"
        except ValueError:
            pass
    # Fallback: estimate from row count at ~10 Hz
    total = row_count // 10
    return f"{total // 60}m {total % 60:02d}s"
