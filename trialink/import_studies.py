"""CLI: import the SONIS export + criteria into IRIS, or just validate locally.

    python -m trialink.import_studies --check          # validate only, no database
    python -m trialink.import_studies                  # validate and upsert into IRIS (TriaLink schema)
    python -m trialink.import_studies --export path.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from .catalog import load_catalog


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export", type=Path, help="Cesta k exportu SONIS (výchozí: dodaný export)")
    ap.add_argument("--check", action="store_true", help="Pouze validace, bez zápisu do IRIS")
    args = ap.parse_args(argv)

    studies, report, issues = load_catalog(args.export)
    print(f"Export: {report.source_name}  sha256={report.source_hash[:12]}…")
    print(f"Studií: {report.records}, opraveno: {report.repaired or '-'}, karanténa: {len(report.quarantined)}, duplicity: {report.duplicates or '-'}")
    for q in report.quarantined:
        print(f"  KARANTÉNA řádek {q['line']}: {q['reason']}")
    errors = [i for i in issues if "citace" in i or "neznám" in i or "neplatn" in i]
    for i in issues:
        print(f"  {'CHYBA' if i in errors else 'info'}: {i}")
    for s in studies:
        for w in s.warnings:
            print(f"  varování {s.study_id}: {w}")
        for b in s.referral_blockers:
            print(f"  referral blokován {s.study_id}: {b}")
    if errors:
        print("Import zastaven – opravte chyby v pravidlech.", file=sys.stderr)
        return 1
    if args.check:
        return 0

    load_dotenv()
    from . import db

    conn = db.connect()
    try:
        created = db.ensure_schema(conn)
        if created:
            print(f"Vytvořeny tabulky: {', '.join(created)}")
        result = db.save_catalog(conn, studies, report, issues)
        print(f"IRIS: nových verzí {result['new_versions']}, beze změny {result['unchanged']}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
