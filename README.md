# SyncFlow Automator

A lightweight, cross-platform automation utility designed for post-production editing pipelines utilizing **Adapt Real Sync**. It eliminates manual directory creation bottlenecks and error-prone file renaming loops.

## 🚀 Key Features

* **Mode A [All Structure]:** Instantly deploys macro studio pipelines (`/Projects`, `/Media`, `/Exports`, etc.).
* **Mode C [Character Exports]:** Generates zero-padded sequence folders (`Scene_01`, `Scene_02`) across multiple characters, automatically appending format subfolders (`MP4`, `Quicktime`, `SyncSO`, `Lipdub`, `Audio`).
* **Double-Shift Global Renaming:** Tap `Shift` twice on any highlighted file inside Windows Explorer or Mac Finder to open a smart, context-aware overlay prompt to enter the `Shot Number`.
* **Integrated Search:** Search-as-you-type directory scanner to jump instantly into deep nested project paths.

## 📸 Screenshots

### Main Interface
![Main GUI Canvas](screenshots/main_interface.png)

### Double-Shift Active Context Overlay
![Context Renamer Pop-up](screenshots/overlay_popup.png)

## 🛠️ How to Compile Natively

Ensure you have Python installed, then run:
```bash
pip install pyinstaller pynput customtkinter
python -m PyInstaller --noconsole --onefile --name="SyncFlow_Automator" main.py
```
Find your compiled, zero-dependency executable inside the `/dist/` folder!
