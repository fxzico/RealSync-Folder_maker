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
APP_VERSION = "1.1.0"

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
        self.root.geometry("680x600")
        self.root.configure(bg="#121214")

        # State
        self._folder_cache = []    # cached folder paths for instant search
        self._search_job = None    # debounce handle
        self.last_rename = None     # (new_path, old_path) for one-step undo

        self.build_clean_ui()
        self.load_settings()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        if self.ent_path.get().strip():
            self._rebuild_cache()

    def build_clean_ui(self):
        main_frame = tk.Frame(self.root, bg="#121214", padx=20, pady=20)
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
        except Exception:
            pass

    def save_settings(self):
        try:
            data = {
                "path": self.ent_path.get().strip(),
                "movie": self.entries["movie"].get().strip(),
                "reel": self.entries["reel"].get().strip(),
                "chars": self.entries["chars"].get().strip(),
                "scenes": self.entries["scenes"].get().strip(),
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
