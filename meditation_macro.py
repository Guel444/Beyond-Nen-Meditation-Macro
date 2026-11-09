"""Automatic meditation minigame helper for Beyond Nen (Roblox).

The application uses screen capture and visual detection to estimate the
closing-ring contact time, then sends synchronized mouse input on Windows.
"""

from __future__ import annotations

import csv
import ctypes
from ctypes import wintypes
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from statistics import median
import tkinter as tk
from tkinter import ttk

import cv2
import mss
import numpy as np
from pynput import keyboard

APP_TITLE = "Beyond Nen - Meditation Macro"
VERSION = "1.0.0"

# Stable visual reference: the ball's inner outline remains white. The outer
# approach ring is measured radially by brightness contrast, not by its hue.
WHITE_LOW = np.array([0, 0, 170], dtype=np.uint8)
WHITE_HIGH = np.array([179, 120, 255], dtype=np.uint8)
MIN_RING_SCORE = 30.0
MIN_RING_COVERAGE = 0.10
SCALE = 0.5

# Timing/tracking constants (screen pixels, seconds, milliseconds).
MIN_VALID_SCORE = 90.0
MIN_VALID_COVERAGE = 0.60
MIN_VALID_SAMPLES = 4
SPEED_WINDOW_S = 0.45
MIN_PAIR_DT_S = 0.06
MIN_SHRINK_SPEED = 15.0
MAX_SHRINK_SPEED = 220.0
MAX_SOFT_SHRINK_SPEED = 260.0
MAX_PREDICTION_AGE_S = 0.85
CONTACT_GAP_PX = 0.0
PREPOSITION_GAP_PX = 38.0
SCHEDULE_WINDOW_MS = 180.0
CLICK_HOLD_MS = 55
SOFT_MIN_SCORE = 40.0
SOFT_MIN_COVERAGE = 0.25
SOFT_MAX_CENTER_DRIFT = 20.0
SOFT_MAX_RADIUS_DRIFT = 12.0


@dataclass
class Target:
    x: float
    y: float
    inner_r: float
    outer_r: float
    score: float
    coverage: float
    circularity: float

    @property
    def gap(self) -> float:
        return max(0.0, self.outer_r - self.inner_r)


