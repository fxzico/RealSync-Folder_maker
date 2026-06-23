# SyncFlow Automator

A lightweight, cross-platform desktop utility built with a native Python Tkinter GUI to automate deep-nested directory structures and run global shortcut-based renaming actions for post-production syncing pipelines.

## 🚀 Core Functionalities

* **Mode A [All Structure]:** Instantly Deploys micro studio pipelines (`/Projects`, `/Media`, `/Exports`, etc.) in the target directory.
* **Mode C [Character Exports]:** Generates zero-padded sequence folders (`Scene_01`, `Scene_02`) dynamically across custom character arrays, creating target format subfolders (`MP4`, `Quicktime`, `SyncSO`, `Lipdub`, `Audio`).
* **Explorer Selected File Renaming:** Highlight any asset inside Windows Explorer or Mac Finder and click the **⚡ Rename Selected Explorer File Now** button in the app to instantly open a sleek, context-aware prompt to enter the **Shot Number** (bypassing laggy background global keyboard hooks).
* **Integrated Jump Search:** Live, recursive search filter allowing post-production editors to navigate directly into deeply nested character paths instantaneously.

## 📸 Interface Walkthrough

### Main Configuration Grid
![Main GUI Workspace](screenshots/main_interface.png)

### Selected File Context Overlay Prompt
![Context Renamer Dialog](screenshots/overlay_popup.png)

## 🛠️ Compilation Blueprint

### Windows (.exe)
To compile the script on Windows, run:
```bash
pip install pyinstaller
pyinstaller --noconsole --onefile --name="SyncFlow_Automator" main.py
```
The standalone binary will generate inside the local `/dist/` workspace folder as `SyncFlow_Automator.exe`.

### macOS (.app / .dmg)
To compile a native macOS application bundle and package it into a mounting disk image (`.dmg`), run on a Mac machine:
```bash
# 1. Compile the script into a native macOS App bundle
pip install pyinstaller
pyinstaller --noconsole --onefile --name="SyncFlow_Automator" main.py

# 2. Package the compiled app directly into a mounting installer (.dmg)
hdiutil create -format UDZO -srcfolder dist/SyncFlow_Automator.app dist/SyncFlow_Automator.dmg
```
The standalone files will generate inside the `/dist/` workspace folder.

> [!TIP]
> **macOS Running Option (Quick Handover):** If a compiled `.dmg` installer is not attached to the release, Mac editors can run the utility natively from source (no external dependencies required). Simply execute:
> ```bash
> python main.py
> ```

