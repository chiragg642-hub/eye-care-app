import ctypes
import json
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

try:
    import comtypes
except ImportError:
    comtypes = None

try:
    from pycaw.pycaw import AudioUtilities
except ImportError:
    AudioUtilities = None

try:
    import pystray
    from pystray import MenuItem as item
except ImportError:
    pystray = None
    item = None

try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:
    Image = None
    ImageDraw = None
    ImageTk = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ---------------- SETTINGS ---------------- #
settings = {
    "idle_threshold": 120,
    "work_interval": 1200,
    "break_duration": 20,
    "break_mode": "full_screen",
    "snooze_minutes": 5,
}

running = False
screen_time = 0
settings_window_open = False
settings_lock = threading.Lock()
ui_queue = queue.Queue()
root = None
tray_icon = None
break_window_ref = None
break_snooze_timer = None

if getattr(sys, "frozen", False):
    APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "EyeCareApp")
    os.makedirs(APP_DIR, exist_ok=True)
    BUNDLED_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLED_SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")

COLORS = {
    "bg": "#F5F7FA",
    "panel": "#FFFFFF",
    "panel_alt": "#EEF3F9",
    "text": "#1F2937",
    "muted": "#5B6472",
    "accent": "#1F4E79",
    "accent_dark": "#183B5B",
    "success": "#2D6A4F",
    "danger": "#B42318",
    "shadow": "#D7DEEA",
}


def apply_theme(window):
    window.configure(bg=COLORS["bg"])
    style = ttk.Style(window)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("App.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI", 10))
    style.configure("Header.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI", 10, "bold"))
    style.configure("Accent.TButton", foreground="white", background=COLORS["accent"], font=("Segoe UI", 10, "bold"))
    style.map("Accent.TButton", background=[("active", COLORS["accent_dark"])])
    style.configure("TCombobox", fieldbackground=COLORS["panel"], background=COLORS["panel"], foreground=COLORS["text"], font=("Segoe UI", 10))
    style.map("TCombobox", fieldbackground=[("readonly", COLORS["panel"])], selectbackground=[("readonly", COLORS["panel"])], selectforeground=[("readonly", COLORS["text"])])

# ---------------- UI HELPER ---------------- #

def initialize_root():
    global root
    if root is not None:
        return root

    root = tk.Tk()
    root.withdraw()
    root.protocol("WM_DELETE_WINDOW", lambda: None)
    root.after(100, process_ui_queue)

    # Set application/taskbar icon to match tray icon when possible
    try:
        # Try .ico for Windows taskbar first
        icon_file = None
        if Image is not None:
            icon_file = ensure_iconfile()
        if icon_file:
            try:
                root.iconbitmap(icon_file)
                root._icon_path = icon_file
            except Exception:
                # fallback to PhotoImage if iconbitmap isn't supported
                if ImageTk is not None:
                    tk_icon = ImageTk.PhotoImage(create_icon())
                    root.iconphoto(False, tk_icon)
                    root._tk_icon = tk_icon
        else:
            if Image is not None and ImageTk is not None:
                tk_icon = ImageTk.PhotoImage(create_icon())
                root.iconphoto(False, tk_icon)
                # keep a ref so it's not garbage-collected
                root._tk_icon = tk_icon
    except Exception:
        logging.exception("Could not set taskbar icon")
    return root


def enqueue_ui_action(action):
    """Thread-safe queue used by tray callbacks and worker threads."""
    if root is None:
        return
    ui_queue.put(action)
    


def process_ui_queue():
    if root is None or not root.winfo_exists():
        return

    while True:
        try:
            action = ui_queue.get_nowait()
        except queue.Empty:
            break

        try:
            action()
        except Exception:
            logging.exception("UI task failed")

    try:
        root.after(100, process_ui_queue)
    except RuntimeError:
        pass

# ---------------- SYSTEM FUNCTIONS ---------------- #

def get_idle_time():
    if os.name != "nt":
        return 0.0

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(lii)

    user32.GetLastInputInfo(ctypes.byref(lii))
    millis = kernel32.GetTickCount() - lii.dwTime
    return millis / 1000.0


def is_audio_playing():
    if AudioUtilities is None:
        return False

    try:
        sessions = AudioUtilities.GetAllSessions()
        for session in sessions:
            if session.State == 1:  # Active
                return True
    except Exception:
        logging.exception("Audio detection check failed")
        return False
    return False


