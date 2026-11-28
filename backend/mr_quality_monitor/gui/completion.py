"""Build the operator's completion checklist from the current validation result."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExaminationCompletion:
    state: str
    details: tuple[str, ...] = ()


def _confirmed_partial(acquisitions: tuple) -> bool:
    if not acquisitions:
        return False
    for item in acquisitions:
        details = item.get("completion_details") or {}
        policy = details.get("policy") or {}
        expected = policy.get("expected_members") or policy.get("min_members")
        if not expected or details["actual_members"] >= expected:
            return False
    return True


def examination_completion(
    protocol: dict, payload: dict, planned_rows: tuple, *, stale: bool, failed: int
) -> ExaminationCompletion:
    """Quality warnings may be accepted by the protocol; absence still requires closure."""
    if failed:
        return ExaminationCompletion(
            "verification_pending", (f"Analýza selhala u {failed} MR objektů.",)
        )
    finalized = payload.get("finalized", False) and not stale
    by_id = {row.key.removeprefix("seq:"): row for row in planned_rows}
    study_rules = protocol.get("study_rules") or {}
    checks = {check["rule_id"]: check for check in payload.get("study_checks", ())}
    definitions = sorted(protocol["sequences"], key=lambda definition: definition["order"])
    unmet = []
    pending = []
    warnings = []
    covered = set()
    possibly_covered = set()
    for rule in study_rules.get("fallback_groups", ()):
        check = checks.get(rule["rule_id"], {})
        details = check.get("details", {})
        reason = check.get("reason")
        if reason == "fallback_sequence_used" and check.get("status") == "ORANGE":
            covered.update(details["primary_missing"] + details["primary_bad"])
            names = ", ".join(by_id[sid].name for sid in details["fallback_ok"])
            warnings.append(f"Použita náhradní sekvence: {names}")
        elif reason != "primary_set_valid":
            fallback_rows = [by_id[sid] for sid in rule["fallback_sequence_ids"]]
            names = ", ".join(row.name for row in fallback_rows)
            if finalized and reason == "fallback_sequence_used":
                unmet.append(f"Kritická odchylka při použití náhradní sekvence: {names}")
            elif finalized and reason == "primary_invalid_without_resolve":
                if any(
                    row.status == "GRAY"
                    and row.evaluations
                    and not _confirmed_partial(row.evaluations)
                    for row in fallback_rows
                ):
                    possibly_covered.update(details["primary_missing"] + details["primary_bad"])
                    pending.append(f"Čeká na ověření náhradní sekvence: {names}")
                elif all(not row.evaluations for row in fallback_rows):
                    unmet.append(f"Chybí náhradní sekvence: {names}")
                else:
                    unmet.append(f"Náhradní sekvence nesplňuje požadavky: {names}")
            else:
                pending.append(f"Náhrada při nevyhovující primární sadě: {names}")

    for definition in definitions:
        sid = definition["sequence_id"]
        if not definition["required"] or sid in covered:
            continue
        row = by_id[sid]
        blockers = pending if sid in possibly_covered else unmet
        matched = tuple(item for item in row.evaluations if item["sequence_id"] == sid)
        if row.reason == "required_sequence_absent_at_finalization":
            if finalized:
                blockers.append(f"Chybí povinná sekvence: {row.name}")
            else:
                pending.append(f"Čeká na povinnou sekvenci: {row.name}")
        elif row.reason == "identification_uncertain":
            pending.append(f"Nejednoznačné přiřazení sekvence: {row.name}")
        elif _confirmed_partial(matched):
            blockers.append(f"Neúplná akvizice: {row.name}")
        elif row.status == "RED":
            blockers.append(f"Kritická odchylka: {row.name}")
        elif row.status == "GRAY" or row.awaiting_analysis:
            text = "Čeká na ověření" if matched else "Čeká na povinnou sekvenci"
            pending.append(f"{text}: {row.name}")
        elif row.status == "ORANGE":
            warnings.append(f"Varování: {row.name}")

    for rule in study_rules.get("min_valid_groups", ()):
        check = checks.get(rule["rule_id"], {})
        valid = set(check.get("details", {}).get("valid_sequences", ()))
        shortfall = rule["min_valid_count"] - len(valid)
        if shortfall <= 0:
            continue
        remaining = [
            by_id[item["sequence_id"]]
            for item in definitions
            if item["group"] == rule["group"] and item["sequence_id"] not in valid
        ]
        names = ", ".join(row.name for row in remaining)
        detail = f"{rule['group']} — zbývá doplnit {shortfall} vyhovujících sekvencí: {names}"
        possible = sum(
            row.status == "GRAY"
            and row.reason != "optional_sequence_absent"
            and not _confirmed_partial(row.evaluations)
            for row in remaining
        )
        (unmet if finalized and possible < shortfall else pending).append(detail)

    for rule in study_rules.get("geometry_alignments", ()):
        if rule["severity"] != "critical":
            continue
        check = checks.get(rule["rule_id"], {})
        targets = set(rule["target_sequence_ids"]) - covered
        mismatches = check.get("details", {}).get("mismatches", {})
        if targets.intersection(mismatches):
            unmet.append("Kritická odchylka v geometrickém zarovnání sekvencí.")
        elif targets and check.get("status") in {None, "GRAY"}:
            pending.append("Čeká na ověření geometrického zarovnání sekvencí.")

    if stale:
        pending.append("Čeká na zpracování přijatých snímků.")
    if not finalized:
        if not (pending or unmet):
            pending.append("Požadavky splněny. Vyšetření lze uzavřít.")
        state = "in_progress"
    elif unmet:
        state = "incomplete"
    elif pending:
        state = "verification_pending"
    else:
        state = "done"
    return ExaminationCompletion(state, tuple(unmet + pending + warnings))
