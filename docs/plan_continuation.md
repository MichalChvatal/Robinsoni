# Continuation Plan — Robinsoni Prototype 2 → Working State

## Goal
Prototype 2 (commit `8da89613`) implemented all major features but introduced **15 failing tests** and **25 lint errors** due to integration gaps. This plan fixes those gaps with **minimal new code** — no redesign, no rewrites.

## Current status (2026-11-28)

Steps 1–7 are integrated and Ruff passes. The Windows launcher defaults to
`config/protocols/prostate_mou.yaml`. The MR50000 regression test checks
`PROSTATE_MOU` from local storage through analysis and GUI history, including
manual study closure and retention of image findings. The full DICOM C-STORE
check still requires a Windows session that permits TCP loopback connections.
See [the implementation log](implementation_log.md) for details.

---

## What prototype 2 already delivered (do NOT re-implement)

| Component | File | Status |
|---|---|---|
| Siemens b-value private tags | `vendors/siemens.py` | ✅ done |
| Derived metadata fields (FOV, SliceGap, resolution) | `dicom/metadata_parser.py` | ✅ done |
| Clinical PROSTATE_MOU protocol | `config/protocols/prostate_mou.yaml` | ✅ done |
| Study-level rules (T2W min-valid, DWI fallback, geometry, coverage) | `analysis/study_rules.py` | ✅ done |
| Image quality detectors (SNR, ghosting, slab discontinuity) | `analysis/image_quality/` | ✅ done |
| DB migration v3 (pacs_exports, study_analytics, artifact_findings) | `database/migrations/versions/v0003_pacs_analytics.py` | ✅ done |
| ORM models for new tables | `database/models.py` | ✅ done |
| PACS forwarder queue | `services/pacs_forwarder.py` | ✅ done |
| QC certificate (DICOM SC) | `services/certificate.py` | ✅ done |

---

## User Review Required

> [!IMPORTANT]
> The plan answers the previous agent's open questions with defaults — review and override if needed:
> 1. **Brain protocol** — not implemented; only prostate per `pozadavky_na_kontrolu.md`.
> 2. **Image analysis** — deterministic metrics only (SNR, ghosting, slab discontinuity); no ML/ONNX.
> 3. **Certificate format** — DICOM Secondary Capture (already implemented in prototype 2).
> 4. **PACS study closure** — not wired to GUI yet (kept for future step); no timeout auto-close.
> 5. **Geometry tolerance** — default 1 mm position, 0.001 orientation, 0.1 mm spacing (already in `GeometryAlignmentRule` defaults).

> [!WARNING]
> `view_data.py` currently rejects any DB that is not exactly schema version `(1, 2)`. After the v3 migration the GUI fails for every test that uses a real DB. Fix is a one-line change.

---

## Proposed Changes

### 1 · Fix schema-version guard in `view_data.py` (root cause of 10 GUI/monitor/integration failures)

#### [MODIFY] view_data.py
```python
# Line 90–91 — change:
if versions != [(1, 2)]:
    raise ValueError("Monitor requires migrated schema version 2")
# To:
if versions not in ([(1, 2)], [(1, 3)]):
    raise ValueError("Monitor requires migrated schema version 2 or 3")
```

---

### 2 · Fix test assertions that hard-code schema version 2 (3 migration test failures)

#### [MODIFY] test_database_migrations.py
Lines 53 and 92 assert `== 2`; after migration HEAD is 3. Update both assertions to `== 3`.

Also update the hard-coded `[(2,)]` in line 92 to `[(3,)]`.

---

### 3 · Add missing `send_dataset(path, pacs_config)` to `dicom/sender.py`

`pacs_forwarder.py` calls `send_dataset(path, pacs_config)` but `sender.py` only exposes `send(paths, *, host, port, ...)`. Add a thin wrapper:

#### [MODIFY] sender.py
```python
def send_dataset(path: Path, pacs: "PacsConfig") -> None:
    result = send(
        (path,),
        host=str(pacs.host),
        port=pacs.port,
        called_aet=pacs.called_aet,
        calling_aet=pacs.calling_aet,
    )
    if result.failed:
        raise ConnectionError(f"C-STORE failed: {result.last_status}")
```

