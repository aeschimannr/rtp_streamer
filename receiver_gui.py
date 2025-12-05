#!/usr/bin/env python3
"""
Minimal cross‑platform RTP receiver GUI for FFmpeg (Tkinter)
- 3 buttons: Load SDP, Choose Output, Start/Stop (same button)
- Launches: ffmpeg -protocol_whitelist "file,rtp,udp" -i <SDP> -c copy -movflags +faststart <OUTPUT>
- No extra deps, no fancy features. Keep it simple.

Packaging so the client installs NOTHING:
- Bundle ffmpeg inside the one‑file executable with PyInstaller.
- Build examples (choose the one for your OS):
  Linux/macOS:
    pyinstaller --onefile --windowed \
      --add-binary "/absolute/path/to/ffmpeg:ffmpeg" \
      receiver_gui.py
  Windows (PowerShell/CMD):
    pyinstaller --onefile --windowed ^
      --add-binary "C:\\path\\to\\ffmpeg.exe;ffmpeg" ^
      receiver_gui.py

Notes:
- The --add-binary puts the ffmpeg binary inside the bundle under an internal folder named "ffmpeg/".
- At runtime we resolve ffmpeg path by checking: PATH → next to the exe → inside the PyInstaller bundle (sys._MEIPASS/ffmpeg/...).
"""

import os
import sys
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
import socket

try:
    import numpy as np
except Exception:
    np = None  # type: ignore

try:
    import cv2
except Exception:
    cv2 = None  # type: ignore

try:
    from PIL import Image, ImageTk, ImageDraw

    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False


def find_ffmpeg() -> str | None:
    """Return an ffmpeg executable path or None if not found.

    Search order:
      1) PATH (shutil.which)
      2) Next to the executable (same folder as the frozen exe or this script)
      3) Inside PyInstaller bundle (sys._MEIPASS/ffmpeg/ffmpeg[.exe])
    """
    # 1) PATH
    p = shutil.which("ffmpeg")
    if p:
        return p

    # 2) Same directory as the executable/script
    base_dir = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
    for cand in ("ffmpeg", "ffmpeg.exe"):
        candidate = os.path.join(base_dir, cand)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    # 3) Inside PyInstaller bundle
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        for cand in (os.path.join(meipass, "ffmpeg", "ffmpeg"), os.path.join(meipass, "ffmpeg", "ffmpeg.exe")):
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand

    return None


def detect_bottom_line(
    frame_gray,
    ksize=21,
    elongation_thresh=10,
    var_thresh_rel=0.5,
):
    """Détecte la ligne du contour le plus bas dans une image (frame_gray)."""
    img = frame_gray
    h, w = img.shape
    img = img.astype(np.float32)

    # Variance locale
    mean = cv2.blur(img, (ksize, ksize))
    variance = cv2.blur(img**2, (ksize, ksize)) - mean**2

    # Seuil relatif autour de la variance moyenne
    var_mean = np.mean(variance)
    mask = (np.abs(variance - var_mean) < var_thresh_rel * var_mean).astype(np.uint8) * 255

    # Contours
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Garder les contours très allongés
    elongated = []
    for c in contours:
        x, y, w_box, h_box = cv2.boundingRect(c)
        if min(w_box, h_box) == 0:
            continue
        if max(w_box, h_box) / min(w_box, h_box) > elongation_thresh:
            elongated.append(c)

    if not elongated:
        return None

    # Prendre le contour le plus "bas" (plus grande coordonnée y moyenne)
    bottom = max(elongated, key=lambda c: np.mean(c[:, 0, 1]))
    x = bottom[:, 0, 0]
    y = bottom[:, 0, 1]

    if len(x) < 2:
        return None

    # Fit d'une droite y = a x + b
    try:
        a, b = np.polyfit(x, y, 1)
    except np.linalg.LinAlgError:
        return None

    x1, x2 = 0, w - 1
    y1, y2 = int(a * x1 + b), int(a * x2 + b)

    # On renvoie les deux points de la droite
    return (x1, y1, x2, y2, a, b)


