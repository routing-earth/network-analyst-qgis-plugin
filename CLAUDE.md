# CLAUDE.md

Notes for future Claude sessions working in this repo. Keep terse and current — update when assumptions change.

## What this is

QGIS plugin for the [Valhalla routing engine](https://github.com/valhalla/valhalla). Provides routing, isochrones, matrix, map-match, elevation, expansion, and TSP — both as interactive map tools and Processing algorithms — for car/bike/pedestrian/truck/motorbike profiles. Talks to remote Valhalla HTTP servers (FOSSGIS public, custom URLs) or runs Valhalla locally via the [`pyvalhalla`](https://pypi.org/project/pyvalhalla/) Python package (Linux/macOS only — Windows is HTTP-only).

### Branding: "Valhalla" → "Network Analyst" (v6.0.0 rename)

The plugin's **display name** is **Network Analyst** (`metadata.txt` `name=`). Only the label changed — the **identity did not**:

- The plugin **folder / Python package** stays `valhalla/` (it's the plugins.qgis.org id AND the package that deliberately shadows pyvalhalla — never rename it).
- The **settings/data dir is hardcoded `<profile>/valhalla/`** (`core/settings.py:get_settings_dir`), NOT derived from the display name. It holds `settings.ini`, the default `graph_dir`, pyvalhalla, and `routing_earth_utils`. It was briefly (and wrongly) `network_analyst/` when first derived from `PLUGIN_NAME` in 6.0.0 — that orphaned pre-rename users' settings and moved `graph_dir` out from under them; pinning it to `valhalla` is the fix. A leftover `network_analyst/` dir from a 6.0.0 test build can be deleted (or its contents moved into `valhalla/`).
- The Processing **provider `id()` is hardcoded `"valhalla"`** (`processing/provider.py`), decoupled from the display name, so saved Processing models referencing `valhalla:route` etc. keep working. `provider.name()` follows the display name.
- `PLUGIN_NAME` (`__init__.py`) is derived from `metadata.txt` `name=`; all user-facing display strings (dock titles, canvas submenu, About title, toolbar/menu) reference it — a future rename is again just the metadata line. "Valhalla" the **engine** name legitimately stays in URLs, `valhalla.json`, config, and class names.
- On first startup after upgrading past 6.0.0 (or a fresh install), `gui/dlg_rebrand_notice.py` shows a one-shot "Valhalla is now Network Analyst" dialog (old + new logos inline), gated on a `last_seen_version` setting. Old logo preserved as `resources/icons/valhalla_old.svg`; `valhalla_logo.svg` is now the new routing.earth logo.

## Branches & QGIS/PyQt strategy

- **`master`**: development branch for QGIS 4.x / PyQt6. **This is where new work lives.**
- **`qgis-v3`**: maintenance branch for QGIS 3.x / PyQt5.
- (`qgis-v4` may exist transiently as a working branch before merging into master — treat `master` as canonical going forward.)
- `metadata.txt` on the v4 line declares `qgisMinimumVersion=4.0`, `qgisMaximumVersion=4.99`, `version=6.0.0` (the Network Analyst rename; see Branding above).
- The `qgis.PyQt.*` compatibility layer (provided by QGIS) abstracts most PyQt version differences, but **not all**: enum scoping (`Qt.LeftButton` vs `Qt.MouseButton.LeftButton`), `QFileSystemModel` location (QtWidgets in PyQt5 → QtGui in PyQt6), `QFileDialog` option flags, etc.

## Repository layout

```
valhalla/                       # plugin source root (this is what gets shipped)
├── __init__.py                 # classFactory(iface) entry point
├── plugin.py                   # ValhallaPlugin: initGui/unload, registers provider + dock
├── metadata.txt                # QGIS plugin manifest
├── global_definitions.py       # RouterType / RouterMethod / RouterProfile / RouterEndpoint enums
├── core/
│   ├── router_factory.py       # builds router instances
│   ├── http/router_client.py   # async HTTP via QgsNetworkAccessManager; sets X-Client-Id
│   ├── results_factory.py      # parses Valhalla responses into QGIS layers
│   ├── graph_registry.py       # the graph library API (over graph_dir) — see "Graphs" below
│   ├── pypi.py                 # interpreter/pip/PyPI handler — see "Dependencies" below
│   ├── routing_earth.py        # routing-earth.com client plumbing (subprocess/auth/HTTP)
│   └── settings.py             # ValhallaSettings (QSettings-backed)
├── gui/
│   ├── dock_routing.py         # RoutingDockWidget — main interactive UI
│   ├── widgets/                # router widget, waypoints, costing settings; unified graphs
│   │                           #   table (widget_graph_manager + graph_table_model +
│   │                           #   graph_ops_re/_local controllers)
│   ├── compiled/*_ui.py        # GENERATED from resources/ui/*.ui — do not hand-edit
│   └── dlg_*.py                # dialogs (settings, providers, server log, …)
├── processing/
│   ├── provider.py             # ValhallaProvider (Processing algorithms registry)
│   └── routing/, spatial_optimization/, …
├── resources/
│   ├── ui/*.ui                 # Qt Designer XML — source of truth for compiled UIs
│   └── icons/
├── utils/                      # geom, http, layer, logger, qt, resource helpers
└── third_party/routingpy/      # vendored routing client lib (do not modify directly)

tests/
├── __init__.py                 # LocalhostDockerTestCase / LocalhostPluginTestCase base classes
├── qgis_interface.py           # mock QgisInterface for headless testing
├── test_localhost_docker/      # tests against external Valhalla on localhost:8002
├── test_localhost_plugin/      # tests using bundled pyvalhalla
└── scripts/qgis_test_setup.sh  # CI bootstrapping inside QGIS docker images

scripts/
├── compile_ui.sh               # pyuic6 over resources/ui/*.ui → gui/compiled/*_ui.py
└── pyqt5_to_pyqt6.py           # QGIS official 3to4.py migration script (one-shot use)

.github/workflows/
├── ci-tests.yml                # QGIS 4.x release tests (matrix: qgis_tags: [4.0.1])
├── ci-tests-v3.yml             # QGIS 3.44.10 tests — slated for removal when v3 dropped
└── ci-tests-latest.yml         # QGIS master nightly
```

## How the plugin loads

1. QGIS calls `valhalla/__init__.py:classFactory(iface)` → returns `ValhallaPlugin(iface)`.
2. `ValhallaPlugin.__init__` instantiates `ValhallaProvider`, which constructs every Processing algorithm class (this is where import-time errors surface — see traceback chain in `processing/provider.py:62`).
3. `ValhallaPlugin.initGui()` registers the Processing provider, builds the toolbar, and creates the `RoutingDockWidget`.

## UI compilation

`.ui` files (Qt Designer XML) live under `valhalla/resources/ui/`. They're compiled into Python with `pyuic6` via `scripts/compile_ui.sh`, output to `valhalla/gui/compiled/*_ui.py`.

- **Never hand-edit** `valhalla/gui/compiled/*_ui.py` — they're regenerated.
- After editing any `.ui`, re-run `bash scripts/compile_ui.sh`.
- Older `.ui` files may use unscoped Qt6 enum syntax (e.g. `QFileDialog::DontResolveSymlinks`); pyuic6 won't fix this for you. Update the `.ui` to use scoped form (`QFileDialog::Option::DontResolveSymlinks`) and recompile.

## Test setup

Two flavors, intentionally exclusive (one or the other, never both):

```shell
# Docker-backed: needs `docker run -p 8002:8002 ghcr.io/valhalla/valhalla-scripted:latest`
# pyvalhalla MUST be uninstalled (rm -r ~/.local/share/QGIS/QGIS4/profiles/default/valhalla/pyvalhalla)
QT_QPA_PLATFORM=offscreen python -m coverage run --append -m unittest discover -s tests/test_localhost_docker -t .

# Plugin-managed: stops docker, downloads & installs pyvalhalla itself
QT_QPA_PLATFORM=offscreen python -m coverage run --append -m unittest discover -s tests/test_localhost_plugin -t .
```

CI runs both inside QGIS docker images; see `.github/workflows/ci-tests.yml`.

## Graphs: unified table over one self-contained graph library (settings dialog "Graphs")

Local graphs and routing-earth.com packages live in ONE table. **`graph_dir` is the single
source of truth** (user-relocatable via the folder button; default `<profile>/valhalla/graph_dir`).
A graph is a self-contained subdir `<graph_dir>/<name>/` holding `id.json` (the valhalla
config overlay, ABSOLUTE paths *inside that dir*) plus its own data (tar/tiles) — **nothing is
tracked outside the subdir**. A routing.earth package is just an `id.json` that also carries an
optional **`routing_earth` block** (`scope`/`cadence` + sync bookkeeping); its presence marks
the entry RE, its absence means plain local. No registry/metadata split, no sidecar, no
migration — an old master `id.json` (no block) loads as a local graph unchanged. Changing
`graph_dir` only re-scans; it never moves graphs (old ones reappear if you point back).

This is the master storage model restored; the earlier registry-split + `re_sync.json` +
`migrate_legacy_graph_dir` design (2026-07..08) was reverted as needless complexity.

Module split (deliberately not one file):

- `core/graph_registry.py` — `GraphEntry` + library API (`graph_dir`, `list_names`,
  `discover`, `register(replace=False)` raises on name collision, `unregister` (deletes the
  whole subdir — data too), `set_re_state`/`mark_synced` write into the `routing_earth`
  block, `local_graph_config`/`re_graph_config` builders, `read_tar_state` tar-member reader,
  timestamp humanizing). No UI, no Qt widgets.
- `core/routing_earth.py` — RE plumbing only: CLI args/env, auth-db API key,
  entitlements HTTP call. Imports nothing from gui; graph_registry never imports it.
- `gui/widgets/graph_table_model.py` — `GraphTableModel(QAbstractTableModel)`: entries +
  grayed "available" entitlement rows in one model; columns Region | Cadence | OSM age |
  Last diff (size of the last downloaded diff/seed, parsed from CLI output) | Synced | Action; red = RE entry whose tar is missing/not managed.
- `gui/widgets/graph_ops_re.py` — `RoutingEarthController` (the `re` QProcess: init/adopt/
  sync/status-queue). `init_package(Entitlement)` = sized confirm then seed (no dialog);
  wildcard `*` first prompts for the region via `QInputDialog`.
  `gui/widgets/graph_ops_local.py` — `LocalGraphController` (chained
  `valhalla_build_admins`/`_tiles` PBF build into `<graph_dir>/<name>/`, **From Tar = move**
  into the library, tile_url add). Controllers get injected callables (log/status_bar/
  refresh/confirm_replace), no parent back-pointers.
- `gui/widgets/widget_graph_manager.py` — chrome + wiring only: API key/URL row, toolbar
  (add-menu, remove, **graph_dir folder picker**, valhalla-config button →
  `ConfigEditorDialog`), QTableView, collapsible log splitter (state key `re_splitter_state`),
  one QFileSystemWatcher on `graph_dir()`. Action buttons via `setIndexWidget`, rebuilt on
  every `modelReset` — closures capture the entry NAME and re-resolve at click time (watcher
  resets would stale them).

Behavior notes:

- **Add-menu**: `Update from routing.earth` (default action), then `addSection("Advanced")`
  with `From Tar` (**moves** the tar into `<graph_dir>/<stem>/`; a *managed* RE tar is refused
  here with a redirect to the account flow), `From URL`, `From PBF` (both build/cache into
  `<graph_dir>/<name>/`, no external picker). NB the section is a separator-action: never index
  `menu.actions()` positionally (tests find actions by text).
- **Duplicates**: every add path funnels through one confirm-replace prompt
  (`_confirm_replace`); `register()` raises `FileExistsError` unless `replace=True`. A replace
  `unregister`s first (fresh subdir, no stale data mixed in).
- **Remove**: `unregister` deletes the whole `<graph_dir>/<name>/` subdir (metadata AND data)
  after one confirm — no separate "delete data" checkbox (data is inside).
- **RouterWidget** (dock): combo from `list_names()`, watcher on `graph_dir()`, selection
  loads `<graph_dir>/<name>/id.json`, **pops `routing_earth`** (plugin metadata, not valhalla
  config), then deep-merges the rest into `valhalla.json`.
- **CRITICAL — in-process import is impossible:** this plugin's package is named `valhalla`
  and shadows pyvalhalla in `sys.modules`, so `routing_earth_utils` (imports
  `valhalla.baldr`) can never load inside QGIS. All RE ops run the CLI as a **QProcess
  subprocess** (`python3 -m routing_earth_utils.cli`, cwd = settings dir to dodge the same
  shadowing via cwd). **Parse contracts** with routing-earth-utils `cli.py`/`client.py`:
  `re status` prints one-line JSON on stdout and exits 2 when behind (git-diff style);
  `sync`/`init` output carries the `osm data <ts>` token (regex-parsed). Same trap applies
  to ANY future pyvalhalla-importing code.
- **Update from routing.earth** = ① `GET /api/v1/entitlements` directly via
  QgsNetworkAccessManager (Bearer key; a 401 pops QGIS's credentials dialog), ② `re status`
  per package. The endpoint returns `Entitlement`s enriched with the newest full snapshot's
  `compressed_size_bytes`/`size_bytes`/`dataset_id`/`osm_data_timestamp` (all nullable,
  snake_case). Non-local pairs render as grayed available rows showing **download size** (in
  the Last-diff column) + OSM age, with a download button → sized confirm → seed into
  `<graph_dir>/<scope>_<cadence>/`. Wildcard `*` shows as-is (no size; prompts for a region on
  download). Available rows are in-memory only.
- **Install (like pyvalhalla):** the plugin installs `routing-earth-utils` itself from the
  deps table (`dlg_plugin_settings` → `core/pypi.install`). Unlike pyvalhalla (a self-contained
  wheel that's just unzipped) re-utils has deps, so it's `pip install --target
  <profile>/routing_earth_utils`: re-utils `--no-deps` (reuse the unpacked pyvalhalla) +
  `cryptography` (+ `backports.zstd` on py<3.14). **On real PyPI** (`PyPiPkg.json_url` →
  pypi.org's JSON API for the version check); the install needs no custom index args — plain
  `pip install --no-deps routing-earth-utils`. The subprocess runs under `pypi.python_exe()`
  with `re_utils_root_dir()` + pyvalhalla dir prepended to PYTHONPATH (`re_process_env`,
  `os.pathsep`-joined — Windows-safe). The old `re_python` setting is gone.
- **Auth:** API key in the QGIS auth database (`APIHeader` config, id in `re_authcfg`);
  passed via env `ROUTING_EARTH_API_KEY`, never argv/QSettings. API origin override:
  `re_api_url` (default https://routing-earth.com).
- **Protection:** non-managed tars (no `.routing-earth.json` member behind `index.bin`,
  read with stdlib tarfile) are rejected on adopt and flagged red with sync disabled.
  dataset_id IS the build timestamp (epoch), shown in the Last-diff cell tooltip.

## Dependencies: the interpreter/pip/PyPI handler (`core/pypi.py`)

Everything Python-interpreter, pip and PyPI lives in **one** module. Nothing else in the plugin
may spawn a python or call pip — `PYTHON_EXE` (the old, macOS-broken constant in
`global_definitions.py`) and `resource_utils.exec_cmd` are gone. Two invariants:

1. **Nothing raw escapes.** Every public function returns a value (`None` = merely unknown, e.g.
   PyPI unreachable) or raises `PyPiError` (`exceptions.py`), whose `str()` fits a message bar
   and whose `.detail` goes to the log panel. GUI call sites additionally catch bare `Exception`
   (`dlg_plugin_settings._log_failure`) — a dependency problem must never pop a traceback dialog.
2. **One interpreter for everything**: `python_exe()` installs *and* runs the `re` CLI, so wheels
   always match the ABI of the process that loads them. Ask `python_version()` — never
   `sys.version_info` — about that environment (that's what picks `backports.zstd`).

- **Interpreter resolution** (`_get_python_candidates`, memoized, validated by actually running
  each candidate): `sys.executable` when it's really a python, then
  `sys.base_prefix/bin/python{X.Y}`, then sysconfig `BINDIR`, then `$PATH`. **`sys.executable`
  is platform-dependent inside QGIS Desktop** (confirmed in the QGIS python console): on
  **linux it IS the system python**, so tier 1 hits and the rest never runs; in the **macOS
  bundle and on windows it's the QGIS binary**, which is what the fallback chain exists for.
  Don't use `sys._base_executable` — it only diverges from `sys.executable` inside a venv,
  where the latter is already a python and wins anyway; in an embedded host getpath just
  copies `executable` into it. Within the fallbacks, `base_prefix` beats `BINDIR` because
  conda-forge builds (the macOS QGIS bundle) bake the *build* prefix into `BINDIR`; it beats
  `$PATH` because `$PATH` may hold an unrelated venv.
- **What the interpreter must satisfy — deliberately very little.** Only: (a) it can run pip (or
  bootstrap it from an ensurepip wheel), and (b) it's **>= 3.12**. It does **not** need the qgis
  bindings, because nothing we install imports `qgis` — the subprocess only ever runs pip and
  `routing_earth_utils.cli` (pyvalhalla + cryptography). So **don't "validate" a candidate with
  `import qgis`**: it costs ~520 ms vs ~12 ms for the version probe (times N candidates, on the
  startup path), and on the macOS bundle / OSGeo4W a bare subprocess lacks the `PYTHONPATH`/
  `PYTHONHOME` that makes `qgis` importable at all — it would reject the *correct* bundled python
  on exactly the platforms where the fallback tiers are the only thing running.
- **Why the 3.12 floor, and why interpreter choice is loose** (wheel tags checked 2026-09-10):
  pyvalhalla and routing-earth-utils both ship **`cp312-abi3`** wheels (cryptography is
  `cp311-abi3`, so not the binding constraint). abi3 means a wheel installed under one >= 3.12
  python loads under any other, which is what makes "some interpreter" good enough — and why we
  install into `--target` dirs rather than a specific site-packages. **`PY_FLOOR = (3, 12)` is
  enforced in `_resolve_python`**: a candidate that runs but is older is *skipped, not accepted*,
  so resolution keeps walking down the tiers instead of dead-ending on it, and if everything is
  too old the `PyPiError` names the versions it found and says a newer python needn't be the one
  QGIS runs on. `_get_python_candidates` also searches `$PATH` for explicitly versioned
  `python3.{20..12}` (newest first), which is the only way out on a host whose QGIS python is
  below the floor. **That host is real: QGIS 3.x's macOS bundle ships Python 3.9.5**
  (`config/ltr.conf` in qgis/QGIS-Mac-Packager, Qt 5.15.2) — so on `qgis-v3` + macOS the deps are
  installable *only* via a separately installed python. Windows/OSGeo4W is fine but has zero
  headroom: `python3-core` is **3.12.14** (alongside `qgis-ltr` 3.44.14, `qgis` 4.2.2).
  **The exception is `backports.zstd`** (installed only on py < 3.14, where zstd isn't stdlib):
  it is **not** abi3, it ships per-version `cp3XX-cp3XX` wheels. So on 3.12/3.13 the install is
  locked to whichever interpreter ran pip — which is what makes invariant 2 (one interpreter for
  install *and* run) load-bearing rather than merely tidy. If our wheels ever stop being abi3,
  this whole looseness goes away and the resolver has to match the host exactly.
- **pip is not a given.** `pip_argv()` tries `<exe> -m pip`, then runs pip straight out of
  ensurepip's wheel (`pip-*.whl/pip` — a wheel on sys.path *is* the package: no install, no
  network, no root, PEP-668-proof), then raises a `PyPiError` naming `python3-pip`/`python3-venv`.
  The wheel is globbed in **both** places ensurepip itself looks: sysconfig's `WHEEL_PKG_DIR`
  (Fedora/Debian relocate the wheel there — python3-pip-whl — and strip `ensurepip._bundled`)
  and then `<stdlib>/ensurepip/_bundled/`. That dir layout has been stable since ensurepip
  landed in 3.4 (only the pip version in the filename moves, hence the glob), and on
  macOS/Windows — stock CPython, no distro patching — `WHEEL_PKG_DIR` is empty and `_bundled`
  is always present. **Flatpak QGIS lives or dies on this fallback**: the KDE runtime has no
  pip in site-packages but a full `_bundled` (verified: py3.12, `base_prefix=/usr`, empty
  `WHEEL_PKG_DIR`), and `--target` writes to the profile under `~/.var/app/`, not read-only
  `/usr`. Deliberately no `pip.pyz` bootstrap download.
- `run(argv)` is the only subprocess entry point: argv **list** (the old string + `shlex.split(…,
  posix=False)` kept the quote chars on Windows), `shell=False`, `CREATE_NO_WINDOW` so Windows
  doesn't flash consoles, everything wrapped into `PyPiError`.
- Package data (`PyPiPkg`, `PyPiState`, `PYVALHALLA_PKG`, `RE_UTILS_PKG`, `PYPI_PKGS`) lives here
  too — **not** in `global_definitions.py`, which imports the whole GUI costing-widget tree and
  would drag it into every consumer.
- Import direction is one-way: `core/pypi.py` → `utils/resource_utils.py` (for
  `check_valhalla_installation`, since the pyvalhalla version is read off `valhalla_service
  --version` in whatever `get_binary_dir()` points at — deliberately, so a custom binary dir
  reports its own build). Never the reverse.

## Known PyQt6 gotchas (already hit during the v4 port)

These will keep biting — check first when something breaks after touching v4 code:

1. **Enum scoping.** PyQt6 requires fully scoped enum access. `Qt.LeftButton` → `Qt.MouseButton.LeftButton`; `QDialogButtonBox.Ok` → `QDialogButtonBox.StandardButton.Ok`; `QDir.Dirs` → `QDir.Filter.Dirs`. The compiled UI files were regenerated with `pyuic6` to handle this for generated code — but hand-written code (especially `tests/`) still needs manual fixes.
2. **Class relocations.** `QFileSystemModel` moved from `QtWidgets` to `QtGui`. `QAction` moved from `QtWidgets` to `QtGui`. `QRegExp` removed → use `QRegularExpression`.
3. **`QSortFilterProxyModel` + `QFileSystemModel` is brittle in Qt6.** `proxy.mapFromSource(idx)` walks the source index's parent chain; `QFileSystemModel` only fetches children of `setRootPath`, never the ancestor chain. Result: `mapFromSource` returns invalid even when `model.index(path)` is valid. Fix used in this repo: drop the proxy, use `QFileSystemWatcher` + explicit `iterdir()`-based models (see `widget_router.py` and `graph_table_model.py`).
4. **Stale `.pyc`.** When refactoring imports, clear `__pycache__/` — Python's mtime-based invalidation can lag and produce confusing tracebacks referring to old import statements.

## Coding conventions

- Black, line length 105. isort with black profile. `pyproject.toml` excludes `compiled/`, `third_party/`, and a few entry-point files.
- Tests under `tests/test_localhost_docker/test_processing/test_spatial_optimization/UNUSED_*.py` are deliberately skipped (filename prefix).
- Don't modify `valhalla/third_party/routingpy/` — it's vendored. The plugin overrides what it needs via subclassing in `valhalla/core/`.

---

# Dev workflow

## When working on `master` (QGIS v4) together

**Mandatory reminder to the user (the user explicitly asked for this):** they cannot test PyQt5/QGIS3 locally and rely on CI for the `qgis-v3` branch. So:

> **Whenever we change something on `master`, I should remind the user to also apply the equivalent change to `qgis-v3`.** This applies to bug fixes, new features, and behavior changes — not to PyQt6-specific syntax migrations (those are by definition v4-only). When unsure whether a change is PyQt6-specific or general, mention it explicitly so the user can decide.

How to phrase the reminder: at the end of a change session, in one sentence. Example: *"FYI — this change is logic, not PyQt6-specific. Worth porting to `qgis-v3` so CI doesn't drift."*

## Keeping this file current

After any significant change to the repo — new architectural decisions, new gotchas hit, branch-strategy shifts, conventions adopted, files moved — update CLAUDE.md in the same change. Stale notes are worse than no notes; if the reader can't trust this file, they'll re-derive everything anyway. Specifically:

- New PyQt6 gotcha → add to the "Known PyQt6 gotchas" section.
- New top-level directory or significant file move → update the layout tree.
- Change to test commands, CI, or branch model → update the corresponding section.
- Convention or workflow we agreed on in conversation → write it down here, not just in memory.

Don't pad with trivia (file-by-file changelogs, one-off bugs we already fixed). The bar is: *would a future agent waste time without this?*

## Quick commands

```shell
# Recompile UI after .ui edit
bash scripts/compile_ui.sh

# Clear stale bytecode (do this any time you refactor imports)
find tests valhalla -type d -name __pycache__ -exec rm -rf {} +

# Format / lint
black valhalla tests
isort valhalla tests
pre-commit run --all-files
```

## Backporting a change to `qgis-v3`

The two branches share most code. Typical flow when something needs to land on both:

```shell
git checkout qgis-v3
git cherry-pick <commit-on-master>
# resolve any PyQt6 vs PyQt5 enum/import conflicts manually
git push origin qgis-v3
```

CI on the v3 branch (via `ci-tests-v3.yml`) will validate. Don't promise the user it works on QGIS 3 without seeing the CI green tick.
