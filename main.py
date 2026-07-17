import os
import sys
import time
import json
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# ==========================================
# 0. CONFIGURATION
# ==========================================

APP_NAME = "SyncFlow Automator"
APP_VERSION = "1.4.0"

# Translation QC engine (optional: app still works without it)
try:
    import translation_qc as tqc
except Exception:
    tqc = None

# Format subfolders created inside every scene folder.
FORMAT_FOLDERS = ["MP4", "Quicktime", "SyncSO", "Lipdub", "Audio"]

# Naming suffix appended per format (matched case-insensitively against the format
# folder name). The file's REAL extension is always preserved on rename — renaming
# never converts media, so we must not fake a codec by turning ".wav" into ".mp3".
FORMAT_SUFFIXES = {
    "mp4": "",
    "quicktime": "",
    "audio": "_Audio",
    "syncso": "_Sync_so",
    "lipdub": "_Lipdub",
}

# Structural folder names skipped when walking upward to infer Reel / Show names.
SKIP_NAMES = {"exports", "media", "projects"}

# Persisted user settings (last-used path / project / reel / etc.).
SETTINGS_FILE = Path.home() / ".syncflow_automator.json"

# ==========================================
# 1. PLATFORM-SPECIFIC SELECTED FILE DETECTION
# ==========================================

def get_clipboard_text_win():
    """Reads standard text from Windows clipboard using ctypes."""
    import ctypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    CF_UNICODETEXT = 13
    text = ""
    if user32.OpenClipboard(None):
        try:
            h_mem = user32.GetClipboardData(CF_UNICODETEXT)
            if h_mem:
                p_text = kernel32.GlobalLock(h_mem)
                if p_text:
                    try:
                        text = ctypes.wstring_at(p_text)
                    finally:
                        kernel32.GlobalUnlock(h_mem)
        finally:
            user32.CloseClipboard()
    return text

def set_clipboard_text_win(text):
    """Writes standard text to Windows clipboard using ctypes."""
    import ctypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    CF_UNICODETEXT = 13
    if user32.OpenClipboard(None):
        try:
            user32.EmptyClipboard()
            text_bytes = (text + "\0").encode('utf-16le')
            h_mem = kernel32.GlobalAlloc(0x0002, len(text_bytes))  # GHND
            if h_mem:
                p_mem = kernel32.GlobalLock(h_mem)
                if p_mem:
                    try:
                        ctypes.memmove(p_mem, text_bytes, len(text_bytes))
                    finally:
                        kernel32.GlobalUnlock(h_mem)
                    user32.SetClipboardData(CF_UNICODETEXT, h_mem)
        finally:
            user32.CloseClipboard()

def _read_hdrop_win():
    """Reads file paths from the CF_HDROP clipboard slot (files copied in Explorer)."""
    import ctypes
    user32 = ctypes.windll.user32
    CF_HDROP = 15
    paths = []
    if user32.OpenClipboard(None):
        try:
            h_mem = user32.GetClipboardData(CF_HDROP)
            if h_mem:
                kernel32 = ctypes.windll.kernel32
                p_drop = kernel32.GlobalLock(h_mem)
                if p_drop:
                    try:
                        shell32 = ctypes.windll.shell32
                        num_files = shell32.DragQueryFileW(p_drop, 0xFFFFFFFF, None, 0)
                        for i in range(num_files):
                            length = shell32.DragQueryFileW(p_drop, i, None, 0)
                            buffer = ctypes.create_unicode_buffer(length + 1)
                            shell32.DragQueryFileW(p_drop, i, buffer, length + 1)
                            paths.append(buffer.value)
                    finally:
                        kernel32.GlobalUnlock(h_mem)
        finally:
            user32.CloseClipboard()
    return paths

def get_selected_file_paths():
    """
    Retrieves the currently selected file path from Windows Explorer or Mac Finder.

    Windows: first reads any file already on the clipboard (the reliable path — the
    user can select a file and press Ctrl+C). If none is present, it falls back to
    synthesising Ctrl+C. The user's original clipboard text is preserved and restored.
    """
    if sys.platform == "win32":
        import ctypes

        # 1) Fast path: a file may already be on the clipboard (user pressed Ctrl+C).
        paths = _read_hdrop_win()
        if paths:
            return paths

        # 2) Fallback: back up clipboard text, synthesise Ctrl+C, then re-read.
        orig_text = ""
        try:
            orig_text = get_clipboard_text_win()
        except Exception:
            pass

        user32 = ctypes.windll.user32
        user32.keybd_event(0x11, 0, 0, 0)      # Ctrl Down
        user32.keybd_event(0x43, 0, 0, 0)      # C Down
        user32.keybd_event(0x43, 0, 2, 0)      # C Up
        user32.keybd_event(0x11, 0, 2, 0)      # Ctrl Up

        time.sleep(0.15)  # Wait for the clipboard to update safely

        paths = _read_hdrop_win()

        # Restore original clipboard text
        if orig_text:
            try:
                set_clipboard_text_win(orig_text)
            except Exception:
                pass

        return paths

    elif sys.platform == "darwin":
        # AppleScript to fetch selected item directly from macOS Finder
        ascript = (
            "tell application \"Finder\"\n"
            "    set theSelection to selection\n"
            "    if theSelection is {} then return \"\"\n"
            "    set theItem to item 1 of theSelection\n"
            "    return POSIX path of (theItem as text)\n"
            "end tell"
        )
        try:
            import subprocess
            proc = subprocess.run(['osascript', '-e', ascript], capture_output=True, text=True, timeout=2)
            path_str = proc.stdout.strip()
            if path_str:
                return [path_str]
        except Exception:
            pass

    return []