def draw_angle_line(frame_bgr, angle_deg, hfov_deg=40.0, color=(0, 0, 255), thickness=2):
    """
    Draw a colored line at a given angle relative to image center (BGR frame).

    Parameters
    ----------
    frame_bgr : np.ndarray (H, W, 3)
        Color image (BGR, from OpenCV).
    angle_deg : float
        Angle in degrees relative to camera center (>0 right, <0 left).
    hfov_deg : float
        Horizontal field of view of the camera (total, in degrees).
    color : (B, G, R)
        Line color (default red).
    thickness : int
        Line thickness in pixels.
    """
    if cv2 is None or np is None:
        return frame_bgr

    H, W = frame_bgr.shape[:2]
    frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

    half_fov = hfov_deg / 2.0
    outside_fov = abs(angle_deg) > half_fov
    angle_clamped = max(-half_fov, min(half_fov, angle_deg))

    px_per_deg = W / hfov_deg
    x_center = W / 2.0
    x = int(round(x_center + angle_clamped * px_per_deg))
    x = max(0, min(W - 1, x))

    y_top_line = 0
    line = detect_bottom_line(frame_gray)
    if line is not None:
        if len(line) == 4:
            y1, y2, a, b = line
        else:
            _, y1, _, y2, a, b = line
        y_h = int(round(a * x + b))
        y_h = max(0, min(H - 1, y_h))
        y_top_line = y_h

    draw_thickness = thickness * 8 if outside_fov else thickness

    vis = frame_bgr.copy()
    cv2.line(vis, (x, H - 1), (x, y_top_line), color, draw_thickness)

    return vis


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Video Stream Recorder")
        self.geometry("640x480")

        self.sdp_path = tk.StringVar(value="")
        self.rtsp_url = tk.StringVar(value="")
        self.base_name = tk.StringVar(value="recording")
        self.output_dir = tk.StringVar(value="")
        self.show_stream = tk.BooleanVar(value=False)
        self.nmea_port_cam = tk.StringVar(value="")
        self.nmea_port_mast = tk.StringVar(value="")
        self.proc: subprocess.Popen | None = None
        self.ffmpeg_path = find_ffmpeg()
        self.preview_stop = threading.Event()
        self.preview_photo = None
        self.hist_photo = None
        self.preview_size = (640, 480)
        self.preview_fps = 12
        self.overlay_angle_deg = 10.0
        self.overlay_hfov_deg = 34.0
        self.angle_mast = 0.0
        self.angle_cam = 0.0
        self.nmea_thread_cam: threading.Thread | None = None
        self.nmea_thread_mast: threading.Thread | None = None
        self.nmea_stop = threading.Event()
        self.nmea_sock_cam: socket.socket | None = None
        self.nmea_sock_mast: socket.socket | None = None
        self.angle_lock = threading.Lock()

        # --- UI ---
        row = 0
        tk.Button(self, text="Load SDP…", command=self.load_sdp).grid(row=row, column=0, padx=10, pady=10, sticky="w")
        tk.Label(self, textvariable=self.sdp_path, anchor="w", fg="#333").grid(row=row, column=1, padx=10, pady=10, sticky="we")

        row += 1
        tk.Label(self, text="or RTSP endpoint:").grid(row=row, column=0, padx=10, pady=10, sticky="w")
        tk.Entry(self, textvariable=self.rtsp_url, width=40).grid(row=row, column=1, padx=10, pady=10, sticky="we")

        row += 1
        tk.Button(self, text="Choose output folder…", command=self.choose_output).grid(row=row, column=0, padx=10, pady=10, sticky="w")
        tk.Label(self, textvariable=self.output_dir, anchor="w", fg="#333").grid(row=row, column=1, padx=10, pady=10, sticky="we")

        row += 1
        tk.Label(self, text="Clip base name:").grid(row=row, column=0, padx=10, pady=10, sticky="w")
        tk.Entry(self, textvariable=self.base_name, width=20).grid(row=row, column=1, padx=10, pady=10, sticky="w")

        row += 1
        tk.Label(self, text="NMEA UDP port (camera):").grid(row=row, column=0, padx=10, pady=10, sticky="w")
        tk.Entry(self, textvariable=self.nmea_port_cam, width=12).grid(row=row, column=1, padx=10, pady=10, sticky="w")

        row += 1
        tk.Label(self, text="NMEA UDP port (mast):").grid(row=row, column=0, padx=10, pady=10, sticky="w")
        tk.Entry(self, textvariable=self.nmea_port_mast, width=12).grid(row=row, column=1, padx=10, pady=10, sticky="w")

        row += 1
        tk.Checkbutton(
            self,
            text="Show stream preview",
            variable=self.show_stream,
            command=self._toggle_preview_box,
        ).grid(row=row, column=0, padx=10, pady=(0, 10), sticky="w")

        row += 1
        self.start_btn = tk.Button(self, text="Start", command=self.toggle_start_stop)
        self.start_btn.grid(row=row, column=0, padx=10, pady=10, sticky="w")
        self.status_lbl = tk.Label(self, text="Idle", anchor="w")
        self.status_lbl.grid(row=row, column=1, padx=10, pady=10, sticky="we")

        # Row index for preview box so we can grid/ungrid cleanly
        self.preview_row_index = row + 1

        # Make right column stretch
        self.grid_columnconfigure(1, weight=1)

        # Ensure clean exit
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # Warn once if ffmpeg not located yet
        if self.ffmpeg_path is None:
            self.status_lbl.config(text="ffmpeg not found — select SDP/output then Start to retry")

        # Preview box (hidden until checkbox is checked)
        self.preview_frame = tk.Frame(self, borderwidth=1, relief="groove")
        self.preview_label = tk.Label(self.preview_frame, text="Live stream preview (uses current RTP/RTSP settings)")
        self.preview_label.pack(anchor="w", padx=8, pady=(6, 2))
        self.preview_canvas = tk.Label(
            self.preview_frame,
            text="Preview will appear here",
            bg="#111",
            fg="#eee",
        )
        self.preview_canvas.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        self.hist_label = tk.Label(self.preview_frame, text="Grayscale histogram", anchor="w")
        self.hist_label.pack(anchor="w", padx=8, pady=(0, 2))
        self.hist_canvas = tk.Label(self.preview_frame, text="Histogram will appear here", bg="#111", fg="#eee")
        self.hist_canvas.pack(fill="x", padx=8, pady=(0, 6))
        self.preview_status = tk.Label(self.preview_frame, text="", fg="#555", anchor="w")
        self.preview_status.pack(anchor="w", padx=8, pady=(0, 6))
        self.overlay_status = tk.Label(self.preview_frame, text="", fg="#555", anchor="w")
        self.overlay_status.pack(anchor="w", padx=8, pady=(0, 10))

    # --- Actions ---
    def load_sdp(self):
        path = filedialog.askopenfilename(
            title="Select SDP file",
            filetypes=[
                ("SDP files", ("*.sdp", "*.SDP")),
                ("All files", "*"),
            ],
        )
        if path:
            self.sdp_path.set(path)

    def choose_output(self):
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self.output_dir.set(path)

    def _toggle_preview_box(self):
        if self.show_stream.get():
            self.preview_frame.grid(
                row=self.preview_row_index,
                column=0,
                columnspan=2,
                sticky="nsew",
                padx=10,
                pady=(0, 10),
            )
            if not PIL_AVAILABLE:
                self.preview_status.config(text="Install Pillow to enable preview rendering.")
        else:
            self.preview_frame.grid_remove()
            self._stop_preview()

    def toggle_start_stop(self):
        if self.proc is None:
            self.start_ffmpeg()
        else:
            self.stop_ffmpeg()

    def _build_input_args(self, sdp: str, rtsp: str) -> list[str]:
        if sdp:
            return ["-protocol_whitelist", "file,rtp,udp", "-i", sdp]
        return ["-rtsp_transport", "tcp", "-i", rtsp]

    def start_ffmpeg(self):
        # Late resolve ffmpeg (in case PATH changed or bundle extracted)
        if self.ffmpeg_path is None:
            self.ffmpeg_path = find_ffmpeg()
        if self.ffmpeg_path is None:
            messagebox.showerror(
                "FFmpeg not found",
                "ffmpeg is missing. If you built with --add-binary, it's bundled automatically."
                "Otherwise put 'ffmpeg' next to this executable or in PATH.",
            )
            return

        sdp = self.sdp_path.get().strip()
        rtsp = self.rtsp_url.get().strip()
        out_dir = self.output_dir.get().strip()
        base_name = self.base_name.get().strip()
        if not sdp and not rtsp:
            messagebox.showerror("Missing source", "Load an SDP file or enter an RTSP URL.")
            return
        if sdp and not os.path.isfile(sdp):
            messagebox.showerror("Missing SDP", "The selected SDP file no longer exists. Load it again or use RTSP.")
            return
        if not out_dir:
            messagebox.showerror("Missing output", "Please choose an output folder.")
            return
        if not base_name:
            messagebox.showerror("Missing name", "Provide a base name for the clips.")
            return

        safe_base = "".join(c if (c.isalnum() or c in ("-", "_")) else "_" for c in base_name).strip("_")
        if not safe_base:
            messagebox.showerror("Invalid name", "Use letters, numbers, dashes or underscores for the base name.")
            return

        os.makedirs(out_dir, exist_ok=True)
        output_template = os.path.join(out_dir, f"{safe_base}_%Y%m%d-%H%M%S.mkv")

        input_args = self._build_input_args(sdp, rtsp)
        preview_requested = self.show_stream.get()
        preview_enabled = preview_requested and PIL_AVAILABLE

        cmd = [
            self.ffmpeg_path,
            *input_args,
        ]

        width, height = self.preview_size
        if preview_enabled:
            # One ffmpeg handles both recording and preview via filter_complex split.
            cmd += [
                "-filter_complex",
                f"[0:v]fps={self.preview_fps},scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black[vout]",
                "-map",
                "0",
                "-c",
                "copy",
                "-f",
                "segment",
                "-segment_time",
                "3600",
                "-reset_timestamps",
                "1",
                "-strftime",
                "1",
                output_template,
                "-map",
                "[vout]",
                "-an",
                "-c:v",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "pipe:1",
            ]
        else:
            cmd += [
                "-c",
                "copy",
                "-f",
                "segment",
                "-segment_time",
                "3600",
                "-reset_timestamps",
                "1",
                "-strftime",
                "1",
                output_template,
            ]
        status_text = "Recording from SDP…" if sdp else "Recording from RTSP…"

        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE if preview_enabled else None,
                stderr=subprocess.DEVNULL,
                bufsize=width * height * 3 if preview_enabled else -1,
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start ffmpeg: {e}")
            self.proc = None
            self._stop_nmea_listener()
            return

        self.status_lbl.config(text=status_text)
        self.start_btn.config(text="Stop")

        self._start_nmea_listener()

        if preview_enabled:
            overlay_ready = np is not None and cv2 is not None
            overlay_note = "" if overlay_ready else " (overlay disabled: install numpy + opencv-python)"
            self.preview_status.config(text=f"Preview running at ~{self.preview_fps} fps{overlay_note}…")
            if overlay_ready:
                self.hist_canvas.config(text="", image="")
            else:
                self.hist_canvas.config(
                    text="Histogram unavailable (requires numpy + opencv-python)",
                    image="",
                )
            self.preview_stop.clear()
            threading.Thread(
                target=self._run_preview,
                args=(self.proc, width, height),
                daemon=True,
            ).start()
        else:
            if preview_requested and not PIL_AVAILABLE:
                self.preview_status.config(text="Preview unavailable (Pillow not installed).")
            else:
                self.preview_status.config(text="")
            self._stop_preview()

        threading.Thread(target=self._wait_and_reset, daemon=True).start()

    def _make_hist_photo(self, frame_gray):
        if cv2 is None or np is None or not PIL_AVAILABLE:
            return None
        hist = cv2.calcHist([frame_gray], [0], None, [256], [0, 256]).flatten()
        if hist.size == 0:
            return None
        max_val = float(hist.max())
        if max_val <= 0.0:
            return None

        # Normalize to [0, 1] so we can scale bars to the canvas height.
        hist = hist / max_val
        width, height = 256, 120
        img = Image.new("RGB", (width, height), "black")
        draw = ImageDraw.Draw(img)
        for x, value in enumerate(hist):
            bar_h = int(value * (height - 1))
            draw.line([(x, height - 1), (x, height - 1 - bar_h)], fill="#39c", width=1)
        return ImageTk.PhotoImage(img)

    def _run_preview(self, proc: subprocess.Popen, width: int, height: int):
        if proc.stdout is None:
            return
        frame_size = width * height * 3
        overlay_ready = np is not None and cv2 is not None
        while not self.preview_stop.is_set():
            data = proc.stdout.read(frame_size)
            if not data or len(data) < frame_size:
                break
            try:
                hist_photo = None
                if overlay_ready:
                    arr = np.frombuffer(data, dtype=np.uint8)
                    if arr.size != frame_size:
                        break
                    arr = arr.reshape((height, width, 3))
                    bgr = arr[:, :, ::-1]
                    frame_gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
                    bgr = draw_angle_line(
                        bgr,
                        self.overlay_angle_deg,
                        hfov_deg=self.overlay_hfov_deg,
                        color=(0, 0, 255),
                        thickness=2,
                    )
                    hist_photo = self._make_hist_photo(frame_gray)
                    arr = bgr[:, :, ::-1]
                    img = Image.fromarray(arr, mode="RGB")
                else:
                    img = Image.frombytes("RGB", (width, height), data)
                photo = ImageTk.PhotoImage(img)
            except Exception:
                break

            def update_label(p=photo, h=hist_photo):
                self.preview_photo = p
                self.preview_canvas.config(image=p, text="")
                if h is not None:
                    self.hist_photo = h
                    self.hist_canvas.config(image=h, text="")
                elif overlay_ready:
                    self.hist_canvas.config(text="Histogram unavailable", image="")

            self.preview_canvas.after(0, update_label)

        self.preview_stop.set()
        self.preview_canvas.after(0, lambda: self.preview_status.config(text="Preview stopped"))

    def _stop_preview(self):
        self.preview_stop.set()
        # No-op otherwise; preview shares the main ffmpeg process when enabled.
        self.hist_canvas.config(text="Histogram will appear here", image="")
        self.hist_photo = None
        self.overlay_status.config(text="")

    def _start_nmea_listener(self):
        cam_port = self._parse_port(self.nmea_port_cam.get().strip())
        mast_port = self._parse_port(self.nmea_port_mast.get().strip())

        if cam_port is None and mast_port is None:
            self.overlay_status.config(text="Overlay angle: using static value")
            return

        self.nmea_stop.clear()

        if cam_port is not None:
            sock_cam = self._bind_udp(cam_port)
            if sock_cam:
                self.nmea_sock_cam = sock_cam
                self.nmea_thread_cam = threading.Thread(
                    target=self._nmea_loop, args=(sock_cam, "camangle"), daemon=True
                )
                self.nmea_thread_cam.start()
        if mast_port is not None:
            sock_mast = self._bind_udp(mast_port)
            if sock_mast:
                self.nmea_sock_mast = sock_mast
                self.nmea_thread_mast = threading.Thread(
                    target=self._nmea_loop, args=(sock_mast, "mastrot"), daemon=True
                )
                self.nmea_thread_mast.start()

        ports = []
        if cam_port is not None and self.nmea_sock_cam:
            ports.append(f"cam UDP {cam_port}")
        if mast_port is not None and self.nmea_sock_mast:
            ports.append(f"mast UDP {mast_port}")
        if ports:
            self.overlay_status.config(text="Listening NMEA on " + ", ".join(ports))
        else:
            self.overlay_status.config(text="NMEA listen failed; overlay uses static value")

    def _stop_nmea_listener(self):
        self.nmea_stop.set()
        for sock in (self.nmea_sock_cam, self.nmea_sock_mast):
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
        self.nmea_sock_cam = None
        self.nmea_sock_mast = None
        self.nmea_thread_cam = None
        self.nmea_thread_mast = None

    def _bind_udp(self, port: int) -> socket.socket | None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("", port))
            sock.settimeout(1.0)
            return sock
        except OSError as exc:
            self.overlay_status.config(text=f"NMEA listen failed on UDP {port}: {exc}")
            return None

    def _parse_port(self, port_str: str) -> int | None:
        if not port_str:
            return None
        try:
            port = int(port_str)
            if not (1 <= port <= 65535):
                raise ValueError
            return port
        except ValueError:
            return None

    def _nmea_loop(self, sock: socket.socket, expected_tag: str):
        while not self.nmea_stop.is_set():
            try:
                data, _ = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                text = data.decode("ascii", errors="ignore")
            except Exception:
                continue
            for line in text.splitlines():
                self._handle_nmea_line(line.strip(), expected_tag)

    def _handle_nmea_line(self, line: str, expected_tag: str):
        # Expect e.g. $INXDR,A,-10.9,D,CamAngle*55 or MastRot
        if not line.startswith("$INXDR"):
            return
        parts = line.split(",")
        if len(parts) < 5:
            return
        try:
            value = float(parts[2])
        except ValueError:
            return
        tag = parts[4].split("*", 1)[0]
        if tag.lower() != expected_tag:
            return
        with self.angle_lock:
            if tag.lower() == "camangle":
                self.angle_cam = value
            elif tag.lower() == "mastrot":
                self.angle_mast = value
            else:
                return
            self.overlay_angle_deg = self.angle_cam - self.angle_mast
            combined = self.overlay_angle_deg
        self.preview_canvas.after(
            0,
            lambda v=value, t=tag, c=combined: self.overlay_status.config(
                text=f"NMEA {t}: {v:.2f}°, combined overlay: {c:.2f}°"
            ),
        )

    def _wait_and_reset(self):
        if self.proc is None:
            return
        self.proc.wait()
        self.after(0, self._reset_ui_after_exit)

    def _reset_ui_after_exit(self):
        self.proc = None
        self.start_btn.config(text="Start")
        self.status_lbl.config(text="Idle")
        self._stop_preview()
        self._stop_nmea_listener()

    def stop_ffmpeg(self):
        if self.proc is None:
            return
        try:
            self.proc.terminate()
        except Exception:
            pass
        self._stop_preview()
        self._stop_nmea_listener()
        self.status_lbl.config(text="Stopping…")

    def on_close(self):
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        self._stop_preview()
        self._stop_nmea_listener()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