def _open_settings_ui():
    global settings_window_open

    if settings_window_open:
        return

    window = tk.Toplevel(root)
    settings_window_open = True

    window.title("Eye Care Settings")
    window.geometry("420x420")
    window.resizable(False, False)

    window.lift()
    window.attributes("-topmost", True)
    window.after(200, lambda: window.attributes("-topmost", False))
    window.focus_force()

    # Ensure settings window uses the same icon as the app/tray when available
    try:
        # Prefer iconbitmap path when available (Windows taskbar)
        if hasattr(root, "_icon_path") and root._icon_path:
            try:
                window.iconbitmap(root._icon_path)
            except Exception:
                pass
        elif hasattr(root, "_tk_icon"):
            window.iconphoto(False, root._tk_icon)
    except Exception:
        logging.exception("Failed to set settings window icon")

    # Do not use grab_set() for tray apps (can block focus on some systems)
    # Build UI inside a visible panel to avoid theme/packing issues
    try:
        apply_theme(window)
        panel = tk.Frame(window, bg=COLORS["panel"], bd=0)
        panel.pack(fill="both", expand=True, padx=12, pady=12)
    except Exception:
        logging.exception("Failed to prepare settings window UI")
        # Fallback: ensure window is visible even if theming fails
        panel = window

    idle_options = {
        "1 minute": 60,
        "2 minutes": 120,
        "3 minutes": 180,
        "5 minutes": 300,
        "10 minutes": 600,
    }

    work_options = {
        "10 minutes": 600,
        "20 minutes": 1200,
        "30 minutes": 1800,
        "40 minutes": 2400,
        "60 minutes": 3600,
    }

    break_options = {
        "20 seconds": 20,
        "30 seconds": 30,
        "1 minute": 60,
        "2 minutes": 120,
    }

    snooze_options = {
        "2 minutes": 2,
        "5 minutes": 5,
        "10 minutes": 10,
        "15 minutes": 15,
        "30 minutes": 30,
    }

    mode_options = {
        "Notification": "notification",
        "Full blackout": "full_screen",
    }

    with settings_lock:
        current_idle = settings.get("idle_threshold", 120)
        current_work = settings.get("work_interval", 1200)
        current_break = settings.get("break_duration", 20)
        current_mode = settings.get("break_mode", "full_screen")
        current_snooze = settings.get("snooze_minutes", 5)

    idle_reverse = {v: k for k, v in idle_options.items()}
    work_reverse = {v: k for k, v in work_options.items()}
    break_reverse = {v: k for k, v in break_options.items()}
    snooze_reverse = {v: k for k, v in snooze_options.items()}
    mode_reverse = {v: k for k, v in mode_options.items()}

    tk.Label(panel, text="Idle Threshold", font=("Segoe UI", 10, "bold"), bg=COLORS["panel"], fg=COLORS["text"]).pack(pady=(18, 6))
    idle_var = tk.StringVar(value=idle_reverse.get(current_idle, "2 minutes"))
    idle_combo = ttk.Combobox(panel, textvariable=idle_var, values=list(idle_options.keys()), state="readonly")
    idle_combo.pack(fill="x", padx=6)

    tk.Label(panel, text="Work Interval", font=("Segoe UI", 10, "bold"), bg=COLORS["panel"], fg=COLORS["text"]).pack(pady=(16, 6))
    work_var = tk.StringVar(value=work_reverse.get(current_work, "20 minutes"))
    work_combo = ttk.Combobox(panel, textvariable=work_var, values=list(work_options.keys()), state="readonly")
    work_combo.pack(fill="x", padx=6)

    tk.Label(panel, text="Break Duration", font=("Segoe UI", 10, "bold"), bg=COLORS["panel"], fg=COLORS["text"]).pack(pady=(16, 6))
    break_var = tk.StringVar(value=break_reverse.get(current_break, "20 seconds"))
    break_combo = ttk.Combobox(panel, textvariable=break_var, values=list(break_options.keys()), state="readonly")
    break_combo.pack(fill="x", padx=6)

    tk.Label(panel, text="Break Mode", font=("Segoe UI", 10, "bold"), bg=COLORS["panel"], fg=COLORS["text"]).pack(pady=(16, 6))
    mode_var = tk.StringVar(value=mode_reverse.get(current_mode, "Full blackout"))
    mode_combo = ttk.Combobox(panel, textvariable=mode_var, values=list(mode_options.keys()), state="readonly")
    mode_combo.pack(fill="x", padx=6)

    tk.Label(panel, text="Snooze Duration", font=("Segoe UI", 10, "bold"), bg=COLORS["panel"], fg=COLORS["text"]).pack(pady=(16, 6))
    snooze_var = tk.StringVar(value=snooze_reverse.get(current_snooze, "5 minutes"))
    snooze_combo = ttk.Combobox(panel, textvariable=snooze_var, values=list(snooze_options.keys()), state="readonly")
    snooze_combo.pack(fill="x", padx=6)

    def close_window():
        global settings_window_open
        settings_window_open = False
        window.destroy()

    def save():
        try:
            with settings_lock:
                updated = {
                    "idle_threshold": idle_options[idle_var.get()],
                    "work_interval": work_options[work_var.get()],
                    "break_duration": break_options[break_var.get()],
                    "break_mode": mode_options[mode_var.get()],
                    "snooze_minutes": snooze_options[snooze_var.get()],
                }
                previous = settings.copy()
                settings.update(updated)
                try:
                    save_settings_to_file()
                except OSError:
                    settings.clear()
                    settings.update(previous)
                    raise
            close_window()
        except Exception:
            messagebox.showerror("Save Error", "Could not save settings. Check that the app folder is writable.")

    # Use a regular tk.Button for predictable coloring across platforms
    save_button = tk.Button(
        panel,
        text="Save",
        command=save,
        bg=COLORS["accent"],
        fg="white",
        activebackground=COLORS["accent_dark"],
        bd=0,
        font=("Segoe UI", 10, "bold"),
    )
    save_button.pack(pady=(20, 20), ipadx=18, ipady=8)
    window.protocol("WM_DELETE_WINDOW", close_window)


