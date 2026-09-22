import os
import sys
import time
import json
import queue
import threading
import subprocess
from ftplib import FTP
from pathlib import Path
import re
import tkinter as tk
from tkinter import ttk, font, filedialog, messagebox
import tkinter.scrolledtext as scrolledtext

# --- High DPI Awareness for Windows ---
if sys.platform == "win32":
    try:
        import ctypes
        # SetProcessDpiAwareness(2) for Per-Monitor High DPI V2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# --- Dependency check (only when running from source, not frozen) ---
def ensure_requirements():
    if getattr(sys, 'frozen', False):
        return  # Frozen executable has everything bundled
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    req_file = os.path.join(script_dir, "requirements.txt")
    if os.path.exists(req_file):
        try:
            import watchdog
        except ImportError:
            print("Installing required dependencies (watchdog)...")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req_file])
            except Exception as e:
                print(f"Warning: Failed to install requirements: {e}")

ensure_requirements()

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileCreatedEvent
except ImportError:
    Observer = None
    FileSystemEventHandler = object
    FileCreatedEvent = None

# --- Path Resolution Helpers ---
def get_bundle_dir():
    """Return the temporary folder where PyInstaller extracts bundled files."""
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

def get_app_dir():
    """Return the folder where the EXE (or main script) is located."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

BUNDLE_DIR = get_bundle_dir()
APP_DIR = get_app_dir()
CONFIG_FILE = os.path.join(APP_DIR, "watcher_config.json")

DEFAULT_CONFIG = {
    "watch_dir": "",
    "output_dir": "",
    "trim_unused": False,
    "thread_count": "4",
    "scan_delay": "2",
    "delete_iso": True,
    "process_timeout": "0",
    "iso2god_binary": "",
    "use_ftp": False,
    "ip_addr": "",
    "ftp_port": "21",
    "ftp_user": "",
    "ftp_pass": "",
    "drv_name": "Hdd1"
}

class IsoHandler(FileSystemEventHandler):
    def __init__(self, queue_obj, extensions=('.iso',)):
        super().__init__()
        self.queue = queue_obj
        self.extensions = extensions
        self.processing = set()
        self.last_event_time = {}
        self.scan_delay = 2.0

    def set_scan_delay(self, delay):
        try:
            self.scan_delay = float(delay)
        except ValueError:
            self.scan_delay = 2.0

    def on_created(self, event):
        if not event.is_directory and event.src_path.lower().endswith(self.extensions):
            current_time = time.time()
            if event.src_path in self.last_event_time:
                if current_time - self.last_event_time[event.src_path] < self.scan_delay:
                    return
            self.last_event_time[event.src_path] = current_time
            if event.src_path not in self.processing:
                self.queue.put(event.src_path)
                self.processing.add(event.src_path)

class DirectoryWatcher(threading.Thread):
    def __init__(self, path, handler):
        super().__init__(daemon=True)
        self.path = path
        self.handler = handler
        self._stop_event = threading.Event()
        self._last_check = {}

    def stop(self):
        self._stop_event.set()

    def check_directory(self):
        try:
            if not os.path.exists(self.path):
                return
            current_files = set()
            for file in os.listdir(self.path):
                if file.lower().endswith('.iso'):
                    filepath = os.path.join(self.path, file)
                    current_files.add(filepath)
                    current_time = time.time()
                    if filepath not in self._last_check:
                        self._last_check[filepath] = current_time
                        if FileCreatedEvent:
                            event = FileCreatedEvent(filepath)
                            self.handler.on_created(event)
                        else:
                            if filepath not in self.handler.processing:
                                self.handler.queue.put(filepath)
                                self.handler.processing.add(filepath)
            for filepath in list(self._last_check.keys()):
                if filepath not in current_files:
                    del self._last_check[filepath]
        except Exception as e:
            print(f"Error checking directory: {e}")

    def run(self):
        while not self._stop_event.is_set():
            self.check_directory()
            time.sleep(1)

class Iso2GodGUI:
    def __init__(self):
        self.app = tk.Tk()
        self.app.title("ISO2GOD-RS GUI - Xbox 360 자동 변환기")
        self.app.geometry("860x720")
        self.app.minsize(780, 620)

        # Apply Windows standard system fonts
        try:
            default_font = font.nametofont("TkDefaultFont")
            default_font.configure(family="Malgun Gothic", size=9)
            text_font = font.nametofont("TkTextFont")
            text_font.configure(family="Malgun Gothic", size=9)
            fixed_font = font.nametofont("TkFixedFont")
            fixed_font.configure(family="Consolas", size=9)
        except Exception:
            pass

        # Set Window Icon
        self.set_app_icon()

        # TTK Style configuration (Windows native 'vista' theme)
        self.style = ttk.Style()
        try:
            if "vista" in self.style.theme_names():
                self.style.theme_use("vista")
            elif "winnative" in self.style.theme_names():
                self.style.theme_use("winnative")
        except Exception:
            pass

        self.style.configure('TLabelframe', padding=8)
        self.style.configure('TLabelframe.Label', font=("Malgun Gothic", 9, "bold"))
        self.style.configure('Accent.TButton', font=("Malgun Gothic", 9, "bold"))

        # Queue and processing state
        self.iso_queue = queue.Queue()
        self.is_processing = False
        self.watcher = None
        self.handler = None
        self.ftp = FTP()

        # Load config
        self.config = self.load_config()

        # Find binaries
        self.iso2god_binaries = self.find_iso2god_binaries()
        self.selected_iso2god = tk.StringVar()
        saved_binary = self.config.get("iso2god_binary", "")
        # Filter windows binaries
        win_binaries = [b for b in self.iso2god_binaries if b.lower().startswith("windows") or b.lower().endswith(".exe")]

        if saved_binary and saved_binary in self.iso2god_binaries:
            if sys.platform == "win32" and not (saved_binary.lower().startswith("windows") or saved_binary.lower().endswith(".exe")) and win_binaries:
                self.selected_iso2god.set(win_binaries[-1])
            else:
                self.selected_iso2god.set(saved_binary)
        elif win_binaries and sys.platform == "win32":
            self.selected_iso2god.set(win_binaries[-1])
        elif self.iso2god_binaries:
            self.selected_iso2god.set(self.iso2god_binaries[0])
        else:
            self.selected_iso2god.set("")

        # Create GUI widgets
        self.create_widgets()

        # Start background processor thread
        self.process_thread = threading.Thread(target=self.process_queue, daemon=True)
        self.process_thread.start()

        # Periodic responsiveness check
        self.check_gui_responsive()

        # Initial binary check warning
        if not self.iso2god_binaries:
            self.update_status("경고: iso2god 실행 바이너리를 찾을 수 없습니다! (iso2god 폴더 확인 필요)", "error")
        else:
            self.update_status(f"프로그램 준비 완료. 감지된 엔진 바이너리 수: {len(self.iso2god_binaries)}개")

    def set_app_icon(self):
        # Look in bundle dir first, then app dir
        icon_candidates = [
            os.path.join(BUNDLE_DIR, "icon.ico"),
            os.path.join(APP_DIR, "icon.ico"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
        ]
        for icon_path in icon_candidates:
            if os.path.exists(icon_path):
                try:
                    self.app.iconbitmap(icon_path)
                    break
                except Exception:
                    pass

    def check_gui_responsive(self):
        self.app.after(100, self.check_gui_responsive)

    def find_iso2god_binaries(self):
        """Scan both external iso2god directory and bundled directory for executables."""
        binaries = set()
        search_dirs = [
            os.path.join(APP_DIR, "iso2god"),
            os.path.join(BUNDLE_DIR, "iso2god"),
            APP_DIR,
            BUNDLE_DIR
        ]

        pattern = re.compile(r'^(windows|mac|linux)-[\d.]+(\.[a-zA-Z0-9]+)?$', re.IGNORECASE)
        for s_dir in search_dirs:
            if os.path.exists(s_dir):
                for fname in os.listdir(s_dir):
                    fpath = os.path.join(s_dir, fname)
                    if os.path.isfile(fpath):
                        if pattern.match(fname) or fname.lower() in ("iso2god.exe", "iso2god"):
                            binaries.add(fname)
        return sorted(list(binaries))

    def resolve_binary_path(self, binary_name):
        """Find the full path of the given binary, checking user directory first, then bundle."""
        candidates = [
            os.path.join(APP_DIR, "iso2god", binary_name),
            os.path.join(BUNDLE_DIR, "iso2god", binary_name),
            os.path.join(APP_DIR, binary_name),
            os.path.join(BUNDLE_DIR, binary_name)
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        return None

    def load_config(self):
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    cfg = DEFAULT_CONFIG.copy()
                    cfg.update(loaded)
                    return cfg
        except Exception as e:
            print(f"Error loading config: {e}")
        return DEFAULT_CONFIG.copy()

    def save_config(self):
        config = {
            "watch_dir": self.watch_path.get(),
            "output_dir": self.output_path.get(),
            "trim_unused": self.trim_var.get(),
            "thread_count": self.thread_count.get(),
            "scan_delay": self.scan_delay.get(),
            "delete_iso": self.delete_iso_var.get(),
            "process_timeout": self.process_timeout.get(),
            "iso2god_binary": self.selected_iso2god.get(),
            "use_ftp": self.use_ftp.get(),
            "ip_addr": self.ftp_ip.get(),
            "ftp_port": self.ftp_port.get(),
            "ftp_user": self.ftp_user.get(),
            "ftp_pass": self.ftp_pass.get(),
            "drv_name": self.drv_field.get()
        }
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            self.update_status(f"설정 저장 실패: {e}", "error")

    def create_widgets(self):
        # Top-level container with margins
        main = ttk.Frame(self.app, padding=(12, 10, 12, 10))
        main.pack(fill="both", expand=True)

        # ----------------------------------------------------
        # 1. Directory Settings Group (폴더 경로 설정)
        # ----------------------------------------------------
        dir_group = ttk.LabelFrame(main, text=" 📁 폴더 경로 설정 ")
        dir_group.pack(fill="x", pady=(0, 8))
        dir_group.columnconfigure(1, weight=1)

        # Watch Directory
        ttk.Label(dir_group, text="감시 폴더 (ISO 위치):").grid(row=0, column=0, sticky="w", padx=5, pady=4)
        self.watch_path = ttk.Entry(dir_group)
        self.watch_path.grid(row=0, column=1, sticky="ew", padx=5, pady=4)
        self.watch_path.insert(0, self.config.get("watch_dir", ""))
        self.watch_path.bind("<FocusOut>", lambda e: self.save_config())
        ttk.Button(dir_group, text="찾아보기...", command=self.browse_watch_dir, width=12).grid(row=0, column=2, padx=4, pady=4)

        # Output Directory
        ttk.Label(dir_group, text="출력 폴더 (GOD 저장):").grid(row=1, column=0, sticky="w", padx=5, pady=4)
        self.output_path = ttk.Entry(dir_group)
        self.output_path.grid(row=1, column=1, sticky="ew", padx=5, pady=4)
        self.output_path.insert(0, self.config.get("output_dir", ""))
        self.output_path.bind("<FocusOut>", lambda e: self.save_config())

        out_btn_frame = ttk.Frame(dir_group)
        out_btn_frame.grid(row=1, column=2, sticky="ew", padx=4, pady=4)
        ttk.Button(out_btn_frame, text="찾아보기...", command=self.browse_output_dir, width=12).pack(side="left")
        ttk.Button(out_btn_frame, text="폴더 열기", command=self.open_output_dir, width=10).pack(side="left", padx=(4, 0))

        # ----------------------------------------------------
        # 2. Conversion Options Group (변환 엔진 및 성능 옵션)
        # ----------------------------------------------------
        opt_group = ttk.LabelFrame(main, text=" ⚙️ 변환 옵션 및 엔진 설정 ")
        opt_group.pack(fill="x", pady=(0, 8))

        # Row 1: Engine & Threads
        row1 = ttk.Frame(opt_group)
        row1.pack(fill="x", pady=3)

        ttk.Label(row1, text="변환 엔진 버전:").pack(side="left", padx=(5, 3))
        self.iso2god_dropdown = ttk.Combobox(
            row1, textvariable=self.selected_iso2god, values=self.iso2god_binaries, state="readonly", width=26
        )
        self.iso2god_dropdown.pack(side="left", padx=(0, 15))
        self.iso2god_dropdown.bind("<<ComboboxSelected>>", lambda e: self.save_config())

        ttk.Label(row1, text="작업 스레드 수:").pack(side="left", padx=(0, 3))
        self.thread_count = ttk.Entry(row1, width=5)
        self.thread_count.insert(0, str(self.config.get("thread_count", "4")))
        self.thread_count.pack(side="left", padx=(0, 15))
        self.thread_count.bind("<FocusOut>", lambda e: self.save_config())

        ttk.Label(row1, text="스캔 지연 (초):").pack(side="left", padx=(0, 3))
        self.scan_delay = ttk.Entry(row1, width=5)
        self.scan_delay.insert(0, str(self.config.get("scan_delay", "2")))
        self.scan_delay.pack(side="left", padx=(0, 15))
        self.scan_delay.bind("<FocusOut>", lambda e: self.save_config())

        ttk.Label(row1, text="타임아웃 (분, 0=무제한):").pack(side="left", padx=(0, 3))
        self.process_timeout = ttk.Entry(row1, width=5)
        self.process_timeout.insert(0, str(self.config.get("process_timeout", "0")))
        self.process_timeout.pack(side="left")
        self.process_timeout.bind("<FocusOut>", lambda e: self.save_config())

        # Row 2: Checkboxes
        row2 = ttk.Frame(opt_group)
        row2.pack(fill="x", pady=(4, 2))

        self.trim_var = tk.BooleanVar(value=self.config.get("trim_unused", False))
        ttk.Checkbutton(row2, text="빈 공간 제거 (Trim unused space)", variable=self.trim_var, command=self.save_config).pack(side="left", padx=(5, 20))

        self.delete_iso_var = tk.BooleanVar(value=self.config.get("delete_iso", True))
        ttk.Checkbutton(row2, text="변환 완료 후 원본 ISO 삭제", variable=self.delete_iso_var, command=self.save_config).pack(side="left", padx=(0, 20))

        # ----------------------------------------------------
        # 3. FTP Transfer Group (FTP 자동 전송 설정)
        # ----------------------------------------------------
        ftp_group = ttk.LabelFrame(main, text=" 📡 FTP 콘솔 자동 전송 ")
        ftp_group.pack(fill="x", pady=(0, 8))

        ftp_head = ttk.Frame(ftp_group)
        ftp_head.pack(fill="x", pady=(0, 4))
        self.use_ftp = tk.BooleanVar(value=self.config.get("use_ftp", False))
        ttk.Checkbutton(
            ftp_head, 
            text="변환 완료 시 Xbox 콘솔로 자동 FTP 전송 활성화", 
            variable=self.use_ftp, 
            command=self.toggle_ftp_fields
        ).pack(side="left", padx=5)

        self.ftp_inputs_frame = ttk.Frame(ftp_group)
        self.ftp_inputs_frame.pack(fill="x", padx=5, pady=2)

        ttk.Label(self.ftp_inputs_frame, text="IP 주소:").grid(row=0, column=0, sticky="w", padx=3, pady=2)
        self.ftp_ip = ttk.Entry(self.ftp_inputs_frame, width=15)
        self.ftp_ip.grid(row=0, column=1, sticky="w", padx=3, pady=2)
        self.ftp_ip.insert(0, self.config.get("ip_addr", ""))
        self.ftp_ip.bind("<FocusOut>", lambda e: self.save_config())

        ttk.Label(self.ftp_inputs_frame, text="포트:").grid(row=0, column=2, sticky="w", padx=(10, 3), pady=2)
        self.ftp_port = ttk.Entry(self.ftp_inputs_frame, width=6)
        self.ftp_port.grid(row=0, column=3, sticky="w", padx=3, pady=2)
        self.ftp_port.insert(0, str(self.config.get("ftp_port", "21") or "21"))
        self.ftp_port.bind("<FocusOut>", lambda e: self.save_config())

        ttk.Label(self.ftp_inputs_frame, text="사용자명:").grid(row=0, column=4, sticky="w", padx=(10, 3), pady=2)
        self.ftp_user = ttk.Entry(self.ftp_inputs_frame, width=12)
        self.ftp_user.grid(row=0, column=5, sticky="w", padx=3, pady=2)
        self.ftp_user.insert(0, self.config.get("ftp_user", "xbox"))
        self.ftp_user.bind("<FocusOut>", lambda e: self.save_config())

        ttk.Label(self.ftp_inputs_frame, text="비밀번호:").grid(row=0, column=6, sticky="w", padx=(10, 3), pady=2)
        self.ftp_pass = ttk.Entry(self.ftp_inputs_frame, width=12, show="*")
        self.ftp_pass.grid(row=0, column=7, sticky="w", padx=3, pady=2)
        self.ftp_pass.insert(0, self.config.get("ftp_pass", "xbox"))
        self.ftp_pass.bind("<FocusOut>", lambda e: self.save_config())

        ttk.Label(self.ftp_inputs_frame, text="드라이브명:").grid(row=0, column=8, sticky="w", padx=(10, 3), pady=2)
        self.drv_field = ttk.Entry(self.ftp_inputs_frame, width=8)
        self.drv_field.grid(row=0, column=9, sticky="w", padx=3, pady=2)
        self.drv_field.insert(0, self.config.get("drv_name", "Hdd1") or "Hdd1")
        self.drv_field.bind("<FocusOut>", lambda e: self.save_config())

        self.toggle_ftp_fields()

        # ----------------------------------------------------
        # 4. Status & Control Group (진행 상태 및 제어)
        # ----------------------------------------------------
        status_box = ttk.LabelFrame(main, text=" 🎮 작업 진행 및 상태 ")
        status_box.pack(fill="x", pady=(0, 8))

        # Current game row
        game_row = ttk.Frame(status_box)
        game_row.pack(fill="x", pady=(2, 6))
        ttk.Label(game_row, text="현재 변환 게임:", font=("Malgun Gothic", 9, "bold")).pack(side="left", padx=(5, 5))
        self.game_title_var = tk.StringVar(value="없음 (대기 중)")
        self.game_title_label = ttk.Label(game_row, textvariable=self.game_title_var, foreground="#005fb8", font=("Malgun Gothic", 10, "bold"))
        self.game_title_label.pack(side="left", fill="x", expand=True)

        # Progress bar
        self.progress_bar = ttk.Progressbar(status_box, mode="indeterminate")
        self.progress_bar.pack(fill="x", padx=5, pady=(0, 6))

        # Button Controls & Status text label
        btn_bar = ttk.Frame(status_box)
        btn_bar.pack(fill="x", pady=(2, 2))

        self.start_btn = ttk.Button(btn_bar, text="▶ 변환 및 감시 시작", command=self.toggle_watching, width=18, style="Accent.TButton")
        self.start_btn.pack(side="left", padx=(5, 6))

        self.clear_btn = ttk.Button(btn_bar, text="대기열 비우기", command=self.clear_queue, width=14)
        self.clear_btn.pack(side="left", padx=4)

        self.status_label = ttk.Label(btn_bar, text="상태: 대기 중", font=("Malgun Gothic", 9))
        self.status_label.pack(side="left", padx=(15, 5), fill="x", expand=True)

        # ----------------------------------------------------
        # 5. Log Console (작업 로그)
        # ----------------------------------------------------
        log_group = ttk.LabelFrame(main, text=" 📜 실시간 로그 ")
        log_group.pack(fill="both", expand=True)

        self.status_text = scrolledtext.ScrolledText(
            log_group, 
            height=12, 
            font=("Consolas", 9), 
            wrap="word", 
            background="#ffffff", 
            foreground="#1e1e1e"
        )
        self.status_text.pack(fill="both", expand=True, padx=4, pady=4)
        self.status_text.tag_configure("found", foreground="#0052cc", font=("Consolas", 9, "bold"))
        self.status_text.tag_configure("success", foreground="#008000", font=("Consolas", 9, "bold"))
        self.status_text.tag_configure("error", foreground="#d90000", font=("Consolas", 9, "bold"))
        self.status_text.tag_configure("watching", foreground="#006699")
        self.status_text.configure(state="disabled")

    def toggle_ftp_fields(self):
        state = "normal" if self.use_ftp.get() else "disabled"
        for child in self.ftp_inputs_frame.winfo_children():
            if isinstance(child, ttk.Entry):
                child.configure(state=state)
        self.save_config()

    def browse_watch_dir(self):
        directory = filedialog.askdirectory(title="감시할 ISO 폴더 선택", initialdir=self.watch_path.get() or APP_DIR)
        if directory:
            self.watch_path.delete(0, "end")
            self.watch_path.insert(0, directory)
            self.save_config()

    def browse_output_dir(self):
        directory = filedialog.askdirectory(title="GOD 파일 저장 폴더 선택", initialdir=self.output_path.get() or APP_DIR)
        if directory:
            self.output_path.delete(0, "end")
            self.output_path.insert(0, directory)
            self.save_config()

    def open_output_dir(self):
        out_dir = self.output_path.get()
        if out_dir and os.path.exists(out_dir):
            try:
                os.startfile(out_dir)
            except Exception as e:
                messagebox.showerror("오류", f"폴더를 열 수 없습니다: {e}")
        else:
            messagebox.showwarning("안내", "유효한 출력 폴더를 먼저 선택해 주세요.")

    def update_status(self, message, status_type=None, current_index=None, total_count=None):
        self.status_text.configure(state="normal")
        timestamp = time.strftime("%H:%M:%S")
        queue_info = ""
        if current_index is not None and total_count is not None:
            queue_info = f" ({total_count}개 중 {current_index}번째 처리 중)"

        if status_type == "found":
            self.status_label.configure(text=f"상태: ISO 발견 - {os.path.basename(message)}{queue_info}")
        elif status_type == "success":
            self.status_label.configure(text=f"상태: 변환 완료{queue_info}")
        elif status_type == "error":
            self.status_label.configure(text=f"상태: 오류 발생{queue_info}")
        elif status_type == "watching":
            self.status_label.configure(text=f"상태: 폴더 감시 중 - {message}{queue_info}")
        else:
            self.status_label.configure(text=f"상태: {message}{queue_info}")

        prefix = f"[{timestamp}] "
        if status_type:
            self.status_text.insert("end", prefix)
            self.status_text.insert("end", f"{message}{queue_info}\n", status_type)
        else:
            self.status_text.insert("end", f"{prefix}{message}{queue_info}\n")

        self.status_text.see("end")
        self.status_text.configure(state="disabled")

    def toggle_watching(self):
        if not self.watcher:
            try:
                watch_dir = self.watch_path.get().strip()
                output_dir = self.output_path.get().strip()

                if not watch_dir or not output_dir:
                    messagebox.showerror("오류", "감시 폴더와 출력 폴더를 모두 지정해 주세요.")
                    return

                if not os.path.exists(watch_dir):
                    messagebox.showerror("오류", f"감시 폴더가 존재하지 않습니다:\n{watch_dir}")
                    return

                if not os.path.exists(output_dir):
                    try:
                        os.makedirs(output_dir, exist_ok=True)
                    except Exception as e:
                        messagebox.showerror("오류", f"출력 폴더 생성 실패:\n{e}")
                        return

                self.save_config()
                self.handler = IsoHandler(self.iso_queue)

                try:
                    delay = float(self.scan_delay.get())
                    self.handler.set_scan_delay(delay)
                except ValueError:
                    self.scan_delay.delete(0, "end")
                    self.scan_delay.insert(0, "2")
                    self.handler.set_scan_delay(2.0)

                self.watcher = DirectoryWatcher(watch_dir, self.handler)
                self.watcher.start()

                self.start_btn.configure(text="⏹ 감시 및 변환 중지")
                self.progress_bar.start(15)
                self.update_status(f"감시 시작: {watch_dir}", "watching")
                self.is_processing = True

            except Exception as e:
                self.update_status(f"감시 시작 오류: {str(e)}", "error")
                if self.watcher:
                    try:
                        self.watcher.stop()
                    except Exception:
                        pass
                self.watcher = None
                self.progress_bar.stop()
                messagebox.showerror("오류", f"변환 작업을 시작하지 못했습니다: {str(e)}")
        else:
            self.stop_watching()

    def stop_watching(self):
        if self.watcher:
            try:
                self.watcher.stop()
                self.watcher = None
                self.start_btn.configure(text="▶ 변환 및 감시 시작")
                self.progress_bar.stop()
                self.update_status("폴더 감시가 중지되었습니다.")
                self.is_processing = False
            except Exception as e:
                self.update_status(f"감시 중지 오류: {str(e)}", "error")

    def clear_queue(self):
        cleared_count = 0
        while not self.iso_queue.empty():
            try:
                self.iso_queue.get_nowait()
                cleared_count += 1
            except queue.Empty:
                break
        self.update_status(f"대기열 비움 완료 ({cleared_count}개 삭제)")

    def process_queue(self):
        while True:
            if self.is_processing:
                try:
                    total_count = self.iso_queue.qsize()
                    if total_count == 0:
                        time.sleep(0.1)
                        continue
                    current_index = 1
                    iso_path = self.iso_queue.get(timeout=1)
                    self.process_iso(iso_path, current_index=current_index, total_count=total_count)
                except queue.Empty:
                    time.sleep(0.1)
            else:
                time.sleep(0.1)

    def process_iso(self, iso_path, current_index=None, total_count=None):
        max_retries = 3
        retry_delay = 10
        current_try = 0
        last_progress_time = 0
        progress_update_interval = 5

        def is_legacy_version(binary_name):
            m = re.search(r'-(\d+\.\d+\.\d+)', binary_name)
            if m:
                version = m.group(1)
                version_tuple = tuple(map(int, version.split('.')))
                return version_tuple <= (1, 6, 0)
            return False

        try:
            filename = os.path.basename(iso_path)
            game_title = os.path.splitext(filename)[0]
            self.game_title_var.set(game_title)
            self.update_status(f"새 ISO 감지: {filename}", "found", current_index=current_index, total_count=total_count)

            iso2god_binary = self.selected_iso2god.get()
            if not iso2god_binary:
                self.update_status("선택된 iso2god 바이너리가 없습니다!", "error")
                return

            iso2god_path = self.resolve_binary_path(iso2god_binary)
            if not iso2god_path or not os.path.exists(iso2god_path):
                self.update_status(f"iso2god 실행 파일을 찾을 수 없습니다: {iso2god_binary}", "error")
                return

            legacy_mode = is_legacy_version(iso2god_binary)

            while current_try < max_retries:
                try:
                    # Check file access
                    try:
                        with open(iso_path, 'rb') as test_file:
                            pass
                    except PermissionError:
                        if current_try < max_retries - 1:
                            self.update_status(f"파일이 잠겨 있습니다. {retry_delay}초 후 재시도... (시도 {current_try + 1}/{max_retries})", "error", current_index=current_index, total_count=total_count)
                            time.sleep(retry_delay)
                            current_try += 1
                            continue
                        else:
                            self.update_status(f"{filename} 건너뜀 - 파일 잠금 해제 실패", "error", current_index=current_index, total_count=total_count)
                            return

                    cmd = [iso2god_path, iso_path, self.output_path.get()]
                    if self.trim_var.get():
                        cmd.append("--trim")

                    thread_count = self.thread_count.get().strip()
                    add_j = thread_count.isdigit() and not legacy_mode
                    if add_j:
                        cmd.extend(["-j", thread_count])

                    try:
                        timeout_minutes = float(self.process_timeout.get())
                        timeout_seconds = timeout_minutes * 60 if timeout_minutes > 0 else None
                    except ValueError:
                        timeout_seconds = None

                    self.update_status(f"변환 시작: {filename} (엔진: {iso2god_binary})", current_index=current_index, total_count=total_count)

                    # Windows: Hide child console window if GUI frozen
                    startupinfo = None
                    if sys.platform == "win32":
                        startupinfo = subprocess.STARTUPINFO()
                        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        bufsize=1,
                        startupinfo=startupinfo
                    )

                    last_output = ""
                    conversion_start_time = time.time()
                    error_detected = {"unexpected_j": False}

                    def read_output(pipe, is_error=False):
                        nonlocal last_output
                        while True:
                            line = pipe.readline()
                            if not line:
                                break
                            line = line.strip()
                            if line:
                                if is_error and legacy_mode and "unexpected argument '-j' found" in line:
                                    error_detected["unexpected_j"] = True
                                if "writing part files:" in line.lower() or "progress" in line.lower():
                                    self.status_label.configure(text=f"상태: {line}")
                                self.update_status(line, "error" if is_error else None)
                                if not is_error:
                                    last_output = line

                    stdout_thread = threading.Thread(target=read_output, args=(process.stdout,))
                    stderr_thread = threading.Thread(target=read_output, args=(process.stderr, True))
                    stdout_thread.daemon = True
                    stderr_thread.daemon = True
                    stdout_thread.start()
                    stderr_thread.start()

                    while process.poll() is None:
                        current_time = time.time()
                        if timeout_seconds and current_time - conversion_start_time > timeout_seconds:
                            process.terminate()
                            time.sleep(1)
                            if process.poll() is None:
                                process.kill()
                            self.update_status(f"{filename} 변환 중단 - {timeout_minutes}분 타임아웃 초과", "error", current_index=current_index, total_count=total_count)
                            return

                        if current_time - last_progress_time >= progress_update_interval:
                            elapsed_minutes = (current_time - conversion_start_time) / 60
                            self.status_label.configure(text=f"진행 중: {filename} ({int(elapsed_minutes)}분 경과)")
                            last_progress_time = current_time

                        time.sleep(0.1)

                    return_code = process.poll()
                    stdout_thread.join(1)
                    stderr_thread.join(1)

                    if legacy_mode and error_detected["unexpected_j"] and add_j:
                        self.update_status("레거시 엔진 감지: '-j' 옵션 없이 재시도합니다...", "error", current_index=current_index, total_count=total_count)
                        current_try += 1
                        continue

                    if return_code == 0:
                        elapsed_minutes = (time.time() - conversion_start_time) / 60
                        self.update_status(
                            f"변환 완료 성공: {filename} (총 소요 시간: {int(elapsed_minutes)}분 {int((elapsed_minutes % 1)*60)}초)", 
                            "success", current_index=current_index, total_count=total_count
                        )
                        if self.delete_iso_var.get() and self.is_processing:
                            try:
                                os.remove(iso_path)
                                self.update_status(f"원본 ISO 파일 삭제 완료: {filename}", "success")
                            except Exception as e:
                                self.update_status(f"원본 ISO 삭제 실패: {e}", "error")
                        return
                    else:
                        error_msg = f"{filename} 변환 실패 (코드: {return_code})"
                        if current_try < max_retries - 1:
                            self.update_status(f"{error_msg}. {retry_delay}초 후 재시도... (시도 {current_try + 1}/{max_retries})", "error", current_index=current_index, total_count=total_count)
                            time.sleep(retry_delay)
                            current_try += 1
                        else:
                            self.update_status(f"{filename} 변환 최종 실패", "error", current_index=current_index, total_count=total_count)
                            return
                except PermissionError as e:
                    if current_try < max_retries - 1:
                        self.update_status(f"파일 접근 오류: {e}. {retry_delay}초 후 재시도...", "error")
                        time.sleep(retry_delay)
                        current_try += 1
                    else:
                        self.update_status(f"{filename} 파일 접근 불가로 건너뜁니다: {e}", "error")
                        return
                except Exception as e:
                    self.update_status(f"예상치 못한 오류: {e}", "error")
                    return
        finally:
            self.game_title_var.set("없음 (대기 중)")
            if iso_path in self.handler.processing:
                self.handler.processing.remove(iso_path)
            self.iso_queue.task_done()

            # FTP Transfer if enabled
            if self.use_ftp.get():
                try:
                    self.update_status("Xbox 콘솔로 FTP 전송을 시작합니다...")
                    self.send_over_ftp()
                except Exception as e:
                    self.update_status(f"FTP 전송 실패: {e}", "error")

            self.update_status("다음 작업 대기 중", current_index=current_index, total_count=total_count)

    def upload_file_with_progress(self, local_path, remote_name):
        total_size = os.path.getsize(local_path)
        uploaded = 0
        last_percent = 0

        def callback(data):
            nonlocal uploaded, last_percent
            uploaded += len(data)
            percent = int((uploaded / total_size) * 100) if total_size > 0 else 100
            if percent >= last_percent + 10:
                last_percent = (percent // 10) * 10
                self.status_label.configure(text=f"FTP 업로드 중: {remote_name} ({percent}%)")

        with open(local_path, "rb") as f:
            self.ftp.storbinary(f"STOR {remote_name}", f, 1024, callback=callback)

    def upload_folder(self, local_dir, remote_dir):
        try:
            self.ftp.mkd(remote_dir)
        except Exception:
            pass
        self.ftp.cwd(remote_dir)

        for item in os.listdir(local_dir):
            local_path = os.path.join(local_dir, item)
            if os.path.isdir(local_path):
                self.upload_folder(local_path, item)
                self.ftp.cwd("..")
            else:
                self.upload_file_with_progress(local_path, item)

    def send_over_ftp(self):
        ip = self.ftp_ip.get().strip()
        port = int(self.ftp_port.get().strip() or "21")
        user = self.ftp_user.get().strip()
        pwd = self.ftp_pass.get().strip()
        drv = self.drv_field.get().strip() or "Hdd1"
        out_folder = self.output_path.get().strip()

        if not ip:
            self.update_status("FTP 전송 실패: IP 주소가 입력되지 않았습니다.", "error")
            return

        self.ftp.connect(ip, port, timeout=15)
        self.ftp.login(user, pwd)
        remote_folder = f"{drv}/Content/0000000000000000"
        self.upload_folder(out_folder, remote_folder)
        self.ftp.quit()
        self.update_status("FTP 콘솔 전송 완료!", "success")

    def run(self):
        self.app.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.app.mainloop()

    def on_closing(self):
        try:
            self.save_config()
            if self.watcher:
                self.stop_watching()
            self.app.quit()
        except Exception as e:
            print(f"Error during shutdown: {e}")
        finally:
            self.app.destroy()

if __name__ == "__main__":
    app = Iso2GodGUI()
    app.run()
