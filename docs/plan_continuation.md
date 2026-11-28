# Continuation Plan

## Live MR examination and completeness

**Status as of 2026-11-28:** Startup reset, protocol identification, live
sequence rows, completion verdicts, and study replacement are implemented.
The desktop walkthrough, archive replays, and replacement race checks passed.
DICOM C-STORE loopback verification is blocked by Windows 10013 in the test
environment. See the [implementation log](implementation_log.md) for results.

### 1. Start with an empty examination

- Clear the previous session's managed DICOM objects, analysis jobs, and study
  results before accepting data. Starting the desktop app through the launcher
  creates a new session, including after closing and reopening the main window.
  Opening the sender uses the existing session.
- Show **Waiting for first data from MR** with an empty sequence table.

### 2. Identify the protocol and show the plan

- Match MR headers against examination-identification criteria in the configured
  clinical YAML files. Use `prostate_pirads_v2_1.yaml` for prostate examinations.
  If the first image is a generic localizer or the match is ambiguous, show
  **Identifying protocol** and retry as more images arrive.
- Pin the selected YAML version to the examination. Populate gray rows in YAML
  order for required, alternative, and conditional sequences. Evaluate images
  already received for this examination.
- Update rows to **OK**, **Warning**, or **Error** from the existing clinical
  checks. Keep unreceived and unverified rows gray while the study is open.
  Show confirmed results for other series while an independent series processes.

### 3. Verify examination completeness

- Add an examination-level completion verdict alongside the per-sequence
  quality statuses. Use the current validation result and its pinned YAML to
  list outstanding requirements during acquisition and refresh the list as
  more data are analyzed.
- Account for required sequences, minimum group counts, and fallback rules.
  For the PI-RADS reference profile, show the remaining T2W plane count
  and outstanding ADC, high-b DWI and DCE requirements. When a configured fallback satisfies
  a requirement, name the fallback and retain its warning. Optional sequences
  may be absent without blocking completion.
- Reuse **Close examination** to confirm acquisition has ended. Finalize
  against the latest study revision after all received MR images are processed.
  Preserve the transactional guard against new data arriving during closure.
- List confirmed missing sequences by YAML display name. For an unmet group
  requirement, show the shortfall and eligible sequences. Distinguish missing
  data, partial acquisitions, ambiguous matches, and failed checks. Use the
  YAML and validation logic to determine which findings prevent completion.

| Completion state | When it applies |
|---|---|
| **In progress** | Acquisition is open. Show outstanding sequences and checks, or indicate that the examination is ready to close. |
| **DONE** | Finalized against current results and all protocol completion requirements are met. Show any accepted warnings alongside the verdict. |
| **INCOMPLETE** | Finalized with a confirmed missing sequence or other confirmed unmet completion requirement. Show exactly what is missing or unmet. |
| **Verification pending** | Current results cannot establish completion. Show the analysis or identification issue that needs resolution. |

### 4. Replace or reopen the examination as data arrive

- When the first valid MR image with a new `StudyInstanceUID` is accepted,
  retire the previous study's local data, queued analysis, and results.
  Clear its sequence plan, completion verdict, and open sequence details.
- A uniquely identified different protocol also starts a new measurement
  when the scanner reuses a current or previously seen study UID. Skip
  late images from retired UIDs when they do not establish a protocol change.
  Ignore analysis results from replaced measurements.
  Identify the new protocol and rebuild all rows, even if both examinations
  use the same YAML.
- If new images arrive for the same finalized study, invalidate its completion
  verdict and return to **In progress** until it is evaluated and closed again.

### 5. Verify the full workflow

- Exercise receiver, worker, and GUI together. Cover startup with data from
  a previous session, an inconclusive localizer followed by a recognizable
  prostate image, and gray rows progressing to each quality outcome.
- Verify **DONE**, a named missing required sequence, an absent optional
  sequence, an unmet T2W group count, a valid RESOLVE fallback, ambiguous
  identification, a partial series, and failed or unfinished analysis.
- Verify that late images invalidate the same study's verdict and that a new
  study arriving during analysis or closure inherits no rows or verdict.
  Replay a complete local prostate dataset and a copy with a required
  sequence omitted. Log expected and observed outcomes and test results.

---

## Prototype 2 integration notes

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
The launcher accepts a `$Protocol` parameter with `prostate_mou.yaml` as its default:

```powershell
param(
    ...
    [string]$Protocol = 'config/protocols/prostate_mou.yaml'
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