def open_settings(icon=None, item=None):
    enqueue_ui_action(_open_settings_ui)


def load_settings():
    """Load known, valid values; ignore invalid entries and retain safe defaults."""
    global settings

    defaults = {
        "idle_threshold": 120,
        "work_interval": 1200,
        "break_duration": 20,
        "break_mode": "full_screen",
        "snooze_minutes": 5,
    }
    allowed = {
        "idle_threshold": {60, 120, 180, 300, 600},
        "work_interval": {600, 1200, 1800, 2400, 3600},
        "break_duration": {20, 30, 60, 120},
        "break_mode": {"notification", "full_screen"},
        "snooze_minutes": {2, 5, 10, 15, 30},
    }
    loaded = {}
    settings_source = SETTINGS_FILE
    if not os.path.exists(settings_source) and getattr(sys, "frozen", False):
        settings_source = BUNDLED_SETTINGS_FILE
    try:
        with open(settings_source, "r", encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(data, dict):
            for key, options in allowed.items():
                value = data.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value in options:
                    loaded[key] = value
                elif isinstance(value, str) and value in options:
                    loaded[key] = value
                elif key in data:
                    logging.warning("Ignoring invalid setting: %s", key)
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError):
        logging.warning("Could not read settings file; using defaults.")

    settings = {**defaults, **loaded}


def save_settings_to_file():
    """Write settings atomically so interruption cannot leave a partial JSON file."""
    temporary_path = SETTINGS_FILE + ".tmp"
    try:
        with open(temporary_path, "w", encoding="utf-8") as file:
            json.dump(settings, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, SETTINGS_FILE)
    except OSError:
        try:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
        except OSError:
            logging.exception("Could not remove temporary settings file")
        logging.exception("Could not write settings file")
        raise

# ---------------- TRACKER ---------------- #

