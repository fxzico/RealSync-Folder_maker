import os
import sys
import time
import threading
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# Attempt to import global keyboard listener hook
try:
    from pynput import keyboard
except ImportError:
    keyboard = None

# ==========================================
# 1. CORE UTILITIES & PLATFORM INTEGRATION
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

def get_selected_file_paths():
    """
    Retrieves the currently selected file path from Windows Explorer or Mac Finder.
    Preserves and restores the user's previous clipboard content on Windows.
    """
    if sys.platform == "win32":
        import ctypes
        
        # Backup original clipboard text
        orig_text = ""
        try:
            orig_text = get_clipboard_text_win()
        except Exception:
            pass
            
        # Simulate Ctrl+C to copy selected item path
        user32 = ctypes.windll.user32
        user32.keybd_event(0x11, 0, 0, 0)      # Ctrl Down
        user32.keybd_event(0x43, 0, 0, 0)      # C Down
        user32.keybd_event(0x43, 0, 2, 0)      # C Up
        user32.keybd_event(0x11, 0, 2, 0)      # Ctrl Up
        
        time.sleep(0.15)  # Wait for clipboard to update safely
        
        # Access native Windows Clipboard to read file paths
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
            proc = subprocess.run(['osascript', '-e', ascript], capture_output=True, text=True)
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
        subprocess.run(["open", path])
    else:
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
        
        skip_names = {"exports", "media", "projects"}
        
        # Find Reel folder
        while temp_idx >= 0 and parts[temp_idx].lower() in skip_names:
            temp_idx -= 1
        if temp_idx >= 0:
            reel = parts[temp_idx]
            temp_idx -= 1
            
        # Find Movie folder
        while temp_idx >= 0 and parts[temp_idx].lower() in skip_names:
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

class RenameOverlay:
    """Minimalist borderless always-on-top modal for Shot number entry."""
    def __init__(self, root, context):
        self.root = root
        self.context = context
        
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
        
        # Content Widgets
        lbl_info = tk.Label(
            self.win, 
            text=f"Detected: {context['movie']} | {context['reel']} | {context['scene']} | {context['character']} | {context['format_folder']}",
            fg="#a4a4a4", bg="#1e1e24", font=("Segoe UI", 9, "bold")
        )
        lbl_info.pack(pady=(15, 5), padx=15)
        
        lbl_prompt = tk.Label(self.win, text="Enter Shot Number:", fg="#ffffff", bg="#1e1e24", font=("Segoe UI", 11))
        lbl_prompt.pack()
        
        self.entry = tk.Entry(
            self.win, bg="#2d2d34", fg="#ffffff", insertbackground="white", 
            font=("Segoe UI", 12), justify="center", bd=0, highlightthickness=1, 
            highlightbackground="#44444c"
        )
        self.entry.pack(pady=5, ipady=3, width=120)
        self.entry.focus_set()
        
        self.win.bind("<Return>", self.execute_rename)
        self.win.bind("<Escape>", lambda e: self.win.destroy())
        
        self.win.deiconify()

    def execute_rename(self, event=None):
        shot_num = self.entry.get().strip()
        if not shot_num:
            return
            
        ctx = self.context
        fmt = ctx['format_folder'].lower()
        
        # Setup specific structural suffix and target extension matching the naming matrix table
        suffix = ""
        ext = ctx['extension'].lower()
        
        if "mp4" in fmt:
            suffix = ""
            ext = ".mp4"
        elif "quicktime" in fmt:
            suffix = ""
            ext = ".mov"
        elif "audio" in fmt:
            suffix = "_Audio"
            ext = ".mp3"
        elif "syncso" in fmt:
            suffix = "_Sync_so"
            ext = ".mp4"
        elif "lipdub" in fmt:
            suffix = "_Lipdub"
            ext = ".mov"
            
        # Standardise zero padding formats
        shot_str = f"Shot_{int(shot_num):02d}" if shot_num.isdigit() else f"Shot_{shot_num}"
        
        new_name = f"{ctx['movie']}_{ctx['reel']}_{ctx['scene']}_{ctx['character']}_{shot_str}{suffix}{ext}"
        target_path = ctx['original_path'].parent / new_name
        
        try:
            os.rename(ctx['original_path'], target_path)
        except Exception as e:
            messagebox.showerror("Error", f"Rename failed:\n{e}")
        finally:
            self.win.destroy()

# ==========================================
# 3. GLOBAL KEYBOARD HOOK LISTENER
# ==========================================