def open_in_file_manager(path):
    """Opens the specified directory path directly in the native OS window."""
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        import subprocess
        subprocess.run(["open", path])
    else:
        import subprocess
        subprocess.run(["xdg-open", path])

# ==========================================
# 2. CONTEXT-AWARE RENAMING ENGINE
# ==========================================

def parse_context_from_path(file_path_str):
    """
    Parses Movie, Reel, Character, Scene, and Format out of a path string
    anchored to 'Character Exports' case-insensitively.
    """
    path = Path(file_path_str)
    parts = list(path.parts)

    # Locate anchor index case-insensitively
    anchor_idx = -1
    for i, part in enumerate(parts):
        if "character exports" in part.lower():
            anchor_idx = i
            break

    if anchor_idx == -1 or len(parts) <= anchor_idx + 2:
        return None  # Critical structure anchor missing

    try:
        character = parts[anchor_idx + 1]
        scene = parts[anchor_idx + 2]
        format_folder = parts[anchor_idx + 3] if len(parts) > anchor_idx + 3 else ""

        # Step backward to intelligently determine Reel and Movie names, skipping intermediate folders
        temp_idx = anchor_idx - 1
        reel = "Reel_01"
        movie = "Show_Name"

        # Find Reel folder
        while temp_idx >= 0 and parts[temp_idx].lower() in SKIP_NAMES:
            temp_idx -= 1
        if temp_idx >= 0:
            reel = parts[temp_idx]
            temp_idx -= 1

        # Find Movie folder
        while temp_idx >= 0 and parts[temp_idx].lower() in SKIP_NAMES:
            temp_idx -= 1
        if temp_idx >= 0:
            movie = parts[temp_idx]

        return {
            "original_path": path,
            "movie": movie.replace(" ", "_"),
            "reel": reel.replace(" ", "_"),
            "character": character,
            "scene": scene,
            "format_folder": format_folder,
            "extension": path.suffix
        }
    except Exception:
        return None

def build_new_name(ctx, shot_num):
    """
    Builds the standardised export filename from a parsed context and a shot number.
    Pure/self-contained so it can be unit-tested without a GUI.
    """
    fmt = ctx['format_folder'].lower()

    # Data-driven suffix lookup (see FORMAT_SUFFIXES).
    suffix = ""
    for key, suf in FORMAT_SUFFIXES.items():
        if key in fmt:
            suffix = suf
            break

    # Preserve the file's REAL extension. Renaming does not transcode media, so
    # forcing an extension (e.g. .wav -> .mp3) would mislabel the file on disk.
    ext = ctx['extension'] or ""

    # Standardise zero padding for numeric shot numbers.
    shot_str = f"Shot_{int(shot_num):02d}" if shot_num.isdigit() else f"Shot_{shot_num}"

    return f"{ctx['movie']}_{ctx['reel']}_{ctx['scene']}_{ctx['character']}_{shot_str}{suffix}{ext}"