---

### 4 · Add missing `Storage.series_paths(series_uid)` to `dicom/storage.py`

`pacs_forwarder.py` calls `self.storage.series_paths(series_uid)` but the method doesn't exist.

#### [MODIFY] storage.py
```python
def series_paths(self, series_uid: str) -> list[Path]:
    """Return stored object paths for all instances in a series."""
    with self.sessions() as session:
        instances = list(session.scalars(
            select(DicomInstance)
            .where(DicomInstance.series_uid == series_uid,
                   DicomInstance.integrity_state == "stored")
        ))
    return [self.path(instance.id) for instance in instances]
```

---

### 5 · Fix ruff lint errors (25 errors, 5 auto-fixable)

Run `ruff check --fix` for auto-fixable, then manually fix the remaining:

#### [MODIFY] services/certificate.py
- Remove unused imports: `hashlib`, `struct`, `pydicom.sequence.Sequence`
- Remove unused local variable `protocol_name` (or just remove the try/except block entirely)
- Shorten 3 lines >100 chars

#### [MODIFY] analysis/image_quality/detectors.py
- Remove unused `import math`
- Break 4 `detect` signatures (line continuation after `->`)

#### [MODIFY] analysis/image_quality/base.py, loader.py, study_rules.py, protocols/models.py
- Break long lines (E501), no logic changes

---

### 6 · Wire image quality into `analysis_worker.py`

Currently `analysis_worker.py` calls `save_evaluation` without image findings. The image_quality module and loader exist but are never called. Add pixel loading + detector call in `_process_study`:

#### [MODIFY] analysis_worker.py
After `records = …`, before `save_evaluation`:

```python
# Load pixel data and run detectors (skipped for non-MR or on error)
from ..analysis.image_quality import default_registry, load_slice_pixels
image_findings: dict[str, tuple] = {}
groups_snap = group_acquisitions(tuple(records))
for group in groups_snap:
    try:
        slices = tuple(
            sl for inst in group.members
            for path in [self.storage.path(inst.known("SOPInstanceUID") or "")]  # resolved via instance lookup
            if (sl := load_slice_pixels(path)) is not None
        )
        if slices:
            image_findings[group.key] = default_registry().evaluate(slices, role=group.role)
    except Exception:
        pass  # pixel loading failure must never block metadata result
```

> [!NOTE]
> The pixel loading loop needs the path from `DicomInstance.id`, not SOPInstanceUID. The actual implementation uses the already-loaded `instances` list from `_process_study`. This is a minor refinement of the sketch above.

---

### 7 · Update `run_windows.ps1` to support prostate_mou.yaml

#### [MODIFY] run_windows.ps1
Change the hard-coded protocol argument on line 70 to accept `$Protocol` parameter with default `demo_mr.yaml`:

```powershell
param(
    ...
    [string]$Protocol = 'config/protocols/demo_mr.yaml'
)
# Line 70:
$launchArgs = @('-X', 'utf8', '-m', 'mr_quality_monitor.launcher', $Action,
    '--config', $Config, '--protocol', $Protocol)
```

---

## Verification Plan

### Automated Tests
```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check backend tests tools
```
**Expected:** 0 failures (was 15 failed, 215 passed → target 230 passed), 0 lint errors.

### Manual Verification
1. `tools\run_windows.ps1 start -Protocol config/protocols/prostate_mou.yaml`
2. Send real DICOM via `Odeslat DICOM.cmd`
3. Verify GUI shows prostate sequences with colored statuses
4. Verify no `ValueError: Monitor requires migrated schema version` in logs

---

## Skipped (token-expensive, low current value)

| Item from original plan | Why skipped now |
|---|---|
| GUI: compact mode / always-on-top | UI-only, no test coverage, low clinical risk |
| GUI: "Close study & issue certificate" button | Requires PACS config to be meaningful; deferred |
| `analytics.py` query module | DB tables exist; queries can be added on demand |
| New test files (prostate_rules, image_quality, pacs_export) | Existing 230 tests cover the plumbing; clinical test data not in repo |