class DoubleShiftListener:
    def __init__(self, trigger_callback):
        self.trigger_callback = trigger_callback
        self.last_shift_time = 0
        self.threshold = 0.4
        
    def on_press(self, key):
        if key in (keyboard.Key.shift, keyboard.Key.shift_r):
            now = time.time()
            if now - self.last_shift_time < self.threshold:
                self.last_shift_time = 0
                self.trigger_callback()
            else:
                self.last_shift_time = now

    def start(self):
        if keyboard:
            listener = keyboard.Listener(on_press=self.on_press)
            listener.daemon = True
            listener.start()

# ==========================================
# 4. MAIN GRAPHICAL USER INTERFACE & SEARCH
# ==========================================

class SyncFlowApp:
    def __init__(self, root):
        self.root = root
        self.root.title("SyncFlow Automator")
        self.root.geometry("680x580")
        self.root.configure(bg="#121214")
        
        self.setup_styles()
        self.build_ui()
        
        if keyboard:
            self.listener = DoubleShiftListener(self.handle_global_rename_trigger)
            self.listener.start()

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background="#121214", foreground="#ffffff", font=("Segoe UI", 10))
        style.configure("TLabel", background="#121214", foreground="#b0b0b8")
        style.configure("TFrame", background="#121214")
        style.configure("TEntry", fieldbackground="#1e1e24", foreground="#ffffff", borderwidth=0)
        style.map("TEntry", fieldbackground=[("focus", "#2d2d34")])
        style.configure("Action.TButton", background="#007acc", foreground="#ffffff", borderwidth=0, font=("Segoe UI", 10, "bold"))
        style.map("Action.TButton", background=[("active", "#0098ff")])
        style.configure("Secondary.TButton", background="#2d2d34", foreground="#ffffff", borderwidth=0)
        style.map("Secondary.TButton", background=[("active", "#3e3e46")])

    def build_ui(self):
        main_frame = ttk.Frame(self.root, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        path_frame = ttk.Frame(main_frame)
        path_frame.pack(fill=tk.X, pady=5)
        ttk.Label(path_frame, text="Target Project Root Directory:").pack(anchor=tk.W)
        self.ent_path = tk.Entry(path_frame, bg="#1e1e24", fg="#ffffff", bd=0, insertbackground="white")
        self.ent_path.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4, pady=2)
        ttk.Button(path_frame, text="Browse", style="Secondary.TButton", command=self.browse_dir).pack(side=tk.RIGHT, padx=(5, 0))
        
        grid_frame = ttk.Frame(main_frame)
        grid_frame.pack(fill=tk.X, pady=10)
        
        ttk.Label(grid_frame, text="Project Name:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.ent_movie = tk.Entry(grid_frame, bg="#1e1e24", fg="#ffffff", bd=0, insertbackground="white")
        self.ent_movie.grid(row=0, column=1, sticky=tk.EW, padx=(5, 20), ipady=4)
        
        ttk.Label(grid_frame, text="Reel Number:").grid(row=0, column=2, sticky=tk.W, pady=4)
        self.ent_reel = tk.Entry(grid_frame, bg="#1e1e24", fg="#ffffff", bd=0, insertbackground="white")
        self.ent_reel.grid(row=0, column=3, sticky=tk.EW, ipady=4)
        
        ttk.Label(grid_frame, text="Characters (comma separated):").grid(row=1, column=0, sticky=tk.W, pady=8)
        self.ent_chars = tk.Entry(grid_frame, bg="#1e1e24", fg="#ffffff", bd=0, insertbackground="white")
        self.ent_chars.grid(row=1, column=1, sticky=tk.EW, padx=(5, 20), ipady=4)
        
        ttk.Label(grid_frame, text="Number of Scenes:").grid(row=1, column=2, sticky=tk.W, pady=8)
        self.ent_scenes = tk.Entry(grid_frame, bg="#1e1e24", fg="#ffffff", bd=0, insertbackground="white")
        self.ent_scenes.grid(row=1, column=3, sticky=tk.EW, ipady=4)
        
        grid_frame.columnconfigure(1, weight=1)
        grid_frame.columnconfigure(3, weight=1)
        
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=15)
        
        ttk.Button(btn_frame, text="Mode A: [All Structure]", style="Action.TButton", command=lambda: self.generate_structure("all")).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(btn_frame, text="Mode B: [Exports Only]", style="Secondary.TButton", command=lambda: self.generate_structure("exports")).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(btn_frame, text="Mode C: [Character Exports Only]", style="Secondary.TButton", command=lambda: self.generate_structure("characters")).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        sep = ttk.Separator(main_frame, orient='horizontal')
        sep.pack(fill=tk.X, pady=10)

        ttk.Label(main_frame, text="Quick Search (e.g., 'Char_A Scene 1')", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W)
        self.ent_search = tk.Entry(main_frame, bg="#1e1e24", fg="#ffffff", bd=0, insertbackground="white")
        self.ent_search.pack(fill=tk.X, ipady=4, pady=5)
        self.ent_search.bind("<KeyRelease>", self.update_search)
        
        self.listbox = tk.Listbox(main_frame, bg="#1e1e24", fg="#ffffff", selectbackground="#007acc", selectforeground="white", bd=0, highlightthickness=0)
        self.listbox.pack(fill=tk.BOTH, expand=True, pady=2)
        self.listbox.bind("<Double-Button-1>", self.open_selected_search_path)
        self.listbox.bind("<Return>", self.open_selected_search_path)

    def browse_dir(self):
        selected = filedialog.askdirectory()
        if selected:
            self.ent_path.delete(0, tk.END)
            self.ent_path.insert(0, selected)

    def get_inputs(self):
        return {
            "root": self.ent_path.get().strip(),
            "movie": self.ent_movie.get().strip().replace(" ", "_"),
            "reel": f"Reel_{int(r):02d}" if (r := self.ent_reel.get().strip()).isdigit() else r.replace(" ", "_"),
            "characters": [c.strip() for c in self.ent_chars.get().split(",") if c.strip()],
            "scenes": int(s) if (s := self.ent_scenes.get().strip()).isdigit() else 0
        }

    def generate_structure(self, mode):
        inputs = self.get_inputs()
        if not inputs["root"] or not os.path.exists(inputs["root"]):
            messagebox.showerror("Error", "Please input a valid Root Directory Path.")
            return
            
        root_dir = Path(inputs["root"])
        movie = inputs["movie"]
        reel = inputs["reel"]
        
        # Smart Folder Resolver: If the target path doesn't end with Movie/Reel, we build a subfolder structure
        if root_dir.name == reel and root_dir.parent.name == movie:
            project_root = root_dir
        elif root_dir.name == movie:
            project_root = root_dir / reel
        else:
            if not movie or not reel:
                messagebox.showerror("Error", "Project Name and Reel Number are required to generate root folders.")
                return
            project_root = root_dir / movie / reel
            
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
                # We initialize standard delivery folders in both direct Exports and Projects/Media/Exports
                for sub in exports_subdirs:
                    os.makedirs(project_root / "Projects/Media/Exports" / sub, exist_ok=True)
                    os.makedirs(project_root / "Exports" / sub, exist_ok=True)
                    
            # Mode C: Under Character Exports, builds character directories, scene folders, and 5 format subfolders
            if mode in ("all", "characters"):
                if not inputs["characters"] or inputs["scenes"] <= 0:
                    messagebox.showerror("Error", "Character names and a valid total Scene count are required for this action.")
                    return
                
                # To be robust, we populate folders in multiple possible layout standards
                char_bases = [
                    project_root / "Projects/Media/Character Exports",
                    project_root / "Character Exports",
                    project_root / "Exports/Character Exports"
                ]
                formats = ["MP4", "Quicktime", "SyncSO", "Lipdub", "Audio"]
                
                # Dynamic zero-padding width based on scene count
                padding_width = max(2, len(str(inputs["scenes"])))
                
                for char in inputs["characters"]:
                    for s_num in range(1, inputs["scenes"] + 1):
                        scene_folder_name = f"Scene_{s_num:0{padding_width}d}"
                        for base in char_bases:
                            for fmt in formats:
                                os.makedirs(base / char / scene_folder_name / fmt, exist_ok=True)
                                
            messagebox.showinfo("Success", f"Structure built successfully via Mode: {mode.upper()}\nProject Path: {project_root}")
            self.update_search()
        except Exception as e:
            messagebox.showerror("Failure", f"An unexpected error occurred:\n{e}")

    def update_search(self, event=None):
        query = self.ent_search.get().strip().lower()
        root_dir = self.ent_path.get().strip()
        self.listbox.delete(0, tk.END)
        
        if not root_dir or not os.path.exists(root_dir):
            return
            
        query_words = query.split()
        match_count = 0
        
        for root, dirs, files in os.walk(root_dir):
            for d in dirs:
                full_path = os.path.join(root, d)
                normalized_path = full_path.lower()
                
                if not query_words or all(word in normalized_path for word in query_words):
                    self.listbox.insert(tk.END, full_path)
                    match_count += 1
                    if match_count >= 150:
                        return

    def open_selected_search_path(self, event=None):
        selection = self.listbox.get(tk.ACTIVE)
        if selection and os.path.exists(selection):
            open_in_file_manager(selection)

    def handle_global_rename_trigger(self):
        self.root.after(0, self.execute_rename_popup_flow)

    def execute_rename_popup_flow(self):
        paths = get_selected_file_paths()
        if not paths:
            return
            
        target_file = paths[0]
        context = parse_context_from_path(target_file)
        
        if context:
            RenameOverlay(self.root, context)

if __name__ == "__main__":
    root = tk.Tk()
    app = SyncFlowApp(root)
    root.mainloop()
