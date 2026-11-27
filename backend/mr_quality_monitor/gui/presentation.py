"""Text and colors only; clinical decisions remain in the validation engine."""

import json

STATUS_TEXT = {
    "GREEN": "● OK · DEMO",
    "ORANGE": "▲ Varování",
    "RED": "■ Kritická odchylka",
    "GRAY": "○ Nevyhodnoceno",
}
COLORS = {
    "GREEN": ("#ddf3e6", "#17643b"),
    "ORANGE": ("#fff0cd", "#875500"),
    "RED": ("#fce2e2", "#a02424"),
    "GRAY": ("#edf0f4", "#536173"),
}
REASONS = {
    "received_all_repeats_retained": "Přijato; všechny dílčí akvizice zachovány",
    "required_sequence_pending": "Požadovaná sekvence zatím nebyla vyhodnocena",
    "analysis_input_failed": "MR objekt se nepodařilo ověřit nebo přečíst",
    "required_sequence_absent_at_finalization": "Povinná sekvence při uzavření chybí",
    "optional_sequence_absent": "Nepovinná sekvence nepřijata",
    "identification_uncertain": "Zařazení sekvence není jednoznačné",
    "sequence_unknown": "Sekvence není rozpoznána",
    "sequence_ambiguous": "Nejednoznačná shoda",
    "completion_not_confirmed": "Dokončení akvizice nepotvrzeno",
    "completion_count_not_met": "Nesouhlasí očekávaný počet snímků/rámců",
    "image_not_evaluated": "Obrazová kvalita nevyhodnocena",
    "image_not_requested_in_demo": "Obrazová kontrola není součástí DEMO",
    "within_requirement": "V požadovaném pásmu",
    "within_warning_band": "V tolerovaném varovném pásmu",
    "outside_requirement": "Mimo požadované hodnoty",
    "required_metadata_absent": "Chybí povinné metadata",
    "optional_metadata_absent": "Nepovinný údaj chybí",
    "metadata_invalid": "Neplatná hodnota",
    "metadata_conflicting": "Rozpor mezi zdroji",
    "metadata_unit_mismatch": "Nesouhlasí jednotka",
    "geometry_issues": "Nález v geometrii řezů",
    "metadata_not_processed": "Metadata čekají na zpracování",
    "demo_metadata_requirements_passed": "Metadatové požadavky DEMO splněny",
    "completion_identity_uncertain": "Počet nelze určit kvůli nejasné identitě snímků",
}


def reason_text(value: str) -> str:
    return "; ".join(REASONS.get(part, part) for part in value.split(";") if part)


def display_value(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, (list, tuple)):
        return " × ".join(display_value(item) for item in value)
    return f"{value:g}" if isinstance(value, float) else str(value)


def requirement_text(rule: dict) -> str:
    constraint = rule.get("constraint", {})
    kind = constraint.get("kind")
    unit = " " + rule["unit"] if rule.get("unit") else ""
    if kind == "exact":
        return "= " + display_value(constraint["value"]) + unit
    if kind == "one_of":
        return " / ".join(display_value(item) for item in constraint["values"]) + unit
    if kind == "contains":
        return "obsahuje " + constraint["value"]
    if kind == "range":
        result = (
            f"{display_value(constraint['minimum'])}–{display_value(constraint['maximum'])}{unit}"
        )
        if constraint.get("warning_minimum") is not None:
            result += (
                f"; varování {display_value(constraint['warning_minimum'])}–"
                f"{display_value(constraint['warning_maximum'])}{unit}"
            )
        return result
    if kind == "target":
        tolerance_unit = " %" if constraint["tolerance_kind"] == "relative_percent" else unit
        return (
            f"{display_value(constraint['target'])}{unit} ± "
            f"{display_value(constraint['tolerance'])}{tolerance_unit}"
        )
    return json.dumps(rule, ensure_ascii=False)


def effective_status(row, stale: bool) -> str:
    return "GRAY" if stale and row.status != "RED" else row.status