def tracker():
    global running, screen_time

    if comtypes is not None:
        try:
            comtypes.CoInitialize()
        except Exception:
            logging.exception("COM initialization failed")

    logging.info("Tracker started...")
    previous_tick = time.monotonic()
    try:
        while running:
            with settings_lock:
                idle_threshold = settings["idle_threshold"]
                work_interval = settings["work_interval"]

            now = time.monotonic()
            elapsed = max(0.0, now - previous_tick)
            previous_tick = now
            idle = get_idle_time()
            audio = is_audio_playing()

            # Count active input, or active audio when the user is away from input.
            active = idle < idle_threshold or audio
            if active:
                screen_time += elapsed

            if screen_time >= work_interval:
                logging.info("Work interval reached; triggering break")
                break_timer()
                screen_time = 0.0

            time.sleep(1.0)
    finally:
        if comtypes is not None:
            try:
                comtypes.CoUninitialize()
            except Exception:
                logging.exception("COM cleanup failed")
        logging.info("Tracker stopped.")


def _show_notification_end_ui(message):
    if root is None:
        return
    end_window = tk.Toplevel(root)
    end_window.title("Eye Care")
    end_window.geometry("420x170")
    end_window.transient(root)
    end_window.attributes("-topmost", True)
    end_window.configure(bg=COLORS["bg"])

    label = tk.Label(
        end_window,
        text=message,
        font=("Segoe UI", 18, "bold"),
        fg=COLORS["text"],
        bg=COLORS["bg"],
        wraplength=360,
        justify="center",
    )
    label.pack(expand=True, padx=24, pady=(16, 8))

    ok_button = ttk.Button(end_window, text="OK", command=end_window.destroy, style="Accent.TButton")
    ok_button.pack(pady=(0, 18), ipadx=14, ipady=6)


def _show_notification_break_ui():
    global break_window_ref

    if break_window_ref is not None and break_window_ref.winfo_exists():
        return

    break_window = tk.Toplevel(root)
    break_window_ref = break_window
    break_window.title("Eye Break")
    break_window.geometry("460x230")
    break_window.attributes("-topmost", True)
    break_window.configure(bg=COLORS["bg"])
    apply_theme(break_window)

    main_text = "Take a quick break and look away from the screen."
    label = tk.Label(
        break_window,
        text=main_text,
        font=("Segoe UI", 17, "bold"),
        fg=COLORS["text"],
        bg=COLORS["bg"],
        wraplength=360,
        justify="center",
    )
    label.pack(pady=(22, 8))

    countdown_label = tk.Label(
        break_window,
        text="",
        font=("Segoe UI", 22, "bold"),
        fg=COLORS["accent"],
        bg=COLORS["bg"],
    )
    countdown_label.pack(pady=8)

    with settings_lock:
        seconds_left = settings["break_duration"]
        snooze_minutes = settings["snooze_minutes"]

    def on_snooze():
        global break_snooze_timer
        if break_window_ref is not None and break_window_ref.winfo_exists():
            break_window_ref.destroy()
            break_window_ref = None

        if break_snooze_timer is not None:
            break_snooze_timer.cancel()

        def delayed_retrigger():
            show_break_screen()

        break_snooze_timer = threading.Timer(snooze_minutes * 60, delayed_retrigger)
        break_snooze_timer.daemon = True
        break_snooze_timer.start()

    snooze_button = ttk.Button(
        break_window,
        text=f"Snooze {snooze_minutes} min",
        command=on_snooze,
        style="Accent.TButton",
    )
    snooze_button.pack(pady=(12, 0), ipadx=18, ipady=7)

    def update_countdown():
        nonlocal seconds_left

        if break_window.winfo_exists() and seconds_left > 0:
            countdown_label.config(text=f"Break ends in {seconds_left} seconds")
            seconds_left -= 1
            break_window.after(1000, update_countdown)
        else:
            break_window.destroy()
            global break_window_ref
            break_window_ref = None
            _show_notification_end_ui("Break over. You can get back to work.")

    update_countdown()


