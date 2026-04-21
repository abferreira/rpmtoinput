from __future__ import annotations
import collections
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import serial.tools.list_ports

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
        self._graph_history: collections.deque[float] = collections.deque(maxlen=300)

        self.title("RPM to Input")
        self.resizable(False, False)
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
        self._rpm_label.grid(row=0, column=0, padx=12, pady=4)

        self._out_label = tk.Label(readout_frame, text="Output: 0.000  (0)", font=("Helvetica", 14))
        self._out_label.grid(row=1, column=0, padx=12, pady=2)

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

        # --- Save button ---
        ttk.Button(self, text="Save Config", command=self._on_save).grid(
            row=5, column=0, columnspan=2, pady=6)

        # --- Status bar ---
        self._status_var = tk.StringVar(value="Disconnected")
        status_bar = ttk.Label(self, textvariable=self._status_var, relief="sunken", anchor="w")
        status_bar.grid(row=6, column=0, columnspan=2, sticky="ew", padx=4, pady=2)

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
            self._rpm_label.config(text=f"{smoothed:.1f} RPM")
            axis_int = int(normalized * 255) if "trigger" in self._config.output_axis \
                       else int(normalized * 32767)
            self._out_label.config(text=f"Output: {normalized:.3f}  ({axis_int})")
            self._controller.set_axis(self._config.output_axis, normalized)
            self._graph_history.append(smoothed)
            self._draw_graph()

        # 3. Single flush pushes passthrough + RPM axis together
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
            self._status_var.set(f"Connected to {port}")
        except Exception as e:
            self._status_var.set(f"Error: {e}")

    def _on_disconnect(self) -> None:
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