def detect_targets(frame_bgra: np.ndarray) -> list[Target]:
    """Fast, hue-independent detector for the meditation target."""
    height, width = frame_bgra.shape[:2]
    small = cv2.resize(
        frame_bgra,
        (max(1, int(width * SCALE)), max(1, int(height * SCALE))),
        interpolation=cv2.INTER_AREA,
    )
    hsv = cv2.cvtColor(small[:, :, :3], cv2.COLOR_BGR2HSV)
    white_mask = cv2.inRange(hsv, WHITE_LOW, WHITE_HIGH)
    contours, _ = cv2.findContours(white_mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    raw_candidates: list[tuple[float, float, float, float]] = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < 55:
            continue
        perimeter = cv2.arcLength(contour, True)
        if perimeter < 1.0:
            continue
        circularity = 4.0 * np.pi * area / (perimeter * perimeter)
        if circularity < 0.60:
            continue
        (cx, cy), radius = cv2.minEnclosingCircle(contour)
        if not 16.0 <= radius <= 36.0:
            continue
        raw_candidates.append((cx, cy, radius, circularity))

    candidates: list[tuple[float, float, float, float]] = []
    for candidate in sorted(raw_candidates, key=lambda item: item[3], reverse=True):
        cx, cy, radius, circularity = candidate
        duplicate = any(
            np.hypot(cx - old[0], cy - old[1]) <= 4.0
            and abs(radius - old[2]) <= 4.0
            for old in candidates
        )
        if not duplicate:
            candidates.append(candidate)

    if not candidates:
        return []

    value = hsv[:, :, 2]
    img_h, img_w = value.shape
    angles = np.linspace(0.0, 2.0 * np.pi, 180, endpoint=False, dtype=np.float32)
    cos_a = np.cos(angles)[None, :]
    sin_a = np.sin(angles)[None, :]
    detected: list[Target] = []

    for cx, cy, inner_r, circularity in candidates:
        radii = np.arange(
            max(inner_r * 1.10, inner_r + 3.0),
            min(inner_r * 2.75, inner_r + 70.0),
            0.75,
            dtype=np.float32,
        )
        if radii.size < 4:
            continue

        def sample_at(radial_values: np.ndarray) -> np.ndarray:
            xs = np.rint(cx + radial_values[:, None] * cos_a).astype(np.int32)
            ys = np.rint(cy + radial_values[:, None] * sin_a).astype(np.int32)
            np.clip(xs, 0, img_w - 1, out=xs)
            np.clip(ys, 0, img_h - 1, out=ys)
            return value[ys, xs].astype(np.float32)

        middle = sample_at(radii)
        inner_flank = sample_at(np.maximum(0.0, radii - 2.5))
        outer_flank = sample_at(radii + 2.5)
        flank = (inner_flank + outer_flank) * 0.5
        contrast = middle - flank
        scores = np.mean(np.maximum(contrast, 0.0), axis=1) + 0.07 * np.mean(middle, axis=1)
        best_index = int(np.argmax(scores))
        score = float(scores[best_index])
        coverage = float(np.mean(middle[best_index] > np.maximum(110.0, flank[best_index] + 28.0)))
        if score < MIN_RING_SCORE or coverage < MIN_RING_COVERAGE:
            continue

        detected.append(
            Target(
                x=float(cx / SCALE),
                y=float(cy / SCALE),
                inner_r=float(inner_r / SCALE),
                outer_r=float(radii[best_index] / SCALE),
                score=score,
                coverage=coverage,
                circularity=float(circularity),
            )
        )

    detected.sort(key=lambda target: target.score, reverse=True)
    return detected


def robust_shrink_speed(history: deque[tuple[float, float]], now: float) -> float | None:
    """Theil-Sen-like median of pairwise slopes from trusted measurements.

    Crucially, one bad final frame cannot create a huge speed estimate. Only
    plausible positive inward slopes are considered, and the median across
    multiple time-separated pairs is used instead of the latest adjacent pair.
    """
    points = [(gap, stamp) for gap, stamp in history if 0.0 <= now - stamp <= SPEED_WINDOW_S]
    slopes: list[float] = []
    for i, (gap_a, time_a) in enumerate(points):
        for gap_b, time_b in points[i + 1 :]:
            dt = time_b - time_a
            if dt < MIN_PAIR_DT_S:
                continue
            speed = (gap_a - gap_b) / dt
            if MIN_SHRINK_SPEED <= speed <= MAX_SHRINK_SPEED:
                slopes.append(float(speed))
    if len(slopes) < 3:
        return None
    return float(median(slopes))


ULONG_PTR = ctypes.c_size_t


def set_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _window_process_path(hwnd) -> str:
    if os.name != "nt" or not hwnd:
        return ""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    process = kernel32.OpenProcess(0x1000, False, pid.value)
    if not process:
        return ""
    try:
        query = kernel32.QueryFullProcessImageNameW
        query.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        query.restype = wintypes.BOOL
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buffer))
        if query(process, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return ""
    finally:
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle(process)


def get_foreground_info() -> tuple[bool, str]:
    if os.name != "nt":
        return True, ""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False, "sem janela ativa"
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(max(1, length + 1))
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    title = buffer.value
    exe = _window_process_path(hwnd)
    detail = title or (exe.rsplit("\\", 1)[-1] if exe else "janela sem título")
    return ("roblox" in title.lower() or "roblox" in exe.lower()), detail


def get_cursor_position() -> tuple[int, int]:
    if os.name != "nt":
        return (0, 0)

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    point = POINT()
    ctypes.set_last_error(0)
    if not user32.GetCursorPos(ctypes.byref(point)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(point.x), int(point.y)


def get_foreground_hwnd() -> int:
    """Return the current foreground HWND without querying process metadata."""
    if os.name != "nt":
        return 0
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    return int(user32.GetForegroundWindow() or 0)


def move_cursor_to(x: int, y: int) -> tuple[bool, str]:
    """Move and verify the cursor early, outside the timing-critical section."""
    if os.name != "nt":
        return False, "disponível apenas no Windows"
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    if not user32.SetCursorPos(int(x), int(y)):
        return False, f"SetCursorPos falhou (WinError {ctypes.get_last_error()})"
    try:
        px, py = get_cursor_position()
        if abs(px - int(x)) > 2 or abs(py - int(y)) > 2:
            return False, f"cursor fora do alvo: {px},{py} (esperado {x},{y})"
        return True, f"cursor pronto em {px},{py}"
    except Exception as exc:
        return False, f"não foi possível verificar o cursor: {exc}"


ULONG_PTR = ctypes.c_size_t

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]

class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]

class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]


def _absolute_mouse_coords(x: int, y: int, user32) -> tuple[int, int]:
    """Map physical screen pixels to SendInput's 0..65535 virtual-desktop space."""
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    left = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
    top = user32.GetSystemMetrics(77)    # SM_YVIRTUALSCREEN
    width = max(1, user32.GetSystemMetrics(78))   # SM_CXVIRTUALSCREEN
    height = max(1, user32.GetSystemMetrics(79))  # SM_CYVIRTUALSCREEN
    nx = round((int(x) - left) * 65535 / max(1, width - 1))
    ny = round((int(y) - top) * 65535 / max(1, height - 1))
    return max(0, min(65535, nx)), max(0, min(65535, ny))


def _send_input_event(user32, flags: int, dx: int = 0, dy: int = 0) -> tuple[int, int]:
    event = INPUT()
    event.type = 0  # INPUT_MOUSE
    event.mi = MOUSEINPUT(int(dx), int(dy), 0, int(flags), 0, 0)
    ctypes.set_last_error(0)
    sent = int(user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)))
    return sent, ctypes.get_last_error()


