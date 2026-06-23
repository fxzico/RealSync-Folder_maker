# SyncFlow Automator

A lightweight, cross-platform desktop utility built with a native Python Tkinter GUI to automate deep-nested directory structures and run global shortcut-based renaming actions for post-production syncing pipelines.

## 🚀 Core Functionalities

* **Mode A [All Structure]:** Instantly Deploys micro studio pipelines (`/Projects`, `/Media`, `/Exports`, etc.) in the target directory.
* **Mode C [Character Exports]:** Generates zero-padded sequence folders (`Scene_01`, `Scene_02`) dynamically across custom character arrays, creating target format subfolders (`MP4`, `Quicktime`, `SyncSO`, `Lipdub`, `Audio`).
* **Double-Shift Global Renaming:** Tap the `Shift` key twice rapidly on any highlighted asset inside Windows Explorer or Mac Finder to call up a sleek, context-aware prompt layer to input the `Shot Number`.
* **Integrated Jump Search:** Live, recursive search filter allowing post-production editors to navigate directly into deeply nested character paths instantaneously.

## 📸 Interface Walkthrough

### Main Configuration Grid
![Main GUI Workspace](screenshots/main_interface.png)

### Double-Shift Active Context Overlay Prompt
![Context Renamer Dialog](screenshots/overlay_popup.png)

## 🛠️ Compilation Blueprint

To build a fresh, portable binary wrapper natively on your operating system, execute:
```bash
pip install pyinstaller pynput
pyinstaller --noconsole --onefile main.py
```
The standalone binary will generate inside the local /dist/ workspace folder.
