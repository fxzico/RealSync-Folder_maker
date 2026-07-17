# SyncFlow Automator

A lightweight, **100% offline** desktop utility for video post-production teams. One app, two jobs:

1. **Folder pipelines** — instantly deploy deep character/scene/format directory structures and rename export files based on their folder context.
2. **Translation QC** *(new in v1.2)* — check whether an OST sheet's translations are actually translated (not just copied English), and get back a colour-coded Excel review copy. Flag-only: your files are never modified.

No internet connection is used or required. Ever.

## 📥 Install (for editors — no technical setup)

1. Go to the [**Releases page**](../../releases/latest).
2. Download **`SyncFlow_Automator.exe`**.
3. Double-click it. Done — no installer, no Python, no dependencies.

> Optional: put a `qc_allowlist.txt` next to the exe to extend the list of brand names allowed to stay identical in translations (one per line).

## 🚀 Features

### Folders tab
* **Mode A [All Structure]** — deploys the full project ecosystem (`/Projects`, `/Media`, `/Exports`, …).
* **Mode B [Exports Only]** — delivery directories only.
* **Mode C [Character Exports]** — zero-padded `Scene_01…N` folders per character, each with `MP4 / Quicktime / SyncSO / Lipdub / Audio`.
* **⚡ Context rename** — select an export file in Explorer/Finder, click once, type the shot number: the file is renamed to spec from its own path. One-step **Undo** included.
* **Quick search** — instant, background-threaded filter over the whole project tree.

### Translation QC tab (new)
* Pick an OST sheet — **`.csv` or `.xlsx`** (Deepdub-style exports supported; CSV encoding auto-detected, cp1252 included; for Excel the first worksheet with the OST columns is used automatically).
* Choose the language pair — **English ⇄ Latin-American Spanish** ships now, both directions (⇄ swap button). More languages are config entries, not code.
* Press **Run** → get a colour-coded **Excel copy** next to your sheet:
  * 🔴 **Red** — translation missing, or English left untranslated
  * 🟡 **Yellow** — verify (identical brand names, partial overlaps)
  * 🟢 **Green** — properly translated
  * 🔵 **Blue** — the on-screen text is *already* in the target language (burned-in video text), shown as a linked source+translation pair
* **Flag-only guarantee:** the source sheet is read-only — byte-identical after every run.
* Scales to ~50k rows without freezing (background thread + memoized verdicts).

## 🖥️ Run from source

```bash
pip install -r requirements.txt   # just openpyxl, for the Excel export
python main.py
```

Headless engine (no GUI) for pipelines:

```bash
python translation_qc.py "sheet.csv"  --source en --target es
python translation_qc.py "sheet.xlsx" --source es --target en   # Excel in, both directions
```

## 🧪 Tests

```bash
python test_syncflow.py         # folder/rename logic  (11 checks)
python test_translation_qc.py   # QC engine golden set (synthetic data only)
```

## 🛠️ Build the exe yourself

```bash
pip install pyinstaller openpyxl
pyinstaller --noconsole --onefile --name="SyncFlow_Automator" main.py
# -> dist/SyncFlow_Automator.exe
```

macOS: same command produces `SyncFlow_Automator.app`; package with
`hdiutil create -format UDZO -srcfolder dist/SyncFlow_Automator.app dist/SyncFlow_Automator.dmg`,
or simply run `python main.py` from source (Translation QC export needs `pip install openpyxl`).

## 📁 Repo layout

| File | Purpose |
|---|---|
| `main.py` | The desktop app (Folders + Translation QC tabs) |
| `translation_qc.py` | Offline QC engine + CLI (importable, zero networking) |
| `qc_allowlist.txt` | Brand/proper-noun terms allowed to stay identical |
| `test_syncflow.py` / `test_translation_qc.py` | Headless test suites |
| `build_deck.py` | Regenerates the product pitch deck |

*Privacy note: client OST sheets and generated QC workbooks are gitignored — no client content lives in this repository; tests use synthetic data only.*