class RenameOverlay:
    """Minimalist borderless always-on-top modal for Shot number entry."""
    def __init__(self, root, context, app=None):
        self.root = root
        self.context = context
        self.app = app

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg="#1e1e24")

        # Center Screen Alignment Calculation
        self.win.update_idletasks()
        w, h = 500, 140
        sx = (self.win.winfo_screenwidth() // 2) - (w // 2)
        sy = (self.win.winfo_screenheight() // 2) - (h // 2)
        self.win.geometry(f"{w}x{h}+{sx}+{sy}")

        # Background canvas frame to draw border
        bg_frame = tk.Frame(self.win, bg="#1e1e24", highlightbackground="#007acc", highlightthickness=1)
        bg_frame.pack(fill=tk.BOTH, expand=True)

        # Content Widgets
        lbl_info = tk.Label(
            bg_frame,
            text=f"Detected: {context['movie']} | {context['reel']} | {context['scene']} | {context['character']} | {context['format_folder']}",
            fg="#a4a4a4", bg="#1e1e24", font=("Segoe UI", 9, "bold")
        )
        lbl_info.pack(pady=(15, 5), padx=15)

        lbl_prompt = tk.Label(bg_frame, text="Enter Shot Number:", fg="#ffffff", bg="#1e1e24", font=("Segoe UI", 11))
        lbl_prompt.pack()

        self.entry = tk.Entry(
            bg_frame, bg="#2d2d34", fg="#ffffff", insertbackground="white",
            font=("Segoe UI", 12), justify="center", bd=0, highlightthickness=1,
            highlightbackground="#44444c"
        )
        self.entry.pack(pady=5, ipady=3)
        self.entry.focus_set()

        # Allow pasting (tk.Entry supports Ctrl+V natively on Windows/Linux; add Command+V on Mac if needed)
        self.entry.bind("<Return>", self.execute_rename)
        self.win.bind("<Escape>", lambda e: self.win.destroy())

        self.win.deiconify()

    def execute_rename(self, event=None):
        shot_num = self.entry.get().strip()
        if not shot_num:
            return

        ctx = self.context
        new_name = build_new_name(ctx, shot_num)
        target_path = ctx['original_path'].parent / new_name

        # No-op guard: nothing to do if the name is unchanged.
        if target_path == ctx['original_path']:
            self.win.destroy()
            return

        try:
            if target_path.exists():
                if not messagebox.askyesno("File Conflict", f"File '{new_name}' already exists.\nDo you want to overwrite it?"):
                    return
            os.rename(ctx['original_path'], target_path)
            if self.app is not None:
                self.app.register_rename(target_path, ctx['original_path'])
        except Exception as e:
            messagebox.showerror("Error", f"Rename failed:\n{e}")
        finally:
            self.win.destroy()

# ==========================================
# 3. MAIN APP INTERFACE
# ==========================================

class SyncFlowApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_NAME}  v{APP_VERSION}")
        # Responsive: user-resizable, sane floor; last size/position is restored
        # from settings (see load_settings) and saved on close.
        self.root.geometry("760x680")
        self.root.minsize(600, 520)
        self.root.configure(bg="#121214")
        self._init_styles()

        # State
        self._folder_cache = []    # cached folder paths for instant search
        self._search_job = None    # debounce handle
        self.last_rename = None     # (new_path, old_path) for one-step undo
        self.qc_running = False
        self.qc_last_res = None     # full run_qc result for the in-app viewer

        self.build_tabbed_ui()
        self.load_settings()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        if self.ent_path.get().strip():
            self._rebuild_cache()

    def _init_styles(self):
        """ttk styling for the dark theme (Treeview result grid needs 'clam' -
        the native Windows theme ignores row background tags)."""
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        # Known ttk bug (8.6.9): style maps override tag colors unless the
        # default '!disabled !selected' entries are stripped.
        def fixed_map(option):
            return [e for e in style.map("Treeview", query_opt=option)
                    if e[:2] != ("!disabled", "!selected")]
        style.map("Treeview", foreground=fixed_map("foreground"),
                  background=fixed_map("background"))
        style.configure("QC.Treeview", background="#1e1e24", fieldbackground="#1e1e24",
                        foreground="#e6e6e6", rowheight=24, borderwidth=0, font=("Segoe UI", 9))
        style.configure("QC.Treeview.Heading", background="#2d2d34", foreground="#ffffff",
                        relief="flat", font=("Segoe UI", 9, "bold"))
        style.map("QC.Treeview.Heading", background=[("active", "#3e3e46")])
        style.map("QC.Treeview", background=[("selected", "#007acc")],
                  foreground=[("selected", "#ffffff")])
        style.configure("TCombobox", fieldbackground="#1e1e24", background="#2d2d34",
                        foreground="#ffffff", arrowcolor="#ffffff")

    # ---------- Tab switcher (Folders view = original UI, untouched) ----------

    def build_tabbed_ui(self):
        tab_bar = tk.Frame(self.root, bg="#121214")
        tab_bar.pack(fill=tk.X, padx=20, pady=(14, 0))

        self.btn_tab_folders = tk.Button(
            tab_bar, text="  Folders  ", relief="flat", bd=0, font=("Segoe UI", 10, "bold"),
            command=lambda: self.show_tab("folders"))
        self.btn_tab_folders.pack(side=tk.LEFT)

        self.btn_tab_qc = tk.Button(
            tab_bar, text="  Translation QC  ", relief="flat", bd=0, font=("Segoe UI", 10, "bold"),
            command=lambda: self.show_tab("qc"))
        self.btn_tab_qc.pack(side=tk.LEFT, padx=(6, 0))

        self.folders_frame = tk.Frame(self.root, bg="#121214")
        self.qc_frame = tk.Frame(self.root, bg="#121214")

        self.build_clean_ui()   # original folder UI builds into self.folders_frame
        self.build_qc_ui()
        self.show_tab("folders")

    def show_tab(self, which):
        active, inactive = ("#007acc", "white"), ("#1e1e24", "#8a8a92")
        if which == "folders":
            self.qc_frame.pack_forget()
            self.folders_frame.pack(fill=tk.BOTH, expand=True)
            self.btn_tab_folders.config(bg=active[0], fg=active[1], activebackground=active[0], activeforeground="white")
            self.btn_tab_qc.config(bg=inactive[0], fg=inactive[1], activebackground="#2d2d34", activeforeground="white")
        else:
            self.folders_frame.pack_forget()
            self.qc_frame.pack(fill=tk.BOTH, expand=True)
            self.btn_tab_qc.config(bg=active[0], fg=active[1], activebackground=active[0], activeforeground="white")
            self.btn_tab_folders.config(bg=inactive[0], fg=inactive[1], activebackground="#2d2d34", activeforeground="white")

    def build_clean_ui(self):
        main_frame = tk.Frame(self.folders_frame, bg="#121214", padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Root Path
        tk.Label(main_frame, text="Target Project Root Directory:", fg="#b0b0b8", bg="#121214").pack(anchor=tk.W, pady=(0, 5))
        p_frame = tk.Frame(main_frame, bg="#121214")
        p_frame.pack(fill=tk.X, pady=(0, 15))

        self.ent_path = tk.Entry(p_frame, bg="#1e1e24", fg="#ffffff", bd=1, relief="flat", insertbackground="white")
        self.ent_path.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)
        self.ent_path.bind("<FocusOut>", lambda e: self._rebuild_cache())

        tk.Button(
            p_frame, text="Browse", bg="#2d2d34", fg="white",
            relief="flat", activebackground="#3e3e46", activeforeground="white",
            command=self.browse_dir
        ).pack(side=tk.RIGHT, padx=(5, 0))

        # Grid fields
        g_frame = tk.Frame(main_frame, bg="#121214")
        g_frame.pack(fill=tk.X, pady=5)

        fields = [
            ("Project Name:", "movie"),
            ("Reel Number:", "reel"),
            ("Characters (comma sep):", "chars"),
            ("Number of Scenes:", "scenes")
        ]

        self.entries = {}
        for i, (label_text, key) in enumerate(fields):
            row = i // 2
            col = (i % 2) * 2
            tk.Label(g_frame, text=label_text, fg="#b0b0b8", bg="#121214").grid(row=row, column=col, sticky=tk.W, pady=5, padx=(5 if col > 0 else 0, 5))
            ent = tk.Entry(g_frame, bg="#1e1e24", fg="#ffffff", bd=1, relief="flat", insertbackground="white")
            ent.grid(row=row, column=col + 1, sticky=tk.EW, pady=5, ipady=4)
            g_frame.columnconfigure(col + 1, weight=1)
            self.entries[key] = ent

        # Action Buttons
        b_frame = tk.Frame(main_frame, bg="#121214")
        b_frame.pack(fill=tk.X, pady=20)

        tk.Button(
            b_frame, text="Mode A: [All Structure]", bg="#007acc", fg="white",
            relief="flat", activebackground="#0098ff", activeforeground="white",
            command=lambda: self.generate_structure("all")
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2, ipady=5)

        tk.Button(
            b_frame, text="Mode B: [Exports Only]", bg="#2d2d34", fg="white",
            relief="flat", activebackground="#3e3e46", activeforeground="white",
            command=lambda: self.generate_structure("exports")
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2, ipady=5)

        tk.Button(
            b_frame, text="Mode C: [Character Exports]", bg="#2d2d34", fg="white",
            relief="flat", activebackground="#3e3e46", activeforeground="white",
            command=lambda: self.generate_structure("characters")
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2, ipady=5)

        # Quick Renamer Activation Button (Zero background hooks)
        tk.Button(
            main_frame, text="⚡ Rename Selected Explorer File Now", bg="#28a745", fg="white",
            font=("Segoe UI", 10, "bold"), relief="flat", activebackground="#218838", activeforeground="white",
            command=self.manual_rename_trigger
        ).pack(fill=tk.X, pady=(10, 4), ipady=6)

        # One-step undo for the most recent rename
        self.btn_undo = tk.Button(
            main_frame, text="↩ Undo Last Rename", bg="#2d2d34", fg="#8a8a92",
            relief="flat", state=tk.DISABLED, activebackground="#3e3e46", activeforeground="white",
            command=self.undo_last_rename
        )
        self.btn_undo.pack(fill=tk.X, pady=(0, 6), ipady=3)

        # Quick Search
        tk.Label(main_frame, text="Quick Search Folders:", fg="#b0b0b8", bg="#121214").pack(anchor=tk.W, pady=(10, 5))
        self.ent_search = tk.Entry(main_frame, bg="#1e1e24", fg="#ffffff", bd=1, relief="flat", insertbackground="white")
        self.ent_search.pack(fill=tk.X, ipady=4, pady=(0, 5))
        self.ent_search.bind("<KeyRelease>", self._schedule_search)

        # Results frame and Listbox with Scrollbar
        list_frame = tk.Frame(main_frame, bg="#1e1e24")
        list_frame.pack(fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(list_frame, orient="vertical", width=12)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.listbox = tk.Listbox(
            list_frame, bg="#1e1e24", fg="#ffffff", bd=0, highlightthickness=0,
            selectbackground="#007acc", selectforeground="white", yscrollcommand=scrollbar.set
        )
        self.listbox.pack(fill=tk.BOTH, expand=True, padx=(8, 0), pady=6)
        scrollbar.config(command=self.listbox.yview)

        self.listbox.bind("<Double-Button-1>", self.open_search_path)
        self.listbox.bind("<Return>", self.open_search_path)

    def browse_dir(self):
        selected = filedialog.askdirectory()
        if selected:
            self.ent_path.delete(0, tk.END)
            self.ent_path.insert(0, selected)
            self._rebuild_cache()

    def manual_rename_trigger(self):
        paths = get_selected_file_paths()
        if not paths:
            messagebox.showinfo(
                "Info",
                "No file detected.\n\nIn Explorer/Finder, click the file to select it "
                "(on Windows, press Ctrl+C to copy it), then click this button again."
            )
            return
        context = parse_context_from_path(paths[0])
        if context:
            RenameOverlay(self.root, context, app=self)
        else:
            messagebox.showwarning("Structure Error", "The selected file is not inside a proper 'Character Exports' folder structure.")

    def register_rename(self, new_path, old_path):
        """Record the most recent rename so it can be undone in one click."""
        self.last_rename = (Path(new_path), Path(old_path))
        self.btn_undo.config(state=tk.NORMAL, fg="#ffffff")
        self._rebuild_cache()

    def undo_last_rename(self):
        if not self.last_rename:
            return
        new_path, old_path = self.last_rename
        try:
            if old_path.exists():
                messagebox.showwarning("Undo blocked", f"Cannot undo: '{old_path.name}' exists again.")
                return
            os.rename(new_path, old_path)
            self.last_rename = None
            self.btn_undo.config(state=tk.DISABLED, fg="#8a8a92")
            self._rebuild_cache()
        except Exception as e:
            messagebox.showerror("Undo failed", str(e))

    def generate_structure(self, mode):
        root_dir = self.ent_path.get().strip()
        if not root_dir or not os.path.exists(root_dir):
            messagebox.showerror("Error", "Valid Root Directory Required.")
            return

        root_path = Path(root_dir)
        movie = self.entries["movie"].get().strip().replace(" ", "_")
        r_val = self.entries["reel"].get().strip()
        reel = f"Reel_{int(r_val):02d}" if r_val.isdigit() else r_val.replace(" ", "_")
        chars = [c.strip() for c in self.entries["chars"].get().split(",") if c.strip()]
        s_val = self.entries["scenes"].get().strip()
        scenes = int(s_val) if s_val.isdigit() else 0

        # Smart Folder Resolver: If the target path doesn't end with Movie/Reel, we build a subfolder structure
        if root_path.name == reel and root_path.parent.name == movie:
            project_root = root_path
        elif root_path.name == movie:
            project_root = root_path / reel
        else:
            if not movie or not reel:
                messagebox.showerror("Error", "Project Name and Reel Number are required to generate root folders.")
                return
            project_root = root_path / movie / reel

        try:
            # Mode A: Generates the core macro structure at the root project folder
            if mode == "all":
                macro_dirs = [
                    "Projects/Media/Exports/Raw Exports",
                    "Projects/Media/Exports/Source Exports",
                    "Projects/Media/Exports/Test",
                    "Projects/Media/Master Timeline",
                    "Projects/Media/Character Exports",
                    "Projects/Media/Stereo Downmixes",
                    "Projects/Media/03_After Effects",
                    "Projects/Media/z_Old",
                    "Projects/Media/09_Files",
                    "Projects/Media/08_Cropaway"
                ]
                for folder in macro_dirs:
                    os.makedirs(project_root / folder, exist_ok=True)

            # Mode B: If the project framework already exists, explicitly initializes standard delivery directories
            if mode in ("all", "exports"):
                exports_subdirs = ["Raw Exports", "Source Exports", "Test"]
                for sub in exports_subdirs:
                    os.makedirs(project_root / "Projects/Media/Exports" / sub, exist_ok=True)
                    os.makedirs(project_root / "Exports" / sub, exist_ok=True)

            # Mode C: Under Character Exports, builds character directories, scene folders, and 5 format subfolders
            if mode in ("all", "characters"):
                if not chars or scenes <= 0:
                    messagebox.showerror("Error", "Character names and a valid total Scene count are required.")
                    return

                # Guard against accidental huge deployments.
                total = len(chars) * scenes * len(FORMAT_FOLDERS)
                if total > 200 and not messagebox.askyesno(
                    "Confirm large deployment",
                    f"This will create up to {total} folders\n"
                    f"({len(chars)} characters × {scenes} scenes × {len(FORMAT_FOLDERS)} formats).\n\nProceed?"
                ):
                    return

                # EXACT nested path format: [Target Path] \ [Project_Name] \ Reel_[Number] \ Exports \ Character Exports
                char_base = project_root / "Exports" / "Character Exports"
                padding_width = max(2, len(str(scenes)))

                for char in chars:
                    for s in range(1, scenes + 1):
                        scene_folder_name = f"Scene_{s:0{padding_width}d}"
                        for fmt in FORMAT_FOLDERS:
                            os.makedirs(char_base / char / scene_folder_name / fmt, exist_ok=True)

            messagebox.showinfo("Success", f"Folders deployed successfully!\n\nProject Path:\n{project_root}")
            self.save_settings()
            self._rebuild_cache()
        except Exception as e:
            messagebox.showerror("Failure", str(e))

    # ---------- Translation QC tab (PRD/PRD_2_AI_Implementation.md §6) ----------

    # Language dropdowns show full names (client feedback round 4), engine
    # keeps using ISO codes. Names come from the engine's LANG_PROFILES so
    # adding a language there automatically surfaces here.
    _FALLBACK_LANG_NAMES = {"en": "English", "es": "Spanish (es-419)"}

    def _lang_maps(self):
        if tqc:
            code_to_name = {c: p["name"] for c, p in tqc.LANG_PROFILES.items()}
        else:
            code_to_name = dict(self._FALLBACK_LANG_NAMES)
        return code_to_name, {v: k for k, v in code_to_name.items()}

    def build_qc_ui(self):
        f = tk.Frame(self.qc_frame, bg="#121214", padx=20, pady=20)
        f.pack(fill=tk.BOTH, expand=True)

        tk.Label(f, text="OST Sheet (.csv / .xlsx):", fg="#b0b0b8", bg="#121214").pack(anchor=tk.W, pady=(0, 5))
        row1 = tk.Frame(f, bg="#121214")
        row1.pack(fill=tk.X, pady=(0, 12))
        self.qc_ent_file = tk.Entry(row1, bg="#1e1e24", fg="#ffffff", bd=1, relief="flat", insertbackground="white")
        self.qc_ent_file.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)
        tk.Button(row1, text="Browse", bg="#2d2d34", fg="white", relief="flat",
                  activebackground="#3e3e46", activeforeground="white",
                  command=self.qc_browse_file).pack(side=tk.RIGHT, padx=(5, 0))

        row2 = tk.Frame(f, bg="#121214")
        row2.pack(fill=tk.X, pady=(0, 12))
        code_to_name, _ = self._lang_maps()
        names = list(code_to_name.values())
        tk.Label(row2, text="Source language:", fg="#b0b0b8", bg="#121214").pack(side=tk.LEFT)
        self.qc_src_lang = tk.StringVar(value=code_to_name.get("en", names[0]))
        self.qc_tgt_lang = tk.StringVar(value=code_to_name.get("es", names[-1]))
        ttk.Combobox(row2, textvariable=self.qc_src_lang, values=names, width=17,
                     state="readonly").pack(side=tk.LEFT, padx=(6, 14))
        tk.Button(row2, text="⇄ Swap", bg="#2d2d34", fg="white", relief="flat",
                  activebackground="#3e3e46", activeforeground="white",
                  command=self.qc_swap_langs).pack(side=tk.LEFT, padx=(0, 14))
        tk.Label(row2, text="Target language:", fg="#b0b0b8", bg="#121214").pack(side=tk.LEFT)
        ttk.Combobox(row2, textvariable=self.qc_tgt_lang, values=names, width=17,
                     state="readonly").pack(side=tk.LEFT, padx=(6, 0))

        row3 = tk.Frame(f, bg="#121214")
        row3.pack(fill=tk.X, pady=(4, 4))
        self.qc_btn_run = tk.Button(
            row3, text="▶ Run Translation QC", bg="#007acc", fg="white",
            font=("Segoe UI", 10, "bold"), relief="flat",
            activebackground="#0098ff", activeforeground="white", command=self.qc_run)
        self.qc_btn_run.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)
        self.qc_btn_clear = tk.Button(
            row3, text="🗑 Clear", bg="#2d2d34", fg="white",
            font=("Segoe UI", 10, "bold"), relief="flat",
            activebackground="#3e3e46", activeforeground="white", command=self.qc_clear)
        self.qc_btn_clear.pack(side=tk.LEFT, padx=(6, 0), ipady=6, ipadx=8)

        self.qc_lbl_status = tk.Label(f, text="Pick a sheet and press Run. The source file is never modified.",
                                      fg="#8a8a92", bg="#121214", anchor=tk.W, justify=tk.LEFT)
        self.qc_lbl_status.pack(fill=tk.X, pady=(4, 6))

        # --- Summary strip: colored count chips, same palette as the Excel ---
        self.qc_summary = tk.Frame(f, bg="#121214")
        self.qc_summary.pack(fill=tk.X, pady=(0, 6))

        # --- Bottom bar: packed BEFORE the grid with side=BOTTOM so it keeps
        # its space at any window size (pack order = space priority in Tk).
        bottom = tk.Frame(f, bg="#121214")
        bottom.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))
        self.qc_filter_flagged = tk.BooleanVar(value=False)
        self.qc_chk_flagged = tk.Checkbutton(
            bottom, text="Flagged rows only", variable=self.qc_filter_flagged,
            command=self._qc_render_grid, bg="#121214", fg="#b0b0b8",
            activebackground="#121214", activeforeground="#ffffff",
            selectcolor="#1e1e24", state=tk.DISABLED)
        self.qc_chk_flagged.pack(side=tk.LEFT)
        self.qc_btn_open = tk.Button(
            bottom, text="📂 Open Highlighted Excel", bg="#28a745", fg="white",
            font=("Segoe UI", 10, "bold"), relief="flat", state=tk.DISABLED,
            activebackground="#218838", activeforeground="white", command=self.qc_open_result)
        self.qc_btn_open.pack(side=tk.RIGHT, ipady=4, ipadx=8)

        # --- Read-only result grid (client feedback round 4): view the QC
        # output inside the tool instead of switching to Excel. Treeview is
        # display-only by design - cells cannot be edited.
        grid_wrap = tk.Frame(f, bg="#1e1e24")
        grid_wrap.pack(fill=tk.BOTH, expand=True)
        cols = ("row", "source", "translation", "verdict", "severity", "reason")
        self.qc_tree = ttk.Treeview(grid_wrap, columns=cols, show="headings",
                                    style="QC.Treeview", selectmode="browse")
        headings = {"row": "#", "source": "Source", "translation": "Translation",
                    "verdict": "Verdict", "severity": "Severity", "reason": "Why"}
        widths = {"row": 46, "source": 230, "translation": 230,
                  "verdict": 120, "severity": 76, "reason": 320}
        for c in cols:
            self.qc_tree.heading(c, text=headings[c])
            stretch = c in ("source", "translation", "reason")
            self.qc_tree.column(c, width=widths[c], minwidth=44, stretch=stretch,
                                anchor=(tk.CENTER if c in ("row", "severity") else tk.W))
        vsb = ttk.Scrollbar(grid_wrap, orient="vertical", command=self.qc_tree.yview)
        hsb = ttk.Scrollbar(grid_wrap, orient="horizontal", command=self.qc_tree.xview)
        self.qc_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        grid_wrap.rowconfigure(0, weight=1)
        grid_wrap.columnconfigure(0, weight=1)
        self.qc_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        # Row colors = the Excel palette (SEV_FILL), so both views read the same.
        sev_fill = tqc.SEV_FILL if tqc else {
            "HIGH": ("FFC7CE", "9C0006"), "MEDIUM": ("FFEB9C", "9C6500"),
            "LOW": ("FFF2CC", "7F6000"), "PASS": ("C6EFCE", "006100"),
            "INFO": ("EDEDED", "3F3F3F"), "NOTE": ("BDD7EE", "1F4E79")}
        for sev, (bg, fg) in sev_fill.items():
            self.qc_tree.tag_configure(sev, background=f"#{bg}", foreground=f"#{fg}")
        # Read-only viewer: swallow edit-ish interactions (double-click resize
        # of headings is fine; cell editing doesn't exist in Treeview).
        self.qc_tree.bind("<Double-1>", lambda e: "break" if
                          self.qc_tree.identify_region(e.x, e.y) == "cell" else None)

        self.qc_last_out = None
        self._qc_grid_gen = 0      # invalidates in-flight chunked inserts

    GRID_MAX_ROWS = 5000           # in-app viewer cap; the Excel always has everything

    def qc_browse_file(self):
        sel = filedialog.askopenfilename(filetypes=[
            ("OST sheets (CSV / Excel)", "*.csv *.xlsx *.xlsm"),
            ("CSV sheets", "*.csv"),
            ("Excel workbooks", "*.xlsx *.xlsm"),
            ("All files", "*.*"),
        ])
        if sel:
            self.qc_ent_file.delete(0, tk.END)
            self.qc_ent_file.insert(0, sel)

    def qc_swap_langs(self):
        s, t = self.qc_src_lang.get(), self.qc_tgt_lang.get()
        self.qc_src_lang.set(t)
        self.qc_tgt_lang.set(s)

    def qc_clear(self):
        """Start-over button (client feedback round 4): fresh tab state."""
        if self.qc_running:
            return
        self._qc_grid_gen += 1                     # cancel in-flight inserts
        self.qc_ent_file.delete(0, tk.END)
        code_to_name, _ = self._lang_maps()
        self.qc_src_lang.set(code_to_name.get("en", next(iter(code_to_name.values()))))
        self.qc_tgt_lang.set(code_to_name.get("es", next(iter(code_to_name.values()))))
        self.qc_last_res = None
        self.qc_last_out = None
        self.qc_filter_flagged.set(False)
        self.qc_tree.delete(*self.qc_tree.get_children())
        for w in self.qc_summary.winfo_children():
            w.destroy()
        self.qc_btn_open.config(state=tk.DISABLED)
        self.qc_chk_flagged.config(state=tk.DISABLED)
        self.qc_lbl_status.config(text="Pick a sheet and press Run. The source file is never modified.")

    def qc_run(self):
        if tqc is None:
            messagebox.showerror("Unavailable", "translation_qc module not found next to the app.")
            return
        if self.qc_running:
            return
        path = self.qc_ent_file.get().strip()
        if not path or not os.path.isfile(path):
            messagebox.showerror("Error", "Pick a valid .csv or .xlsx sheet first.")
            return
        _, name_to_code = self._lang_maps()
        src = name_to_code.get(self.qc_src_lang.get(), self.qc_src_lang.get())
        tgt = name_to_code.get(self.qc_tgt_lang.get(), self.qc_tgt_lang.get())
        if src == tgt:
            messagebox.showerror("Error", "Source and target language must differ.")
            return

        self.qc_running = True
        self._qc_grid_gen += 1
        self.qc_btn_run.config(state=tk.DISABLED, text="Running…")
        self.qc_btn_clear.config(state=tk.DISABLED)
        self.qc_btn_open.config(state=tk.DISABLED)
        self.qc_chk_flagged.config(state=tk.DISABLED)
        self.qc_tree.delete(*self.qc_tree.get_children())
        for w in self.qc_summary.winfo_children():
            w.destroy()
        self.qc_lbl_status.config(text="Analysing…")

        def progress(done, total):
            self.root.after(0, lambda: self.qc_lbl_status.config(text=f"Analysing… {done}/{total} rows"))

        def worker():
            try:
                tqc.load_allowlist_file(os.path.join(tqc.app_dir(), "qc_allowlist.txt"))
                res = tqc.run_qc(path, src, tgt, progress_cb=progress)
                self.root.after(0, lambda: self._qc_done(res))
            except SystemExit as e:
                self.root.after(0, lambda: self._qc_fail(str(e)))
            except Exception as e:
                self.root.after(0, lambda: self._qc_fail(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _qc_fail(self, msg):
        self.qc_running = False
        self.qc_btn_run.config(state=tk.NORMAL, text="▶ Run Translation QC")
        self.qc_btn_clear.config(state=tk.NORMAL)
        self.qc_lbl_status.config(text="Failed.")
        messagebox.showerror("Translation QC failed", msg)

    def _qc_chip(self, text, bg, fg):
        tk.Label(self.qc_summary, text=text, bg=f"#{bg}", fg=f"#{fg}",
                 font=("Segoe UI", 9, "bold"), padx=8, pady=2).pack(side=tk.LEFT, padx=(0, 6))

    def _qc_done(self, res):
        self.qc_running = False
        self.qc_btn_run.config(state=tk.NORMAL, text="▶ Run Translation QC")
        self.qc_btn_clear.config(state=tk.NORMAL)
        self.qc_chk_flagged.config(state=tk.NORMAL)
        self.qc_last_res = res
        self.qc_last_out = res["out"]
        self.qc_btn_open.config(state=tk.NORMAL)

        sev = res["severity"]
        total = len(res["results"])
        flagged = sev.get("HIGH", 0) + sev.get("MEDIUM", 0)
        self.qc_lbl_status.config(
            text=f"Done - {total} rows analysed, {flagged} need action. "
                 f"Source file untouched. Report: {os.path.basename(res['out'])}")

        for w in self.qc_summary.winfo_children():
            w.destroy()
        self._qc_chip(f"Rows {total}", "2d2d34", "ffffff")
        sev_fill = tqc.SEV_FILL if tqc else {}
        order = [("HIGH", "fix"), ("MEDIUM", "check language"), ("LOW", "eyeball"),
                 ("NOTE", "already target-lang"), ("PASS", "ok")]
        for k, hint in order:
            n = res["src_notes"] if k == "NOTE" else sev.get(k, 0)
            if n:
                bg, fg = sev_fill.get(k, ("2d2d34", "ffffff"))
                self._qc_chip(f"{k} {n} · {hint}", bg, fg)

        # Column headers reflect the real mapped sheet columns
        data = res["data"]
        self.qc_tree.heading("source", text=data["headers"][data["src_idx"]] or "Source")
        self.qc_tree.heading("translation", text=data["headers"][data["tgt_idx"]] or "Translation")
        self._qc_render_grid()

    def _qc_render_grid(self):
        """(Re)fill the read-only viewer from the last result, chunked so the
        UI never freezes; a generation counter cancels stale fills."""
        self._qc_grid_gen += 1
        gen = self._qc_grid_gen
        self.qc_tree.delete(*self.qc_tree.get_children())
        res = self.qc_last_res
        if not res:
            return
        data, results = res["data"], res["results"]
        si, ti = data["src_idx"], data["tgt_idx"]
        flagged_only = self.qc_filter_flagged.get()

        items = []
        for i, (row, v) in enumerate(zip(data["rows"], results), start=2):  # row 2 = first data row, matches Excel
            if flagged_only and v.severity not in ("HIGH", "MEDIUM"):
                continue
            tag = "NOTE" if v.src_flag else (v.severity if v.severity else "INFO")
            items.append((i, row[si] if si < len(row) else "",
                          row[ti] if ti < len(row) else "", v.verdict, v.severity, v.reason, tag))
            if len(items) >= self.GRID_MAX_ROWS:
                break

        truncated = len(items) >= self.GRID_MAX_ROWS
        CHUNK = 400

        def insert(from_idx=0):
            if gen != self._qc_grid_gen:
                return                              # a newer render/clear superseded us
            for it in items[from_idx:from_idx + CHUNK]:
                self.qc_tree.insert("", tk.END, values=it[:6], tags=(it[6],))
            nxt = from_idx + CHUNK
            if nxt < len(items):
                self.root.after(1, lambda: insert(nxt))
            elif truncated:
                self.qc_tree.insert("", tk.END, tags=("INFO",), values=(
                    "…", f"Viewer capped at {self.GRID_MAX_ROWS} rows",
                    "open the Excel for the full sheet", "", "", ""))

        insert()

    def qc_open_result(self):
        if self.qc_last_out and os.path.exists(self.qc_last_out):
            open_in_file_manager(self.qc_last_out)

    # ---------- Quick search (debounced + off the UI thread) ----------

    def _schedule_search(self, event=None):
        """Debounce keystrokes so filtering runs at most ~every 220ms."""
        if self._search_job:
            self.root.after_cancel(self._search_job)
        self._search_job = self.root.after(220, self._apply_filter)

    def _rebuild_cache(self):
        """Walk the project tree once on a background thread, then refresh results."""
        root_dir = self.ent_path.get().strip()
        if not root_dir or not os.path.exists(root_dir):
            self._folder_cache = []
            self._apply_filter()
            return

        def worker(rd):
            found = []
            for r, dirs, _ in os.walk(rd):
                for d in dirs:
                    found.append(os.path.join(r, d))
                if len(found) >= 50000:
                    break
            self._folder_cache = found[:50000]
            # Marshal the UI update back onto the Tk main thread.
            self.root.after(0, self._apply_filter)

        threading.Thread(target=worker, args=(root_dir,), daemon=True).start()

    def _apply_filter(self, event=None):
        """Filter the cached folder list in-memory (fast, main-thread safe)."""
        words = self.ent_search.get().strip().lower().split()
        self.listbox.delete(0, tk.END)
        count = 0
        for fp in self._folder_cache:
            low = fp.lower()
            if not words or all(w in low for w in words):
                self.listbox.insert(tk.END, fp)
                count += 1
                if count >= 300:
                    break

    def open_search_path(self, event=None):
        sel = self.listbox.get(tk.ACTIVE)
        if sel:
            open_in_file_manager(sel)

    # ---------- Settings persistence ----------

    def load_settings(self):
        try:
            if SETTINGS_FILE.exists():
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                if data.get("path"):
                    self.ent_path.insert(0, data["path"])
                for key in ("movie", "reel", "chars", "scenes"):
                    if data.get(key):
                        self.entries[key].insert(0, data[key])
                if data.get("geometry"):
                    try:
                        self.root.geometry(data["geometry"])
                    except tk.TclError:
                        pass
                qc = data.get("qc") or {}
                # Back-compat: settings hold ISO codes ("en"); dropdowns show
                # full names ("English"). Values already in name form pass through.
                code_to_name, _ = self._lang_maps()
                if qc.get("src"):
                    self.qc_src_lang.set(code_to_name.get(qc["src"], qc["src"]))
                if qc.get("tgt"):
                    self.qc_tgt_lang.set(code_to_name.get(qc["tgt"], qc["tgt"]))
                if qc.get("file"):
                    self.qc_ent_file.insert(0, qc["file"])
        except Exception:
            pass

    def save_settings(self):
        try:
            _, name_to_code = self._lang_maps()
            data = {
                "path": self.ent_path.get().strip(),
                "movie": self.entries["movie"].get().strip(),
                "reel": self.entries["reel"].get().strip(),
                "chars": self.entries["chars"].get().strip(),
                "scenes": self.entries["scenes"].get().strip(),
                "geometry": self.root.winfo_geometry(),
                "qc": {
                    "src": name_to_code.get(self.qc_src_lang.get(), self.qc_src_lang.get()),
                    "tgt": name_to_code.get(self.qc_tgt_lang.get(), self.qc_tgt_lang.get()),
                    "file": self.qc_ent_file.get().strip(),
                },
            }
            SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def on_close(self):
        self.save_settings()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = SyncFlowApp(root)
    root.mainloop()
