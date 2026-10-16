"""PDF export of matching results – built in memory only, nothing is written to disk."""
from __future__ import annotations

import unicodedata
from datetime import date
from pathlib import Path

from .matching import STATUS_LABELS, CohortResult, sex_caution
from .model import PatientProfile
from .referral import _summary

FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
]
DISCLAIMER = ("Předběžný screening podle strukturovaných kritérií. Nejde o doporučení léčby ani o potvrzení zařazení "
              "do studie – o vhodnosti vždy rozhoduje lékař studie. Aktuální nábor ověřte u studijního týmu.")


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


class _Doc:
    def __init__(self):
        from fpdf import FPDF

        self.pdf = FPDF(format="A4")
        self.pdf.set_auto_page_break(True, margin=15)
        self.pdf.set_margins(15, 15, 15)
        font = next((f for f in FONT_CANDIDATES if f.is_file()), None)
        if font:
            self.pdf.add_font("Uni", "", str(font))
            self.family, self.clean = "Uni", str
        else:  # core fonts cannot render Czech diacritics
            self.family, self.clean = "Helvetica", _ascii
        self.pdf.add_page()

    def text(self, text: str, size: int = 10, gap: float = 1.5, indent: float = 0) -> None:
        self.pdf.set_font(self.family, size=size)
        self.pdf.set_x(self.pdf.l_margin + indent)
        self.pdf.multi_cell(self.pdf.w - self.pdf.r_margin - self.pdf.get_x(), size * 0.5,
                            self.clean(text), new_x="LMARGIN", new_y="NEXT")
        self.pdf.ln(gap)

    def bullets(self, title: str, items: list[str]) -> None:
        if items:
            self.text(title, size=10, gap=0.5, indent=4)
            for item in items:
                self.text(f"- {item}", size=9, gap=0.3, indent=8)


def _reason(x) -> str:
    return x.criterion.get("value") if x.criterion["field"] == "manual" else x.reason


def build_pdf(profile: PatientProfile, groups: list[tuple[str, list[CohortResult]]], *, lay: bool,
              today: date | None = None) -> bytes:
    """groups = [(section title, results)], already filtered and ordered as shown in the UI."""
    doc = _Doc()
    doc.text("TriaLink – výsledky vyhledání klinických studií", size=16, gap=1)
    doc.text(f"Masarykův onkologický ústav · datum hodnocení {(today or date.today()):%d. %m. %Y}", size=9, gap=4)
    doc.text("Zadané údaje (bez identifikace pacienta)", size=12, gap=1)
    for line in _summary(profile) or ["Nebyly zadány žádné údaje."]:
        doc.text(f"- {line}", size=9, gap=0.3, indent=4)
    doc.pdf.ln(3)
    for title, results in groups:
        doc.text(f"{title} ({len(results)})", size=13, gap=1.5)
        if not results:
            doc.text("Žádná studie v této skupině.", size=9, gap=2, indent=4)
        for r in results:
            s = r.study
            doc.text(f"{s.acronym} · {s.study_id} · {r.cohort['label']}", size=11, gap=0.5)
            doc.text(f"{STATUS_LABELS[r.status]} · {s.indication}", size=9, gap=0.5, indent=4)
            note = sex_caution(r, profile)
            if note:
                doc.text(f"Upozornění: {note}", size=9, gap=0.5, indent=4)
            doc.bullets("Odpovídá:" if lay else "Splněno:", [x.reason for x in r.satisfied])
            doc.bullets("Je potřeba ověřit:" if lay else "K ověření:", [_reason(x) for x in r.open_items])
            doc.bullets("Neodpovídá:" if lay else "Nesplněno:", [x.reason for x in r.failed])
            contacts = [c for c in s.contacts if c.email and (c.role == "SC" or not lay)]
            if contacts:
                doc.text("Kontakt na studijní tým: " + ", ".join(f"{c.role} {c.name} <{c.email}>" for c in contacts),
                         size=9, gap=0.5, indent=4)
            doc.pdf.ln(2.5)
    doc.text(DISCLAIMER, size=8, gap=0)
    return bytes(doc.pdf.output())