def click_at_screen(x: int, y: int, hold_ms: int = CLICK_HOLD_MS) -> tuple[float | None, float | None, str]:
    """
    At the scheduled instant, send a real absolute mouse MOVE followed by
    LEFTDOWN in one ordered SendInput batch, then LEFTUP after the hold.
    This differs from SetCursorPos + a separate legacy mouse_event click.
    """
    if os.name != "nt":
        return None, None, "SendInput disponível apenas no Windows"

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short

    ax, ay = _absolute_mouse_coords(int(x), int(y), user32)
    events = (INPUT * 2)()
    events[0].type = 0
    events[0].mi = MOUSEINPUT(ax, ay, 0, 0x0001 | 0x8000 | 0x4000, 0, 0)  # MOVE|ABSOLUTE|VIRTUALDESK
    events[1].type = 0
    events[1].mi = MOUSEINPUT(0, 0, 0, 0x0002, 0, 0)  # LEFTDOWN

    ctypes.set_last_error(0)
    sent = int(user32.SendInput(2, events, ctypes.sizeof(INPUT)))
    down_stamp = time.perf_counter()

    if sent != 2:
        _send_input_event(user32, 0x0004)  # try to release any partial/stale state
        error_code = ctypes.get_last_error()
        return None, None, (
            f"SendInput MOVE+DOWN retornou {sent}/2 (WinError {error_code}); "
            f"INPUT_SIZE={ctypes.sizeof(INPUT)}"
        )

    hold = max(45, min(int(hold_ms), 90)) / 1000.0
    probe_wait = min(0.006, hold / 4)
    time.sleep(probe_wait)
    key_state = int(user32.GetAsyncKeyState(0x01)) & 0x8000
    time.sleep(max(0.0, hold - probe_wait))

    up_sent, up_error = _send_input_event(user32, 0x0004)  # LEFTUP
    up_stamp = time.perf_counter()
    key_label = "DOWN" if key_state else "NOT_DOWN"
    detail = (
        f"SendInput MOVE+DOWN={sent}/2 • VK_LBUTTON={key_label} • "
        f"UP={up_sent}/1 • tela={x},{y} • abs={ax},{ay}"
    )
    if up_sent != 1:
        detail += f" • WinError_UP={up_error}"
    return down_stamp, up_stamp, detail


def click_current_cursor(x: int, y: int, hold_ms: int = CLICK_HOLD_MS) -> tuple[float | None, float | None, str]:
    """Scheduler wrapper: send the click at an explicit desktop coordinate."""
    return click_at_screen(x, y, hold_ms)


def wait_until_precise(deadline: float, stop_event: threading.Event) -> bool:
    """Wait to a perf_counter deadline; sleep coarsely, spin only for the final 2 ms."""
    while not stop_event.is_set():
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return True
        if remaining > 0.0025:
            stop_event.wait(remaining - 0.0015)
        else:
            # Final short spin avoids the scheduler granularity of time.sleep().
            pass
    return False


def begin_1ms_timer() -> bool:
    if os.name != "nt":
        return False
    try:
        winmm = ctypes.WinDLL("winmm")
        return winmm.timeBeginPeriod(1) == 0
    except Exception:
        return False


def end_1ms_timer(enabled: bool) -> None:
    if enabled:
        try:
            ctypes.WinDLL("winmm").timeEndPeriod(1)
        except Exception:
            pass


def fast_left_click(x: int, y: int, hold_ms: int = 55, move_cursor: bool = True) -> tuple[bool, str]:
    """Test click using the same synchronized SendInput path as automatic mode."""
    if os.name != "nt":
        return False, "clique automático disponível apenas no Windows"
    if move_cursor:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        user32.SetCursorPos.restype = wintypes.BOOL
        ctypes.set_last_error(0)
        if not user32.SetCursorPos(int(x), int(y)):
            return False, f"SetCursorPos falhou (WinError {ctypes.get_last_error()})"
    down_stamp, up_stamp, detail = click_at_screen(int(x), int(y), hold_ms)
    if down_stamp is None or up_stamp is None:
        return False, detail
    return True, detail