def _show_fullscreen_break_ui():
    global break_window_ref

    if break_window_ref is not None and break_window_ref.winfo_exists():
        return

    break_window = tk.Toplevel(root)
    break_window_ref = break_window
    break_window.attributes("-fullscreen", True)
    break_window.attributes("-topmost", True)
    break_window.configure(bg="#0F172A")
    break_window.title("Eye Break")

    message = "You need to stop working right now and give your eyes some rest."
    label = tk.Label(
        break_window,
        text=message,
        font=("Segoe UI", 30, "bold"),
        fg="#F8FAFC",
        bg="#0F172A",
        wraplength=900,
        justify="center",
    )
    label.pack(expand=True)

    countdown_label = tk.Label(
        break_window,
        text="",
        font=("Segoe UI", 26, "bold"),
        fg="#E2E8F0",
        bg="#0F172A",
    )
    countdown_label.pack(pady=20)

    with settings_lock:
        seconds_left = settings["break_duration"]
        snooze_minutes = settings["snooze_minutes"]

    def on_snooze():
        global break_snooze_timer
        if break_window_ref is not None and break_window_ref.winfo_exists():
            break_window_ref.destroy()
            break_window_ref = None

        if break_snooze_timer is not None:
            break_snooze_timer.cancel()

        def delayed_retrigger():
            show_break_screen()

        break_snooze_timer = threading.Timer(snooze_minutes * 60, delayed_retrigger)
        break_snooze_timer.daemon = True
        break_snooze_timer.start()

    snooze_button = ttk.Button(
        break_window,
        text=f"Snooze {snooze_minutes} min",
        command=on_snooze,
        style="Accent.TButton",
    )
    snooze_button.pack(pady=20, ipadx=22, ipady=9)

    def update_countdown():
        nonlocal seconds_left

        if break_window.winfo_exists() and seconds_left > 0:
            countdown_label.config(text=f"Break ends in {seconds_left} seconds")
            seconds_left -= 1
            break_window.after(1000, update_countdown)
        else:
            break_window.destroy()
            global break_window_ref
            break_window_ref = None
            _show_notification_end_ui("Break over. You can resume work.")

    update_countdown()


def _show_break_screen_ui():
    with settings_lock:
        break_mode = settings.get("break_mode", "full_screen")

    if break_mode == "notification":
        _show_notification_break_ui()
    else:
        _show_fullscreen_break_ui()


def show_break_screen():
    enqueue_ui_action(_show_break_screen_ui)


def break_timer():
    show_break_screen()

# ---------------- CONTROL ---------------- #

def update_tray_menu():
    """Refresh enabled states after tracker state changes outside menu rendering."""
    if tray_icon is not None:
        try:
            tray_icon.update_menu()
        except Exception:
            logging.exception("Could not refresh tray menu")


def start_tracker(icon=None, item=None):
    global running
    if not running:
        running = True
        threading.Thread(target=tracker, daemon=True).start()
        update_tray_menu()


def stop_tracker(icon=None, item=None):
    global running
    if running:
        running = False
        update_tray_menu()


def quit_app(icon=None, item=None):
    global running, tray_icon
    running = False
    if tray_icon is not None:
        tray_icon.stop()
    if root is not None:
        root.quit()
        root.destroy()
    # Clean up


# ---------------- TRAY ICON ---------------- #

def create_icon():
    if Image is None or ImageDraw is None:
        raise RuntimeError("Pillow is required to create the tray icon.")

    image = Image.new("RGB", (64, 64), "black")
    draw = ImageDraw.Draw(image)
    draw.ellipse((16, 16, 48, 48), fill="white")
    return image


def ensure_iconfile():
    """Create an .ico file next to the script from the PIL image so Windows can use it for taskbar icons."""
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(base_dir, "eye_care_icon.ico")
        if os.path.exists(icon_path):
            return icon_path
        img = create_icon()
        # Save as ICO (Pillow supports saving as .ico)
        img.save(icon_path, format="ICO")
        return icon_path
    except Exception:
        logging.exception("Could not create .ico file for taskbar icon")
        return None


def setup_tray():
    global tray_icon

    if pystray is None or item is None or Image is None:
        logging.error("Tray dependencies are missing; install pystray, pillow, and pywin32.")
        return

    tray_icon = pystray.Icon("EyeCareApp")
    tray_icon.icon = create_icon()
    tray_icon.title = "Eye Care App"
    tray_icon.menu = pystray.Menu(
        item("Start", start_tracker, enabled=lambda menu_item: not running),
        item("Stop", stop_tracker, enabled=lambda menu_item: running),
        item("Settings", open_settings),
        item("Exit", quit_app),
    )
    tray_icon.run_detached()


# ---------------- MAIN ---------------- #

if __name__ == "__main__":
    load_settings()
    initialize_root()
    threading.Thread(target=setup_tray, daemon=True).start()
    root.mainloop()

