"""Deterministic MR artifact detectors (motion/ghosting, low SNR, slab discontinuity)."""

import statistics
from dataclasses import replace

from .base import ArtifactDetector, ArtifactFinding, DetectorRegistry, SlicePixels


def _subsample_grid(sl: SlicePixels, target_dim: int = 96) -> tuple[list[list[float]], int, int]:
    step_r = max(1, sl.rows // target_dim)
    step_c = max(1, sl.columns // target_dim)
    px = sl.pixels
    cols = sl.columns
    grid = []
    for r in range(0, sl.rows, step_r):
        base = r * cols
        grid.append([float(px[base + c]) for c in range(0, cols, step_c)])
    return grid, len(grid), len(grid[0]) if grid else 0


def _corner_stats(grid: list[list[float]], gh: int, gw: int) -> tuple[float, float]:
    bh = max(2, gh // 8)
    bw = max(2, gw // 8)
    corners = []
    for r_range in (range(0, bh), range(gh - bh, gh)):
        for c_range in (range(0, bw), range(gw - bw, gw)):
            for r in r_range:
                row = grid[r]
                for c in c_range:
                    corners.append(row[c])
    if not corners:
        return 0.0, 0.0
    mean = statistics.fmean(corners)
    std = statistics.pstdev(corners)
    return mean, std


def _foreground_values(grid: list[list[float]], threshold: float) -> list[float]:
    return [val for row in grid for val in row if val > threshold]


class LowSNRDetector(ArtifactDetector):
    """Detects degraded signal-to-noise ratio across slices."""

    detector_id = "low_snr"
    artifact_type = "low_snr"

    # Per-role (ok, warn) thresholds tuned for clinical Siemens compressed MR.
    _ROLE_THRESHOLDS: dict[str, tuple[float, float]] = {
        "t2w_sag": (4.0, 2.0),
        "t2w_cor": (4.0, 2.0),
        "t2w_tra": (4.0, 2.0),
        "dwi_low_b": (5.0, 3.0),
        "dwi_int_b": (5.0, 3.0),
        "dwi_adc": (4.0, 2.5),
        "dwi_high_b": (4.0, 2.5),
        "dwi_resolve": (5.0, 3.0),
        "localizer": (3.0, 1.5),
    }

    def __init__(self, *, min_snr_ok: float = 8.0, min_snr_warn: float = 5.0):
        self.min_snr_ok = min_snr_ok
        self.min_snr_warn = min_snr_warn

    def detect(  # noqa: D102
        self, slices: tuple[SlicePixels, ...], *, role: str | None = None
    ) -> ArtifactFinding:
        if not slices:
            return ArtifactFinding(
                self.detector_id,
                self.artifact_type,
                "GRAY",
                "no_pixel_data",
                None,
                {"min_snr_ok": self.min_snr_ok, "min_snr_warn": self.min_snr_warn},
            )
        # Apply per-role threshold if available, else fall back to global defaults.
        role_key = (role or "").lower()
        if role_key in self._ROLE_THRESHOLDS:
            ok_lim, warn_lim = self._ROLE_THRESHOLDS[role_key]
        else:
            ok_lim, warn_lim = self.min_snr_ok, self.min_snr_warn

        snrs: list[tuple[int, float]] = []
        for idx, sl in enumerate(slices):
            if sl.rows < 8 or sl.columns < 8:
                continue
            grid, gh, gw = _subsample_grid(sl)
            bg_mean, bg_std = _corner_stats(grid, gh, gw)
            fg = _foreground_values(grid, bg_mean + 3.0 * max(bg_std, 1e-3))
            if not fg:
                snrs.append((idx, 0.0))
                continue
            fg_mean = statistics.fmean(fg)
            # If corners are strictly noiseless (e.g. masked background std == 0), estimate
            # local noise floor from interior low-variance differences.
            noise = bg_std
            if noise <= 1e-6:
                diffs = [
                    abs(grid[r][c] - grid[r][c - 1])
                    for r in range(1, gh - 1)
                    for c in range(1, gw - 1)
                    if grid[r][c] <= bg_mean + 1e-3
                ]
                noise = statistics.fmean(diffs) if diffs else 0.0
            if noise <= 1e-6:
                # Uniform noiseless synthetic image with nonzero foreground -> high SNR.
                snr = 100.0 if fg_mean > 0 else 0.0
            else:
                snr = max(0.0, (fg_mean - bg_mean) / noise)
            snrs.append((idx, snr))

        if not snrs:
            return ArtifactFinding(
                self.detector_id,
                self.artifact_type,
                "GRAY",
                "image_too_small_for_snr",
                None,
                {"min_snr_ok": ok_lim, "min_snr_warn": warn_lim},
            )

        median_snr = round(statistics.median(val for _, val in snrs), 2)
        low_slices = tuple(idx for idx, val in snrs if val < warn_lim)
        warn_slices = tuple(idx for idx, val in snrs if warn_lim <= val < ok_lim)
        threshold = {"min_snr_ok": ok_lim, "min_snr_warn": warn_lim}
        if median_snr < warn_lim or len(low_slices) > max(1, len(snrs) // 3):
            return ArtifactFinding(
                self.detector_id,
                self.artifact_type,
                "RED",
                "snr_critically_low",
                median_snr,
                threshold,
                low_slices,
                "Nízký poměr signál/šum (SNR) — zopakovat sekvenci nebo zkontrolovat cívky",
            )
        if median_snr < ok_lim or warn_slices:
            return ArtifactFinding(
                self.detector_id,
                self.artifact_type,
                "ORANGE",
                "snr_reduced",
                median_snr,
                threshold,
                warn_slices or low_slices,
                "Snížené SNR — ověřit diagnostickou hodnotu",
            )
        return ArtifactFinding(
            self.detector_id,
            self.artifact_type,
            "GREEN",
            "snr_acceptable",
            median_snr,
            threshold,
        )


class MotionGhostingDetector(ArtifactDetector):
    """Detects phase-encoding ghosting and severe motion blurring."""

    detector_id = "motion_ghosting"
    artifact_type = "motion_ghosting"

    def __init__(self, *, max_ghost_ok: float = 0.25, max_ghost_crit: float = 0.42):
        self.max_ghost_ok = max_ghost_ok
        self.max_ghost_crit = max_ghost_crit

    def detect(  # noqa: D102
        self, slices: tuple[SlicePixels, ...], *, role: str | None = None
    ) -> ArtifactFinding:
        if not slices:
            return ArtifactFinding(
                self.detector_id,
                self.artifact_type,
                "GRAY",
                "no_pixel_data",
                None,
                {"max_ghost_ok": self.max_ghost_ok, "max_ghost_crit": self.max_ghost_crit},
            )
        scores: list[tuple[int, float]] = []
        for idx, sl in enumerate(slices):
            if sl.rows < 8 or sl.columns < 8:
                continue
            grid, gh, gw = _subsample_grid(sl)
            bg_mean, bg_std = _corner_stats(grid, gh, gw)
            all_vals = sorted(val for row in grid for val in row)
            p95 = all_vals[int(0.95 * (len(all_vals) - 1))] if all_vals else 0.0
            scale = max(p95 - bg_mean, 1e-6)
            # Measure peripheral energy along phase vs perpendicular edges (ghosting asymmetry)
            # plus row/col periodic ripple outside central region.
            band_h = max(2, gh // 10)
            band_w = max(2, gw // 10)
            row_border = [
                abs(grid[r][c] - bg_mean)
                for r in (*range(0, band_h), *range(gh - band_h, gh))
                for c in range(band_w, gw - band_w)
            ]
            col_border = [
                abs(grid[r][c] - bg_mean)
                for r in range(band_h, gh - band_h)
                for c in (*range(0, band_w), *range(gw - band_w, gw))
            ]
            row_energy = statistics.fmean(row_border) / scale if row_border else 0.0
            col_energy = statistics.fmean(col_border) / scale if col_border else 0.0
            if sl.phase_direction == "ROW":
                ghost_ratio = max(0.0, row_energy - 0.5 * col_energy)
            elif sl.phase_direction == "COL":
                ghost_ratio = max(0.0, col_energy - 0.5 * row_energy)
            else:
                ghost_ratio = abs(row_energy - col_energy)
            # Account for high corner noise relative to the foreground if ghosting wraps around.
            if bg_std / scale > 0.18:
                ghost_ratio = max(ghost_ratio, bg_std / scale)
            scores.append((idx, ghost_ratio))

        if not scores:
            return ArtifactFinding(
                self.detector_id,
                self.artifact_type,
                "GRAY",
                "image_too_small_for_motion",
                None,
                {"max_ghost_ok": self.max_ghost_ok, "max_ghost_crit": self.max_ghost_crit},
            )
        max_score = round(max(val for _, val in scores), 4)
        median_score = round(statistics.median(val for _, val in scores), 4)
        crit_slices = tuple(idx for idx, val in scores if val > self.max_ghost_crit)
        warn_slices = tuple(
            idx for idx, val in scores if self.max_ghost_ok < val <= self.max_ghost_crit
        )
        threshold = {"max_ghost_ok": self.max_ghost_ok, "max_ghost_crit": self.max_ghost_crit}
        if len(crit_slices) >= max(2, len(scores) // 4) or median_score > self.max_ghost_crit:
            return ArtifactFinding(
                self.detector_id,
                self.artifact_type,
                "RED",
                "motion_ghosting_detected",
                median_score,
                threshold,
                crit_slices,
                "Detekován pohybový artefakt (ghosting) — zvážit opakování sekvence",
            )
        if warn_slices or crit_slices:
            return ArtifactFinding(
                self.detector_id,
                self.artifact_type,
                "ORANGE",
                "mild_motion_ghosting",
                max_score,
                threshold,
                warn_slices or crit_slices,
                "Mírné pohybové artefakty v obraze",
            )
        return ArtifactFinding(
            self.detector_id,
            self.artifact_type,
            "GREEN",
            "no_motion_ghosting",
            median_score,
            threshold,
        )


class SlabDiscontinuityDetector(ArtifactDetector):
    """Detects post-processing / multi-slab intensity discontinuities across adjacent slices."""

    detector_id = "slab_discontinuity"
    artifact_type = "slab_discontinuity"

    def __init__(self, *, max_jump_ok: float = 0.40, max_jump_crit: float = 0.65):
        self.max_jump_ok = max_jump_ok
        self.max_jump_crit = max_jump_crit

    def detect(  # noqa: D102
        self, slices: tuple[SlicePixels, ...], *, role: str | None = None
    ) -> ArtifactFinding:
        if len(slices) < 3:
            finding = ArtifactFinding(
                self.detector_id,
                self.artifact_type,
                "GRAY",
                "too_few_slices_for_slab_check" if slices else "no_pixel_data",
                None,
                {"max_jump_ok": self.max_jump_ok, "max_jump_crit": self.max_jump_crit},
            )
            return replace(
                finding,
                per_image=tuple(replace(finding, sop_uid=image.sop_uid) for image in slices),
            )
        means = []
        for sl in slices:
            step = max(1, len(sl.pixels) // 4096)
            sample = sl.pixels[::step]
            means.append(statistics.fmean(sample) if sample else 0.0)
        global_scale = max(statistics.median(means), max(means, default=0.0) * 0.25, 1e-6)
        jumps = [abs(b - a) / global_scale for a, b in zip(means, means[1:])]
        med_jump = statistics.median(jumps) if jumps else 0.0
        # Look for isolated sharp step discontinuity far above typical slice-to-slice variation.
        max_jump = round(max(jumps, default=0.0), 4)
        anomaly_slices = tuple(
            idx + 1
            for idx, jmp in enumerate(jumps)
            if jmp > self.max_jump_ok and jmp > 3.0 * max(med_jump, 0.05)
        )
        crit_slices = tuple(
            idx + 1
            for idx, jmp in enumerate(jumps)
            if jmp > self.max_jump_crit and jmp > 3.5 * max(med_jump, 0.05)
        )
        threshold = {"max_jump_ok": self.max_jump_ok, "max_jump_crit": self.max_jump_crit}
        per_image = []
        for index, image in enumerate(slices):
            status, reason, score = "GREEN", "slice_continuity_ok", None
            if index == 0:
                status, reason = "GRAY", "no_previous_slice_for_slab_check"
            else:
                score = round(jumps[index - 1], 4)
                if index in crit_slices:
                    status, reason = "RED", "slab_discontinuity_detected"
                elif index in anomaly_slices:
                    status, reason = "ORANGE", "mild_slab_discontinuity"
            per_image.append(ArtifactFinding(
                self.detector_id, self.artifact_type, status, reason, score, threshold,
                sop_uid=image.sop_uid,
            ))
        if crit_slices:
            return ArtifactFinding(
                self.detector_id,
                self.artifact_type,
                "RED",
                "slab_discontinuity_detected",
                max_jump,
                threshold,
                crit_slices,
                "Narušená návaznost řezů / multi-slab postprocessingový artefakt",
                per_image=tuple(per_image),
            )
        if anomaly_slices:
            return ArtifactFinding(
                self.detector_id,
                self.artifact_type,
                "ORANGE",
                "mild_slab_discontinuity",
                max_jump,
                threshold,
                anomaly_slices,
                "Zjištěn skok intenzity mezi řezy",
                per_image=tuple(per_image),
            )
        return ArtifactFinding(
            self.detector_id,
            self.artifact_type,
            "GREEN",
            "slice_continuity_ok",
            max_jump,
            threshold,
            per_image=tuple(per_image),
        )


def default_registry() -> DetectorRegistry:
    return DetectorRegistry(
        (
            LowSNRDetector(),
            MotionGhostingDetector(),
            SlabDiscontinuityDetector(),
        )
    )