class MeditationMacro:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("640x650")
        self.root.resizable(False, False)

        self.running = False
        self.armed = False
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.listener = None
        self.show_debug = tk.BooleanVar(value=True)
        self.timing_offset_ms = tk.IntVar(value=0)
        self.status = tk.StringVar(value="Parado — F6 inicia o automático")
        self.x = tk.StringVar(value="0")
        self.y = tk.StringVar(value="0")
        self.w = tk.StringVar(value="0")
        self.h = tk.StringVar(value="0")

        self.cfg_timing_offset_ms = 0
        self.cfg_show_debug = True
        self.cfg_region: dict | None = None
        self.debug_enabled = True
        self.tracked_center: tuple[float, float] | None = None
        self.last_any_seen = 0.0

        self._build_ui()
        self._start_hotkeys()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _build_ui(self) -> None:
        ttk.Label(self.root, text="BEYOND NEN MEDITATION MACRO v1.0.0", font=("Segoe UI", 18, "bold")).pack(pady=(13, 2))
        ttk.Label(self.root, text="Detecção visual • temporização preditiva • clique sincronizado", foreground="#555").pack(pady=(0, 10))

        state = ttk.LabelFrame(self.root, text="Estado em tempo real")
        state.pack(fill="x", padx=12, pady=5)
        ttk.Label(state, textvariable=self.status, wraplength=580, justify="left").pack(anchor="w", padx=10, pady=9)

        actions = ttk.Frame(self.root)
        actions.pack(fill="x", padx=10, pady=5)
        ttk.Button(actions, text="INICIAR AUTO (F6)", command=self.start_auto).pack(side="left", expand=True, fill="x", padx=3)
        ttk.Button(actions, text="DIAGNÓSTICO (F8)", command=self.start_diagnostic).pack(side="left", expand=True, fill="x", padx=3)
        ttk.Button(actions, text="PARAR (F7)", command=self.stop).pack(side="left", expand=True, fill="x", padx=3)

        roi = ttk.LabelFrame(self.root, text="Região de captura (0 = tela inteira)")
        roi.pack(fill="x", padx=12, pady=5)
        row = ttk.Frame(roi)
        row.pack(fill="x", padx=8, pady=7)
        for label, var, width in [("X", self.x, 7), ("Y", self.y, 7), ("Largura", self.w, 9), ("Altura", self.h, 9)]:
            ttk.Label(row, text=label).pack(side="left", padx=(3, 2))
            ttk.Entry(row, textvariable=var, width=width).pack(side="left", padx=(0, 7))

        settings = ttk.LabelFrame(self.root, text="Ajuste fino do momento do clique")
        settings.pack(fill="x", padx=12, pady=5)
        self._slider(settings, "Ajuste do instante (ms)", self.timing_offset_ms, -150, 120)
        ttk.Label(
            settings,
            text="Positivo = clicar antes • Negativo = clicar depois. Padrão: 0 ms. Pressionamento: 55 ms.",
            foreground="#555",
            wraplength=560,
        ).pack(anchor="w", padx=10, pady=(0, 7))
        ttk.Checkbutton(self.root, text="Mostrar diagnóstico visual (F8)", variable=self.show_debug, command=self._update_debug_window).pack(anchor="w", padx=16, pady=5)

        instructions = ttk.LabelFrame(self.root, text="Atalhos")
        instructions.pack(fill="x", padx=12, pady=5)
        message = (
            "F6  — iniciar/ativar automático (Roblox precisa estar em foco)\n"
            "F8  — diagnóstico sem clicar\n"
            "F7 ou ESC  — parar imediatamente\n"
            "PAUSE/BREAK  — teste de clique na posição atual do mouse\n\n"
            "No instante programado, envia MOVE ABSOLUTE + LEFTDOWN como eventos Windows ordenados, mantém 55 ms e envia LEFTUP. O CSV registra a quantidade de eventos aceita pelo Windows."
        )
        ttk.Label(instructions, text=message, justify="left", wraplength=585).pack(anchor="w", padx=10, pady=8)
        ttk.Label(self.root, text="O envio inclui movimento absoluto do mouse junto com LEFTDOWN para sincronizar a posição interna do jogo. Ajustes valem ao rearmar com F6.", foreground="#666", wraplength=580).pack(padx=12, pady=(3, 8))

    @staticmethod
    def _slider(parent, label: str, variable: tk.IntVar, low: int, high: int) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text=label, width=27).pack(side="left")
        ttk.Scale(row, from_=low, to=high, variable=variable, orient="horizontal").pack(side="left", fill="x", expand=True, padx=6)
        ttk.Label(row, textvariable=variable, width=5).pack(side="right")

    def _update_debug_window(self) -> None:
        self.debug_enabled = bool(self.show_debug.get()) and self.running
        if not self.debug_enabled:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass

    def _start_hotkeys(self) -> None:
        def on_press(key):
            try:
                if key == keyboard.Key.f6:
                    self.root.after(0, self.start_auto)
                elif key == keyboard.Key.f7 or key == keyboard.Key.esc:
                    self.root.after(0, self.stop)
                elif key == keyboard.Key.f8:
                    self.root.after(0, self.start_diagnostic)
                elif key == keyboard.Key.pause:
                    self.root.after(0, self.test_click_at_cursor)
            except Exception:
                pass

        self.listener = keyboard.Listener(on_press=on_press)
        self.listener.daemon = True
        self.listener.start()

    def _launch(self, auto: bool) -> None:
        if auto:
            focused, details = get_foreground_info()
            if not focused:
                self.status.set(f"Roblox não está em primeiro plano ({details}). Clique no jogo e pressione F6.")
                return

        # Read Tk variables on the UI thread only; the capture worker uses plain values.
        self.cfg_timing_offset_ms = int(self.timing_offset_ms.get())
        if auto:
            self.show_debug.set(False)
        self.cfg_show_debug = bool(self.show_debug.get()) and not auto
        self.debug_enabled = self.cfg_show_debug
        try:
            roi_x, roi_y, roi_w, roi_h = [int(value.get()) for value in (self.x, self.y, self.w, self.h)]
            self.cfg_region = {"left": roi_x, "top": roi_y, "width": roi_w, "height": roi_h} if roi_w > 0 and roi_h > 0 else None
        except (ValueError, tk.TclError):
            self.cfg_region = None

        if auto:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass

        if self.running:
            self.armed = auto
            self.status.set("AUTOMÁTICO ATIVO — clique agendado" if auto else "Diagnóstico ativo — sem cliques")
            return

        self.running = True
        self.armed = auto
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._loop, name="MeditationDetector", daemon=True)
        self.worker.start()
        self.status.set("AUTOMÁTICO ATIVO — clique agendado" if auto else "Diagnóstico ativo — sem cliques")

    def start_auto(self) -> None:
        self._launch(True)

    def start_diagnostic(self) -> None:
        self.show_debug.set(True)
        self._launch(False)

    def test_click_at_cursor(self) -> None:
        focused, details = get_foreground_info()
        if not focused:
            self.status.set(f"TESTE CANCELADO: Roblox sem foco ({details}).")
            return
        try:
            px, py = get_cursor_position()
            ok, message = fast_left_click(px, py, hold_ms=20, move_cursor=False)
            self.status.set(("TESTE DE CLIQUE ENVIADO" if ok else "FALHA NO TESTE") + f" em {px},{py} • {message}")
        except Exception as exc:
            self.status.set(f"Falha no teste de clique: {exc}")

    def stop(self) -> None:
        self.armed = False
        self.running = False
        self.stop_event.set()
        self.status.set("Parado")
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass

    def _choose_target(self, candidates: list[Target], now: float) -> Target | None:
        previous = self.tracked_center
        if previous is not None and now - self.last_any_seen <= MAX_PREDICTION_AGE_S:
            near = [c for c in candidates if np.hypot(c.x - previous[0], c.y - previous[1]) <= 48.0]
            if near:
                return max(near, key=lambda c: c.score)
            fresh = [c for c in candidates if c.score >= 120.0 and c.coverage >= 0.70]
            if fresh:
                return max(fresh, key=lambda c: c.score)
            return None
        return max(candidates, key=lambda c: c.score) if candidates else None

    def _click_target(self, x: float, y: float, gap: float, speed: float, eta_ms: float) -> tuple[bool, str]:
        focused, title = get_foreground_info()
        if not focused:
            return False, f"FOCO PERDIDO ({title})"
        click_x, click_y = int(round(x)), int(round(y))
        ok, message = fast_left_click(click_x, click_y, hold_ms=50, move_cursor=True)
        if ok:
            return True, f"Clique enviado ({click_x},{click_y}) • gap previsto {gap:.1f}px • vel {speed:.0f}px/s • ETA {eta_ms:.0f}ms"
        return False, f"Falha no clique: {message}"

    def _loop(self) -> None:
        history: deque[tuple[float, float]] = deque(maxlen=48)
        strong_history: deque[tuple[float, float]] = deque(maxlen=48)
        inner_radius_history: deque[float] = deque(maxlen=8)
        center_history: deque[tuple[float, float]] = deque(maxlen=5)
        self.tracked_center = None
        self.last_any_seen = 0.0
        last_valid_time = 0.0
        last_valid_gap = 9999.0
        last_observed_gap = 9999.0
        last_observed_time = 0.0
        last_observed_was_soft = False
        last_strong_gap = 9999.0
        last_strong_time = 0.0
        last_speed = 0.0
        last_valid_target: Target | None = None
        clicked_this_target = False
        cursor_prepositioned = False
        armed_hwnd = 0
        last_preposition_xy: tuple[int, int] | None = None
        last_click_label = ""
        last_click_time = 0.0
        last_status_update = 0.0
        last_log_time = 0.0
        frame_times: deque[float] = deque(maxlen=40)
        last_detection_ms = 0.0
        last_eta_ms = 9999.0
        last_quality = 0.0
        last_coverage = 0.0
        log_file = None
        writer = None
        start_clock = time.perf_counter()
        timer_resolution_enabled = begin_1ms_timer()

        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "meditation_macro_log.csv")
        try:
            log_exists = os.path.exists(log_path) and os.path.getsize(log_path) > 0
            log_file = open(log_path, "a", newline="", encoding="utf-8")
            writer = csv.writer(log_file)
            if not log_exists:
                writer.writerow(["elapsed_s", "event", "x", "y", "gap_px", "score", "coverage", "speed_px_s", "eta_ms", "offset_ms", "note"])
                log_file.flush()

            with mss.mss() as sct:
                while not self.stop_event.is_set():
                    loop_start = time.perf_counter()
                    region = dict(self.cfg_region) if self.cfg_region is not None else dict(sct.monitors[1])
                    capture_start = time.perf_counter()
                    try:
                        raw = np.asarray(sct.grab(region))
                    except Exception as exc:
                        self.root.after(0, lambda message=str(exc): self.status.set(f"Erro na captura: {message}"))
                        time.sleep(0.02)
                        continue
                    capture_end = time.perf_counter()
                    # The frame represents a moment during grab(); midpoint reduces
                    # systematic timestamp bias from the capture call duration.
                    capture_stamp = (capture_start + capture_end) * 0.5

                    detect_start = time.perf_counter()
                    candidates = detect_targets(raw)
                    detect_end = time.perf_counter()
                    last_detection_ms = (detect_end - detect_start) * 1000.0
                    now = detect_end
                    target = self._choose_target(candidates, now)
                    observed_target: Target | None = None
                    quality_valid = False
                    observation_stamp = capture_stamp

                    # Detect a fresh cue at a new location, or a new cue at the
                    # same location after the previous click. Reset the tracker so
                    # it never carries an old radius/speed into the next ball.
                    if target is not None:
                        far = self.tracked_center is not None and np.hypot(
                            target.x - self.tracked_center[0], target.y - self.tracked_center[1]
                        ) > 48.0
                        predicted_now_gap = (
                            last_valid_gap - last_speed * max(0.0, now - last_valid_time)
                            if last_valid_time > 0.0 and last_speed > 0.0 else 9999.0
                        )
                        same_spot_new_cue = (
                            clicked_this_target
                            and target.score >= 120.0
                            and target.coverage >= 0.70
                            and target.gap >= max(24.0, predicted_now_gap + 16.0)
                        )
                        is_fresh = target.score >= 120.0 and target.coverage >= 0.70
                        if (far and is_fresh) or same_spot_new_cue:
                            history.clear()
                            strong_history.clear()
                            inner_radius_history.clear()
                            center_history.clear()
                            self.tracked_center = None
                            self.last_any_seen = 0.0
                            last_valid_time = 0.0
                            last_valid_gap = 9999.0
                            last_observed_gap = 9999.0
                            last_observed_time = 0.0
                            last_observed_was_soft = False
                            last_strong_gap = 9999.0
                            last_strong_time = 0.0
                            last_speed = 0.0
                            last_valid_target = None
                            clicked_this_target = False
                            cursor_prepositioned = False
                            armed_hwnd = 0
                            last_preposition_xy = None
                            last_click_label = ""
                            last_eta_ms = 9999.0
                        elif far:
                            target = None

                    if target is not None:
                        if self.tracked_center is None:
                            self.tracked_center = (target.x, target.y)
                        else:
                            if np.hypot(target.x - self.tracked_center[0], target.y - self.tracked_center[1]) <= 48.0:
                                self.last_any_seen = observation_stamp
                        if self.last_any_seen == 0.0:
                            self.last_any_seen = observation_stamp

                        strong_quality = (
                            target.score >= MIN_VALID_SCORE
                            and target.coverage >= MIN_VALID_COVERAGE
                            and target.circularity >= 0.60
                        )
                        stable_inner_radius = (
                            float(median(inner_radius_history))
                            if inner_radius_history else float(target.inner_r)
                        )
                        center_drift = (
                            float(np.hypot(target.x - self.tracked_center[0], target.y - self.tracked_center[1]))
                            if self.tracked_center is not None else 0.0
                        )
                        soft_quality = (
                            not strong_quality
                            and target.score >= SOFT_MIN_SCORE
                            and target.coverage >= SOFT_MIN_COVERAGE
                            and target.circularity >= 0.60
                            and last_strong_time > 0.0
                            and center_drift <= SOFT_MAX_CENTER_DRIFT
                            and abs(float(target.inner_r) - stable_inner_radius) <= SOFT_MAX_RADIUS_DRIFT
                        )

                        if strong_quality:
                            # A strong ring observation supersedes tentative fragments.
                            # This prevents a partial arc at the end of one cue from
                            # contaminating the next clear full-circle measurement.
                            if last_observed_was_soft:
                                history.clear()
                                history.extend(strong_history)
                                last_observed_gap = last_strong_gap
                                last_observed_time = last_strong_time
                                last_valid_gap = last_strong_gap
                                last_valid_time = last_strong_time
                                last_observed_was_soft = False

                            reference_gap = last_strong_gap
                            reference_time = last_strong_time
                            measurement_valid = True
                            if reference_time > 0.0:
                                dt = observation_stamp - reference_time
                                drop = reference_gap - target.gap
                                if (
                                    dt <= 0.0
                                    or target.gap > reference_gap + 3.0
                                    or drop > MAX_SHRINK_SPEED * dt + 4.0
                                ):
                                    measurement_valid = False

                            if measurement_valid:
                                center_history.append((target.x, target.y))
                                center = (
                                    float(median([p[0] for p in center_history])),
                                    float(median([p[1] for p in center_history])),
                                )
                                self.tracked_center = center
                                inner_radius_history.append(float(target.inner_r))
                                history.append((float(target.gap), observation_stamp))
                                strong_history.append((float(target.gap), observation_stamp))
                                last_valid_gap = float(target.gap)
                                last_valid_time = observation_stamp
                                last_observed_gap = float(target.gap)
                                last_observed_time = observation_stamp
                                last_strong_gap = float(target.gap)
                                last_strong_time = observation_stamp
                                last_observed_was_soft = False
                                last_valid_target = target
                                last_quality = target.score
                                last_coverage = target.coverage

                        elif soft_quality:
                            # Near contact the bright annulus can be partially covered
                            # by the ball edge. Its center/radius fragment is still useful
                            # for timing if it stays attached to the already locked cue.
                            dt = observation_stamp - last_observed_time
                            drop = last_observed_gap - target.gap
                            measurement_valid = (
                                dt > 0.0
                                and target.gap <= last_observed_gap + 3.0
                                and drop <= MAX_SOFT_SHRINK_SPEED * dt + 4.0
                                and drop >= -3.0
                            )
                            if measurement_valid:
                                history.append((float(target.gap), observation_stamp))
                                last_valid_gap = float(target.gap)
                                last_valid_time = observation_stamp
                                last_observed_gap = float(target.gap)
                                last_observed_time = observation_stamp
                                last_observed_was_soft = True
                                last_quality = target.score
                                last_coverage = target.coverage

                        if strong_quality or soft_quality:
                            robust_speed = robust_shrink_speed(history, observation_stamp)
                            if robust_speed is not None:
                                last_speed = robust_speed

                            if writer and now - last_log_time >= 0.04 and measurement_valid:
                                writer.writerow([
                                    f"{now-start_clock:.4f}",
                                    "sample_soft" if soft_quality and not strong_quality else "sample_strong",
                                    f"{self.tracked_center[0] if self.tracked_center else target.x:.1f}",
                                    f"{self.tracked_center[1] if self.tracked_center else target.y:.1f}",
                                    f"{target.gap:.2f}", f"{target.score:.1f}", f"{target.coverage:.3f}",
                                    f"{last_speed:.2f}", "", self.cfg_timing_offset_ms,
                                    "soft_fragment" if soft_quality and not strong_quality else "trusted_full_ring",
                                ])
                                log_file.flush()
                                last_log_time = now

                    predicted_gap = 9999.0
                    eta_ms = 9999.0
                    center = self.tracked_center
                    sample_age = (now - last_valid_time) if last_valid_time > 0 else 9999.0
                    enough_history = len(history) >= MIN_VALID_SAMPLES and last_speed >= MIN_SHRINK_SPEED

                    if center is not None and enough_history and 0.0 <= sample_age <= MAX_PREDICTION_AGE_S:
                        predicted_gap = last_valid_gap - last_speed * sample_age
                        eta_ms = ((predicted_gap - CONTACT_GAP_PX) / last_speed) * 1000.0
                        last_eta_ms = eta_ms

                        if self.armed and not clicked_this_target:
                            # 1) Move to the target while there is still time. Do not
                            #    move the cursor inside the timing-critical click path.
                            if (not cursor_prepositioned) and last_valid_gap <= PREPOSITION_GAP_PX:
                                focused, focus_details = get_foreground_info()
                                hwnd = get_foreground_hwnd()
                                if focused and hwnd:
                                    click_x = int(round(region["left"] + center[0]))
                                    click_y = int(round(region["top"] + center[1]))
                                    moved, move_note = move_cursor_to(click_x, click_y)
                                    if moved:
                                        cursor_prepositioned = True
                                        armed_hwnd = hwnd
                                        last_preposition_xy = (click_x, click_y)
                                        if writer:
                                            writer.writerow([
                                                f"{time.perf_counter()-start_clock:.6f}", "CURSOR_READY",
                                                f"{center[0]:.1f}", f"{center[1]:.1f}", f"{predicted_gap:.2f}",
                                                f"{last_quality:.1f}", f"{last_coverage:.3f}", f"{last_speed:.2f}",
                                                f"{eta_ms:.1f}", self.cfg_timing_offset_ms, move_note,
                                            ])
                                            log_file.flush()
                                else:
                                    last_click_label = f"Aguardando foco do Roblox ({focus_details})"

                            # 2) Schedule the click independently of the next capture frame.
                            #    This removes frame-loop quantization near the judgement point.
                            fire_deadline = (
                                last_valid_time
                                + max(0.0, last_valid_gap - CONTACT_GAP_PX) / last_speed
                                - self.cfg_timing_offset_ms / 1000.0
                            )
                            fire_eta_ms = (fire_deadline - now) * 1000.0
                            if cursor_prepositioned and fire_eta_ms <= SCHEDULE_WINDOW_MS:
                                if wait_until_precise(fire_deadline, self.stop_event):
                                    # HWND comparison is a cheap focus check; no process
                                    # metadata lookup is done in the timing-critical path.
                                    if get_foreground_hwnd() == armed_hwnd and armed_hwnd:
                                        click_x, click_y = last_preposition_xy
                                        down_stamp, up_stamp, input_detail = click_current_cursor(
                                            click_x, click_y, CLICK_HOLD_MS
                                        )
                                        clicked_this_target = True  # one attempt per target; never spam repeats
                                        if down_stamp is not None and up_stamp is not None:
                                            last_click_time = down_stamp
                                            schedule_error_ms = (down_stamp - fire_deadline) * 1000.0
                                            last_click_label = (
                                                f"{input_detail} • erro do agendamento {schedule_error_ms:+.1f} ms"
                                            )
                                            if writer:
                                                writer.writerow([
                                                    f"{down_stamp-start_clock:.6f}", "CLICK_DOWN",
                                                    f"{center[0]:.1f}", f"{center[1]:.1f}",
                                                    f"{predicted_gap:.2f}", f"{last_quality:.1f}", f"{last_coverage:.3f}",
                                                    f"{last_speed:.2f}", f"{(fire_deadline-down_stamp)*1000.0:.2f}",
                                                    self.cfg_timing_offset_ms, last_click_label,
                                                ])
                                                writer.writerow([
                                                    f"{up_stamp-start_clock:.6f}", "CLICK_UP",
                                                    f"{center[0]:.1f}", f"{center[1]:.1f}",
                                                    f"{predicted_gap:.2f}", f"{last_quality:.1f}", f"{last_coverage:.3f}",
                                                    f"{last_speed:.2f}", "", self.cfg_timing_offset_ms,
                                                    f"hold {(up_stamp-down_stamp)*1000.0:.1f} ms • {input_detail}",
                                                ])
                                                log_file.flush()
                                        else:
                                            last_click_label = f"FALHA DE INPUT: {input_detail}"
                                            if writer:
                                                writer.writerow([
                                                    f"{time.perf_counter()-start_clock:.6f}", "CLICK_INPUT_ERROR",
                                                    f"{center[0]:.1f}", f"{center[1]:.1f}",
                                                    f"{predicted_gap:.2f}", f"{last_quality:.1f}", f"{last_coverage:.3f}",
                                                    f"{last_speed:.2f}", f"{fire_eta_ms:.1f}", self.cfg_timing_offset_ms,
                                                    input_detail,
                                                ])
                                                log_file.flush()
                                    else:
                                        last_click_label = "CLIQUE CANCELADO: Roblox perdeu o foco antes do disparo"
                                        if writer:
                                            writer.writerow([
                                                f"{time.perf_counter()-start_clock:.6f}", "CLICK_SKIPPED",
                                                f"{center[0]:.1f}", f"{center[1]:.1f}",
                                                f"{predicted_gap:.2f}", f"{last_quality:.1f}", f"{last_coverage:.3f}",
                                                f"{last_speed:.2f}", f"{fire_eta_ms:.1f}", self.cfg_timing_offset_ms,
                                                last_click_label,
                                            ])
                                            log_file.flush()

                    elif last_valid_time > 0.0 and sample_age > MAX_PREDICTION_AGE_S:
                        # No trusted cue for too long: release this track and search anew.
                        history.clear()
                        strong_history.clear()
                        inner_radius_history.clear()
                        center_history.clear()
                        self.tracked_center = None
                        self.last_any_seen = 0.0
                        last_valid_time = 0.0
                        last_valid_gap = 9999.0
                        last_observed_gap = 9999.0
                        last_observed_time = 0.0
                        last_observed_was_soft = False
                        last_strong_gap = 9999.0
                        last_strong_time = 0.0
                        last_speed = 0.0
                        last_valid_target = None
                        clicked_this_target = False
                        cursor_prepositioned = False
                        armed_hwnd = 0
                        last_preposition_xy = None
                        last_click_label = ""
                        last_eta_ms = 9999.0
                        center = None
                        predicted_gap = 9999.0
                        eta_ms = 9999.0

                    if self.debug_enabled:
                        bgr = raw[:, :, :3].copy()
                        if center is not None and last_valid_target is not None:
                            c = (int(round(center[0])), int(round(center[1])))
                            inner_radius = max(1, int(round(last_valid_target.inner_r)))
                            predicted_outer = max(1, int(round(last_valid_target.inner_r + max(0.0, predicted_gap))))
                            cv2.circle(bgr, c, inner_radius, (255, 255, 255), 2)
                            cv2.circle(bgr, c, predicted_outer, (0, 255, 0), 2)
                            cv2.drawMarker(bgr, c, (0, 0, 255), cv2.MARKER_CROSS, 18, 2)
                            cv2.putText(bgr, f"gap previsto {predicted_gap:.1f}px | velocidade {last_speed:.1f}px/s", (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
                            cv2.putText(bgr, f"ETA {eta_ms:.0f}ms | ajuste {self.cfg_timing_offset_ms:+d}ms", (12, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
                            cv2.putText(bgr, f"score {last_quality:.0f} | cobertura {last_coverage:.2f} | detector {last_detection_ms:.1f}ms", (12, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
                            state = "AUTO ATIVO" if self.armed else "DIAGNOSTICO - SEM CLIQUES"
                            if clicked_this_target:
                                state += " • CLIQUE ENVIADO"
                            cv2.putText(bgr, state, (12, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 255, 0) if self.armed else (0, 220, 255), 2)
                        else:
                            cv2.putText(bgr, "Procurando alvo...", (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 220, 255), 2)
                            cv2.putText(bgr, f"detector {last_detection_ms:.1f}ms", (12, 64), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
                            cv2.putText(bgr, "AUTO ATIVO" if self.armed else "DIAGNOSTICO - SEM CLIQUES", (12, 94), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 255, 0) if self.armed else (0, 220, 255), 2)
                        cv2.imshow("Beyond Nen Meditation Macro - Diagnóstico (F8)", bgr)
                        cv2.waitKey(1)

                    frame_times.append(now)
                    if now - last_status_update >= 0.15:
                        fps = 0.0
                        if len(frame_times) >= 2:
                            span = frame_times[-1] - frame_times[0]
                            if span > 0:
                                fps = (len(frame_times) - 1) / span
                        if center is not None and enough_history and sample_age <= MAX_PREDICTION_AGE_S:
                            text = (
                                f"{'AUTO ATIVO' if self.armed else 'Diagnóstico'} • {fps:.0f} FPS • "
                                f"gap previsto {predicted_gap:.1f}px • vel {last_speed:.0f}px/s • ETA {eta_ms:.0f}ms"
                            )
                        else:
                            text = f"{'AUTO ATIVO' if self.armed else 'Diagnóstico'} • {fps:.0f} FPS • aguardando medições confiáveis"
                        if last_click_label:
                            text += f" • {last_click_label}"
                        self.root.after(0, lambda message=text: self.status.set(message))
                        last_status_update = now

                    # No post-detection sleep: the capture loop runs at its natural cadence.
                    # Yield minimally to avoid monopolizing a CPU core.
                    time.sleep(0.0005)

        except Exception as exc:
            self.root.after(0, lambda message=str(exc): self.status.set(f"Erro no macro: {message}"))
        finally:
            end_1ms_timer(timer_resolution_enabled)
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass
            if log_file:
                try:
                    log_file.flush()
                    log_file.close()
                except Exception:
                    pass
            self.running = False
            self.armed = False

    def close(self) -> None:
        self.stop()
        try:
            if self.listener is not None:
                self.listener.stop()
        except Exception:
            pass
        self.root.destroy()


def main() -> None:
    set_dpi_awareness()
    try:
        cv2.setNumThreads(1)
    except Exception:
        pass
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except Exception:
        pass
    MeditationMacro(root)
    root.mainloop()


if __name__ == "__main__":
    main()
