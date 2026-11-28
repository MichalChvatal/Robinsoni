"""Text and colors only; clinical decisions remain in the validation engine."""

import json

STATUS_TEXT = {
    "GREEN": "OK",
    "ORANGE": "Varování",
    "RED": "Kritická odchylka",
    "GRAY": "Nevyhodnoceno",
}
COLORS = {
    "GREEN": ("#ddf3e6", "#17643b"),
    "ORANGE": ("#fff0cd", "#875500"),
    "RED": ("#fce2e2", "#a02424"),
    "GRAY": ("#edf0f4", "#536173"),
}
REASONS = {
    "image_skipped_parameter_error": "Obrazová kontrola vynechána kvůli chybě parametrů",
    "image_waiting_for_parameters": "Obrazová kontrola čeká na ověření parametrů",
    "no_previous_slice_for_slab_check": "První řez nemá předchozí řez pro porovnání návaznosti",
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
    # Study-level rule reasons (clinical mode)
    "group_min_valid_not_met": "Není splněn minimální počet platných akvizic",
    "fallback_sequence_used": "Použita záložní sekvence (primární nedostupná)",
    "geometry_alignment_mismatch": "Nesouhlasí geometrické zarovnání sekvencí",
    "prostate_coverage_incomplete": "Pokrytí prostaty je neúplné",
    "segmentation_mask_bounds_unavailable": "Rozsah prostaty ze segmentační masky není ověřen",
    "clinical_requirements_passed": "Klinické požadavky splněny",
    "image_not_required": "Obrazová kontrola pro tuto sekvenci není definována",
    "study_rule_passed": "Pravidlo celého vyšetření splněno",
    "study_rule_failed": "Pravidlo celého vyšetření nesplněno",
    "missing_slice_positions": "Chybí informace o poloze řezů pro geometrické ověření",
    "alignment_axial_unavailable": "Chybí jednoznačná axiální T2W série pro porovnání",
    "alignment_reference_unknown": "Série nemají ověřený společný souřadný systém",
    "alignment_geometry_unknown": "Chybí spolehlivé údaje o poloze nebo orientaci řezů",
    "alignment_no_overlap": "Rozsahy řezů DWI a axiální T2W se nepřekrývají",
    "alignment_measured": "Geometrické porovnání s axiální T2W",
    "alignment_outside_tolerance": "Geometrická odchylka od axiální T2W je mimo toleranci",
    "t2_plane_count": "Požadovány jsou dvě vyhovující T2W roviny",
    "t2_axial_present": "Axiální T2W je přítomná",
    "t2_axial_missing": "Axiální T2W chybí",
    "dwi_set_status": "Technická kontrola čtyř částí DWI sady",
    "dwi_resolve_required": "DWI sada nevyhovuje. Prověřte odpovídající RESOLVE sérii",
    "resolve_identity_unknown": "Identifikace odpovídající RESOLVE série není nastavená",
    "resolve_present_unverified": "RESOLVE byla nalezena. Její použitelnost čeká na ověření",
    "resolve_missing": "Po uzavření vyšetření chybí odpovídající RESOLVE série",
    "resolve_pending": "Čeká se na odpovídající RESOLVE sérii",
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
    if kind == "limit":
        return plain_requirement(rule)
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


PARAMETER_NAMES = {
    "RepetitionTime": "Čas opakování (TR)",
    "EchoTime": "Čas echa (TE)",
    "InversionTime": "Inverzní čas (TI)",
    "SliceThickness": "Tloušťka řezu",
    "SpacingBetweenSlices": "Rozestup mezi řezy",
    "PixelSpacing": "Velikost pixelu",
    "FlipAngle": "Sklápěcí úhel",
    "Rows": "Počet řádků obrazu",
    "Columns": "Počet sloupců obrazu",
    "MagneticFieldStrength": "Síla magnetického pole",
    "NumberOfAverages": "Počet opakování pro průměrování",
    "EchoTrainLength": "Počet ech v sérii",
    "PixelBandwidth": "Šířka pásma na pixel",
    "PrivateDiffusionBValue": "Skutečná b-hodnota",
    "SliceGap": "Mezera mezi řezy",
    "FieldOfView": "Velikost zobrazeného pole (FOV)",
    "PhaseResolution": "Rozlišení ve směru fáze",
    "FrequencyResolution": "Rozlišení ve směru frekvence",
    "DWIAlignment": "Shoda DWI a axiální T2W",
    "T2PlaneCount": "Počet vyhovujících T2W rovin",
    "T2Axial": "Přítomnost axiální T2W",
    "DWISet": "Sada DWI",
    "ResolvePresence": "Přítomnost RESOLVE",
    "low_snr": "Nízký poměr signál/šum",
    "motion_ghosting": "Pohybové artefakty",
    "slab_discontinuity": "Nespojitost řezů",
}


def sequence_layer(row, *, image: bool, stale: bool) -> tuple[str, str, str]:
    evaluations = [
        item for item in row.evaluations if not image or item.get("image_required", True)
    ]
    if image and row.evaluations and not evaluations:
        return "GRAY", "Nevyžadováno", "Obrazová kontrola není v protokolu požadována."
    field = "image_status" if image else "metadata_status"
    statuses = {item.get(field, "GRAY") for item in evaluations}
    status = next(
        (value for value in ("RED", "GRAY", "ORANGE", "GREEN") if value in statuses),
        "GRAY" if image else row.status,
    )
    if stale and status != "RED":
        status = "GRAY"
    checks = [
        check for item in evaluations for check in item.get("checks", ())
        if check.get("rule_id", "").startswith("image:") == image
        and check["status"] == status and status in {"RED", "ORANGE"}
    ]
    names = dict.fromkeys(
        PARAMETER_NAMES.get(check.get("field"), check.get("field") or "Geometrie řezů")
        for check in checks
    )
    text = STATUS_TEXT[status]
    if names:
        text += "\n" + ", ".join(names)
    tooltip = "\n\n".join(dict.fromkeys(critical_description(check) for check in checks))
    if not tooltip:
        tooltip = reason_text(row.reason) if not evaluations else STATUS_TEXT[status]
        if image and status == "GRAY":
            reasons = dict.fromkeys(
                reason_text(reason) for item in evaluations for reason in item.get("reasons", ())
                if reason in {"image_skipped_parameter_error", "image_waiting_for_parameters"}
            )
            tooltip = "\n".join(reasons) or "Obrazová kvalita zatím nebyla vyhodnocena."
    if stale:
        tooltip = "Čeká na aktualizaci hodnocení.\n" + tooltip
    return status, text, tooltip
GEOMETRY_NAMES = {
    "incomplete_geometry": "chybí údaje o poloze nebo rozměrech řezů",
    "uncertain_dimension": "některé údaje potřebné k seskupení řezů jsou nejasné",
    "invalid_geometry": "údaje o poloze nebo rozměrech řezů jsou neplatné",
    "repeated_slice_position": "více řezů má stejnou polohu",
    "irregular_slice_spacing": "rozestupy mezi řezy nejsou stejné",
    "conflicting_declared_spacing": "snímky uvádějí různé rozestupy mezi řezy",
    "declared_spacing_mismatch": "skutečný rozestup nesouhlasí s údajem ve snímcích",
}
ACTION_TEXT = {
    "Review geometry findings": "Zkontrolujte údaje o geometrii řezů.",
    "Review DEMO timing requirement": "Ověřte časování podle ukázkového protokolu.",
}


def plain_value(value) -> str:
    if isinstance(value, (list, tuple)):
        return " × ".join(plain_value(item) for item in value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (
            format(value, ",.15g" if isinstance(value, float) else ",d")
            .replace(",", "\u00a0")
            .replace(".", ",")
        )
    return display_value(value)


def plain_requirement(rule: dict) -> str:
    constraint = rule.get("constraint") or {}
    kind = constraint.get("kind")
    unit = f" {rule['unit']}" if rule.get("unit") else ""
    if kind == "limit":
        qualifier = "nejvýše" if constraint["bound"] == "maximum" else "nejméně"
        return f"{qualifier} {plain_value(constraint['value'])}{unit}"
    if kind == "range":
        return (
            f"hodnotu od {plain_value(constraint['minimum'])} "
            f"do {plain_value(constraint['maximum'])}{unit}"
        )
    if kind == "target":
        tolerance_unit = " %" if constraint["tolerance_kind"] == "relative_percent" else unit
        return (
            f"hodnotu {plain_value(constraint['target'])}{unit} "
            f"s tolerancí ±{plain_value(constraint['tolerance'])}{tolerance_unit}"
        )
    if kind == "exact":
        return f"hodnotu {plain_value(constraint['value'])}{unit}"
    if kind == "one_of":
        return "jednu z hodnot " + " nebo ".join(
            plain_value(value) + unit for value in constraint["values"]
        )
    if kind == "contains":
        return f"údaj obsahující „{constraint['value']}“"
    return ""


def critical_description(check: dict) -> str:
    field = check.get("field")
    name = PARAMETER_NAMES.get(field, field or "Kontrolovaný údaj")
    reason = check.get("reason")
    actual = check.get("actual")
    if field == "FieldOfView" and reason == "outside_requirement":
        constraint = (check.get("expected") or {}).get("constraint", {})
        if isinstance(actual, (tuple, list)) and len(actual) == 2:
            low = plain_value(constraint.get("minimum"))
            high = plain_value(constraint.get("maximum"))
            unit = check.get("unit") or "mm"
            minimum, maximum = constraint.get("minimum"), constraint.get("maximum")
            outside = [
                axis
                for axis, value in zip(("A", "B"), actual, strict=True)
                if isinstance(value, (int, float))
                and isinstance(minimum, (int, float))
                and isinstance(maximum, (int, float))
                and not minimum <= value <= maximum
            ]
            outside_text = ", ".join(outside) if outside else "žádný"
            return (
                f"Rozměry FOV A={plain_value(actual[0])} {unit} a "
                f"B={plain_value(actual[1])} {unit}; každý se samostatně "
                f"porovnává s rozsahem {low}–{high} {unit}. "
                f"Mimo rozsah: {outside_text}."
            )
    if field == "PrivateDiffusionBValue" and reason == "outside_requirement":
        bounds = (check.get("expected") or {}).get("constraint", {})
        if isinstance(actual, (int, float)):
            for bound, phrase in (
                ("maximum", "překročila povolené maximum"),
                ("minimum", "je pod povoleným minimem"),
            ):
                limit = bounds.get(bound)
                if bounds.get("bound") == bound:
                    limit = bounds.get("value")
                if limit is not None and (actual > limit if bound == "maximum" else actual < limit):
                    return (
                        f"B-hodnota {plain_value(actual)} s/mm² "
                        f"{phrase} {plain_value(limit)} s/mm²."
                    )
    if field == "DWIAlignment":
        message = REASONS.get(reason, name) + "."
        expected = check.get("expected") or {}
        if actual is not None:
            position, spacing, angle = (plain_value(value) for value in actual)
            message += (
                f" Posun řezů {position} mm, rozdíl roztečí {spacing} mm,"
                f" rozdíl orientace {angle}°."
            )
        if expected.get("position_tolerance_mm") is None or (
            expected.get("orientation_tolerance_degrees") is None
        ):
            message += " Tolerance není schválená, výsledek shody nelze určit."
        else:
            message += (
                f" Povolená odchylka je {plain_value(expected['position_tolerance_mm'])} mm"
                f" a {plain_value(expected['orientation_tolerance_degrees'])}°."
            )
        return message
    if field == "T2PlaneCount":
        minimum = check["expected"]["minimum"]
        message = f"Vyhovující T2W roviny: {actual} ze {minimum} požadovaných."
        if check["status"] == "RED" and actual < minimum:
            missing = minimum - actual
            message += (
                " Chybí jedna vyhovující rovina."
                if missing == 1
                else f" Chybějí {missing} vyhovující roviny."
            )
        return message
    if field in {"T2Axial", "DWISet", "ResolvePresence"}:
        return REASONS.get(reason, name) + "."
    if reason == "geometry_issues":
        findings = [GEOMETRY_NAMES[code] for code in (actual or ()) if code in GEOMETRY_NAMES]
        return (
            "Kontrola geometrie řezů zjistila odchylku. "
            + ("; ".join(findings).capitalize() + "." if findings else "")
        ).strip()
    if reason == "required_metadata_absent":
        message = f"V datech chybí povinný údaj „{name}“."
    elif reason == "outside_requirement" and actual is not None:
        unit = f" {check['unit']}" if check.get("unit") else ""
        message = f"{name} má hodnotu {plain_value(actual)}{unit} mimo požadavek protokolu."
    else:
        description = REASONS.get(reason, "Konkrétní důvod v uloženém výsledku chybí")
        message = f"{name} — {description}."
    expected = plain_requirement(check.get("expected") or {})
    if expected:
        message += f"\nUkázkový protokol očekává {expected}."
    constraint = (check.get("expected") or {}).get("constraint") or {}
    if reason == "outside_requirement" and constraint.get("warning_minimum") is not None:
        unit = f" {check['unit']}" if check.get("unit") else ""
        message += (
            f"\nHodnota je i mimo pásmo varování od {plain_value(constraint['warning_minimum'])} "
            f"do {plain_value(constraint['warning_maximum'])}{unit}."
        )
    return message


def sequence_overview(row, statuses) -> str:
    findings = {}
    for evaluation in (*row.evaluations, {"checks": row.checks}):
        for check in evaluation.get("checks", ()):
            if check.get("status") not in statuses:
                continue
            message = critical_description(check)
            action = check.get("action") or ""
            affected = findings.setdefault((message, ACTION_TEXT.get(action, action)), set())
            if uid := check.get("sop_uid"):
                affected.add((uid, check.get("frame_index")))
    if not findings:
        if row.reason == "required_sequence_absent_at_finalization":
            return "Při uzavření vyšetření chyběla tato povinná sekvence."
        if row.status == "RED":
            return "Konkrétní důvod v uloženém výsledku chybí."
        evidence = tuple(
            dict.fromkeys(
                reason
                for evaluation in row.evaluations
                for reason in evaluation.get("reasons", ())
                if reason.startswith("completion_") or reason.startswith("sequence_")
            )
        )
        if evidence:
            return "\n".join(reason_text(reason) for reason in evidence)
        return reason_text(row.reason)
    blocks = []
    for (message, action), affected in findings.items():
        if affected:
            message += f"\nPočet dotčených snímků nebo rámců: {len(affected)}."
        if action:
            message += f"\nPodle protokolu: {action}"
        blocks.append(message)
    return "\n\n".join(blocks)
