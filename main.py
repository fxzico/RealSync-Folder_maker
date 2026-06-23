import os
import sys
import time
import threading
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

# Import CustomTkinter for sleek modern appearance
import customtkinter as ctk

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
        # Robust AppleScript to fetch selected item directly from macOS Finder
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
    """Opens the specified directory path directly in the native OS file manager."""
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
        movie = "Movie"
        
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

class RenameOverlay(ctk.CTkToplevel):
    """Minimalist borderless always-on-top modal for Shot number entry."""
    def __init__(self, parent, context):
        super().__init__(parent)
        self.parent = parent
        self.context = context
        
        # Borderless and Always-on-top window setup
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color="#18181a")
        
        # Center Screen Alignment Calculation
        self.update_idletasks()
        w, h = 500, 160
        sx = (self.winfo_screenwidth() // 2) - (w // 2)
        sy = (self.winfo_screenheight() // 2) - (h // 2)
        self.geometry(f"{w}x{h}+{sx}+{sy}")
        
        # Background canvas frame to draw border
        bg_frame = ctk.CTkFrame(self, fg_color="#18181a", border_color="#00adb5", border_width=1, corner_radius=8)
        bg_frame.pack(fill=tk.BOTH, expand=True)
        
        # Content Widgets
        lbl_info = ctk.CTkLabel(
            bg_frame, 
            text=f"Detected: {context['movie']} | {context['reel']} | {context['scene']} | {context['character']} | {context['format_folder']}",
            text_color="#8e8e93", font=("Segoe UI", 11, "bold")
        )
        lbl_info.pack(pady=(20, 5), padx=15)
        
        lbl_prompt = ctk.CTkLabel(bg_frame, text="Enter Shot Number:", text_color="#ffffff", font=("Segoe UI", 13))
        lbl_prompt.pack(pady=2)
        
        self.entry = ctk.CTkEntry(
            bg_frame, fg_color="#242428", text_color="#ffffff", 
            insert_color="white", font=("Segoe UI", 14), justify="center",
            width=140, height=32, corner_radius=6, border_color="#3e3e42"
        )
        self.entry.pack(pady=10)
        
        # Auto-focus the entry field
        self.after(100, self.force_focus)
        
        self.bind("<Return>", self.execute_rename)
        self.bind("<Escape>", lambda e: self.destroy())

    def force_focus(self):
        self.focus_force()
        self.entry.focus_set()

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
            if target_path.exists():
                if not messagebox.askyesno("File Conflict", f"File '{new_name}' already exists.\nDo you want to overwrite it?"):
                    return
            
            os.rename(ctx['original_path'], target_path)
            self.parent.show_status(f"Successfully renamed file to '{new_name}'", "success")
        except Exception as e:
            messagebox.showerror("Rename Error", f"Failed to rename file:\n{e}")
        finally:
            self.destroy()

# ==========================================
# 3. GLOBAL KEYBOARD HOOK LISTENER
# ==========================================

class DoubleShiftListener:
    def __init__(self, trigger_callback):
        self.trigger_callback = trigger_callback
        self.last_shift_time = 0
        self.threshold = 0.4  # Time window for double tap (400ms)
        
    def on_press(self, key):
        if key in (keyboard.Key.shift, keyboard.Key.shift_r):
            now = time.time()
            if now - self.last_shift_time < self.threshold:
                self.last_shift_time = 0  # reset to avoid triple-tap double-firing
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

class SyncFlowApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # Theme configuration
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        
        self.title("SyncFlow Automator")
        self.geometry("720x640")
        self.configure(fg_color="#121214")
        
        # Build layout
        self.build_ui()
        
        # Initialize Global Double Shift Hook
        if keyboard:
            try:
                self.listener = DoubleShiftListener(self.handle_global_rename_trigger)
                self.listener.start()
                self.show_status("Double-Shift Listener: Active", "success")
            except Exception as e:
                self.show_status(f"Failed to start hotkey listener: {e}", "error")
        else:
            self.show_status("Double-Shift Listener: Disabled (pynput missing)", "warning")
            messagebox.showwarning("Dependency Warning", "pynput is not installed. Global Double-Shift hotkey renaming is unavailable.")

    def build_ui(self):
        # Master Frame
        main_frame = ctk.CTkFrame(self, fg_color="#121214")
        main_frame.pack(fill=tk.BOTH, expand=True, padx=24, pady=16)
        
        # Title Label
        title_lbl = ctk.CTkLabel(
            main_frame, text="SYNCFLOW AUTOMATOR", 
            font=("Segoe UI", 18, "bold"), text_color="#00adb5"
        )
        title_lbl.pack(anchor=tk.W, pady=(0, 15))
        
        # Path Entry Frame
        path_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        path_frame.pack(fill=tk.X, pady=4)
        
        path_lbl = ctk.CTkLabel(path_frame, text="Target Project Root Directory:", text_color="#e0e0e6", font=("Segoe UI", 11, "bold"))
        path_lbl.pack(anchor=tk.W, pady=2)
        
        path_input_row = ctk.CTkFrame(path_frame, fg_color="transparent")
        path_input_row.pack(fill=tk.X)
        
        self.ent_path = ctk.CTkEntry(
            path_input_row, fg_color="#1e1e24", text_color="#ffffff", 
            border_color="#3e3e42", height=32, corner_radius=6,
            placeholder_text="e.g. C:\\RealSyncProjects\\"
        )
        self.ent_path.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        btn_browse = ctk.CTkButton(
            path_input_row, text="Browse", width=80, height=32, 
            fg_color="#2d2d34", hover_color="#3e3e46", text_color="#ffffff",
            font=("Segoe UI", 11, "bold"), corner_radius=6, command=self.browse_dir
        )
        btn_browse.pack(side=tk.RIGHT, padx=(8, 0))
        
        # Variable Parameter Grid
        grid_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        grid_frame.pack(fill=tk.X, pady=12)
        
        # Col 0, 1: Movie, Reel
        lbl_movie = ctk.CTkLabel(grid_frame, text="Movie/Series Name:", text_color="#b0b0b8", font=("Segoe UI", 11))
        lbl_movie.grid(row=0, column=0, sticky=tk.W, pady=4)
        self.ent_movie = ctk.CTkEntry(grid_frame, fg_color="#1e1e24", border_color="#3e3e42", height=30, width=180, placeholder_text="e.g. Blood_Brother")
        self.ent_movie.grid(row=0, column=1, sticky=tk.W, padx=(5, 20), pady=4)
        
        lbl_reel = ctk.CTkLabel(grid_frame, text="Reel Number:", text_color="#b0b0b8", font=("Segoe UI", 11))
        lbl_reel.grid(row=0, column=2, sticky=tk.W, pady=4)
        self.ent_reel = ctk.CTkEntry(grid_frame, fg_color="#1e1e24", border_color="#3e3e42", height=30, width=180, placeholder_text="e.g. 01")
        self.ent_reel.grid(row=0, column=3, sticky=tk.W, pady=4)
        
        # Col 2, 3: Characters, Scenes
        lbl_chars = ctk.CTkLabel(grid_frame, text="Character Names:", text_color="#b0b0b8", font=("Segoe UI", 11))
        lbl_chars.grid(row=1, column=0, sticky=tk.W, pady=8)
        self.ent_chars = ctk.CTkEntry(grid_frame, fg_color="#1e1e24", border_color="#3e3e42", height=30, width=180, placeholder_text="e.g. Ariff, Rohan, Maya")
        self.ent_chars.grid(row=1, column=1, sticky=tk.W, padx=(5, 20), pady=8)
        
        lbl_scenes = ctk.CTkLabel(grid_frame, text="Number of Scenes:", text_color="#b0b0b8", font=("Segoe UI", 11))
        lbl_scenes.grid(row=1, column=2, sticky=tk.W, pady=8)
        self.ent_scenes = ctk.CTkEntry(grid_frame, fg_color="#1e1e24", border_color="#3e3e42", height=30, width=180, placeholder_text="e.g. 12")
        self.ent_scenes.grid(row=1, column=3, sticky=tk.W, pady=8)
        
        # Action Buttons Layout Frame
        btn_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        btn_frame.pack(fill=tk.X, pady=8)
        
        self.btn_mode_a = ctk.CTkButton(
            btn_frame, text="Mode A: [All Structure]", fg_color="#00adb5", hover_color="#00989f",
            text_color="#121214", font=("Segoe UI", 12, "bold"), height=36, corner_radius=6,
            command=lambda: self.generate_structure("all")
        )
        self.btn_mode_a.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        
        self.btn_mode_b = ctk.CTkButton(
            btn_frame, text="Mode B: [Exports Only]", fg_color="#2d2d34", hover_color="#3e3e46",
            text_color="#ffffff", font=("Segoe UI", 12), height=36, corner_radius=6,
            command=lambda: self.generate_structure("exports")
        )
        self.btn_mode_b.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        
        self.btn_mode_c = ctk.CTkButton(
            btn_frame, text="Mode C: [Character Exports]", fg_color="#2d2d34", hover_color="#3e3e46",
            text_color="#ffffff", font=("Segoe UI", 12), height=36, corner_radius=6,
            command=lambda: self.generate_structure("characters")
        )
        self.btn_mode_c.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        # Separator Line
        sep = ctk.CTkFrame(main_frame, height=2, fg_color="#242428")
        sep.pack(fill=tk.X, pady=16)

        # Integrated Quick Search Section
        search_lbl = ctk.CTkLabel(main_frame, text="Jump-to-Folder Quick Search:", text_color="#e0e0e6", font=("Segoe UI", 12, "bold"))
        search_lbl.pack(anchor=tk.W, pady=(0, 2))
        
        self.ent_search = ctk.CTkEntry(
            main_frame, fg_color="#1e1e24", text_color="#ffffff", 
            border_color="#3e3e42", height=32, corner_radius=6,
            placeholder_text="Type keywords (e.g. 'Ariff Scene 3') and press Enter to open folder..."
        )
        self.ent_search.pack(fill=tk.X, pady=4)
        self.ent_search.bind("<KeyRelease>", self.update_search)
        self.ent_search.bind("<Return>", self.open_selected_search_path)
        
        # Search Results Frame and ListBox
        results_frame = ctk.CTkFrame(main_frame, fg_color="#1e1e24", border_color="#3e3e42", border_width=1, corner_radius=6)
        results_frame.pack(fill=tk.BOTH, expand=True, pady=4)
        
        # Scrollbar for listbox
        scrollbar = tk.Scrollbar(results_frame, orient="vertical", width=12)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.listbox = tk.Listbox(
            results_frame, bg="#1e1e24", fg="#ffffff", selectbackground="#00adb5", 
            selectforeground="#121214", bd=0, highlightthickness=0, font=("Segoe UI", 10),
            yscrollcommand=scrollbar.set
        )
        self.listbox.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        scrollbar.config(command=self.listbox.yview)
        
        self.listbox.bind("<Double-Button-1>", self.open_selected_search_path)
        
        # Status Bar Footer
        self.status_bar = ctk.CTkLabel(
            self, text="Ready", height=24, fg_color="#18181a", 
            text_color="#8e8e93", font=("Segoe UI", 10), anchor=tk.W
        )
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=0, pady=0)

    def browse_dir(self):
        selected = filedialog.askdirectory()
        if selected:
            self.ent_path.delete(0, tk.END)
            self.ent_path.insert(0, selected)

    def get_inputs(self):
        movie_name = self.ent_movie.get().strip().replace(" ", "_")
        r_val = self.ent_reel.get().strip()
        reel_num = f"Reel_{int(r_val):02d}" if r_val.isdigit() else r_val.replace(" ", "_")
        
        return {
            "root": self.ent_path.get().strip(),
            "movie": movie_name,
            "reel": reel_num,
            "characters": [c.strip() for c in self.ent_chars.get().split(",") if c.strip()],
            "scenes": int(s) if (s := self.ent_scenes.get().strip()).isdigit() else 0
        }

    def generate_structure(self, mode):
        inputs = self.get_inputs()
        if not inputs["root"] or not os.path.exists(inputs["root"]):
            messagebox.showerror("Validation Error", "Please select a valid Target Project Root Directory.")
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
                messagebox.showerror("Validation Error", "Movie/Series Name and Reel Number are required to generate root folders.")
                return
            project_root = root_dir / movie / reel
            
        t_start = time.time()
        
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
                    messagebox.showerror("Validation Error", "Character names and a valid total Scene count are required for this action.")
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
            
            elapsed = time.time() - t_start
            success_msg = f"Directories generated in {elapsed:.3f} seconds."
            self.show_status(success_msg, "success")
            messagebox.showinfo("Success", f"{success_msg}\nProject Path: {project_root}")
            
            # Auto update search with new folders
            self.update_search()
        except Exception as e:
            self.show_status(f"Structure generation failed: {e}", "error")
            messagebox.showerror("Processing Failure", f"An unexpected error occurred during directory generation:\n{e}")

    def update_search(self, event=None):
        query = self.ent_search.get().strip().lower()
        root_dir = self.ent_path.get().strip()
        self.listbox.delete(0, tk.END)
        
        if not root_dir or not os.path.exists(root_dir):
            return
            
        query_words = query.split()
        match_count = 0
        
        # Scan folder trees cleanly
        for root, dirs, files in os.walk(root_dir):
            for d in dirs:
                full_path = os.path.join(root, d)
                normalized_path = full_path.lower()
                
                # Check if all search query keywords reside inside the discovered subpath string
                if not query_words or all(word in normalized_path for word in query_words):
                    self.listbox.insert(tk.END, full_path)
                    match_count += 1
                    if match_count >= 150:  # Cap UI display elements to prevent lagging
                        return

    def open_selected_search_path(self, event=None):
        selection = self.listbox.get(tk.ACTIVE)
        if selection and os.path.exists(selection):
            open_in_file_manager(selection)
            self.show_status(f"Opened folder: {Path(selection).name}", "success")

    def handle_global_rename_trigger(self):
        """Cross-Thread safe execution launcher for double-shift event."""
        self.after(0, self.execute_rename_popup_flow)

    def execute_rename_popup_flow(self):
        paths = get_selected_file_paths()
        if not paths:
            self.show_status("Hotkey triggered, but no file selected in file manager.", "warning")
            return
            
        target_file = paths[0]
        context = parse_context_from_path(target_file)
        
        if context:
            RenameOverlay(self, context)
        else:
            self.show_status("Selected file is not in a valid Character Exports folder structure.", "error")
            # Log full context error to status bar
            self.show_status(f"Path Error: '{Path(target_file).name}' is outside Character Exports.", "error")

    def show_status(self, message, msg_type="info"):
        colors = {
            "success": "#00adb5",
            "warning": "#ffb86c",
            "error": "#ff5555",
            "info": "#8e8e93"
        }
        self.status_bar.configure(text=f"  {message}", text_color=colors.get(msg_type, "#8e8e93"))

# ==========================================
# 5. INITIALIZATION ENTRY POINT
# ==========================================

if __name__ == "__main__":
    app = SyncFlowApp()
    app.mainloop()
