# routing-earth.com graph packages — plan & status

**Status 2026-08-24: storage reverted to the self-contained `graph_dir` model.** The
registry-split + `re_sync.json` sidecar + `migrate_legacy_graph_dir` design was scrapped as
needless complexity. A graph is now a self-contained subdir `<graph_dir>/<name>/` (id.json +
its data); routing.earth = an optional `routing_earth` block in id.json. See the "Graphs"
section in `CLAUDE.md` and `GRAPH_OPS_OVERVIEW.md` for the current shape. The historical notes
below describe the RE feature as first built — the subprocess/parse contracts still hold, but
the storage details (registry, sidecar, graphs_data/graphs_re dirs) no longer apply.

## As built

| Piece | File |
|---|---|
| Service layer: tar-state reader (stdlib tarfile), sidecar, package discovery/registration, CLI args + subprocess env, auth-db key store | `valhalla/core/routing_earth.py` |
| Group-box content: API key/URL row, package table, add/remove/status buttons, per-row sync, FS watcher, QProcess plumbing | `valhalla/gui/widgets/widget_routing_earth.py` |
| Settings: `re_authcfg`, `re_api_url` | `valhalla/core/settings.py` |
| Empty `ui_re_group` placeholder (content is code) | `resources/ui/dlg_plugin_settings.ui` |

- Table columns: **Region | Cadence | OSM age | Graph age | Synced | Action** (Graph age = dataset_id,
  which IS the build timestamp).
- Storage: tar anywhere on disk, never copied; registry = `<graph_dir>/<scope>_<cadence>/`
  with `id.json` (absolute `tile_extract` → shows up in local graphs list, routable) +
  `re_sync.json` sidecar.
- Protection: non-managed tars (no `.routing-earth.json` state member) are rejected on
  adopt and flagged red / sync-disabled in the table — also catches a tar swapped out
  after registration.
- Remove: confirm dialog with "also delete the extract tar" checkbox.
- Status button: `re status` per package sequentially; "behind" parsed into the sidecar →
  row tooltips.

## Deviation from the original plan

**Subprocess instead of QgsTask + logging bridge.** In-process
`import routing_earth_utils` is impossible inside QGIS: the plugin package is itself named
`valhalla` and shadows pyvalhalla in `sys.modules`. So every op runs
`python3 -m routing_earth_utils.cli` as a QProcess (mirroring the graph-build flow), stdout+stderr
(`-v` logs) streaming into the settings dialog's log panel. `routing-earth-utils` must be
installed in `python3`'s env (currently: global_venv); profile pyvalhalla is prepended to
the subprocess PYTHONPATH. API key via env, never argv.

## Later / follow-ups

- ~~OSM data column~~ done 2026-07-21: routing.earth-utils gained
  `osm_data_timestamp` on `SyncResult`/`Status` + an `osm data <ts>` token in CLI
  output/logs; the plugin parses it into the sidecar (status only stores it when the
  extract is current — the resolver reports the *latest* dataset's timestamp).
- ~~Add-dialog scope enumeration~~ done 2026-07-21, better: **refresh auto-detects
  available packages** — entitlements fetched directly over HTTP (QgsNetworkAccessManager +
  Bearer), non-local scope×cadence pairs shown as grayed rows with a download button
  (prefilled Init dialog); `re status` JSON (exit 2 = behind) fills OSM/build data and
  behind-tooltips for real.
- **Auto-update checkbox** per row (sync on plugin load).
- **Install routing-earth-utils from PyPI** via the plugin (like pyvalhalla) — then delete
  the `re_python` setting (marked TODO in `settings.py`; dev-only escape hatch because PATH
  inside QGIS doesn't see the venv).
- Rethink merging with the local-graphs table/logic.
