"""Step-by-step guide: role -> (doctor: input choice) -> report -> review -> detail -> results.

Doctors can also pick "quick" (rychlé zadání): a compact one-row form from `quick_entry` that asks only for the
next needed parameter; it can switch to the full detail form and back, and "Upravit údaje" in results returns
to the step the search was started from (`wiz_edit_step`).

All state lives in st.session_state only: wiz_* = navigation, doc_* / pat_* = patient data per role.
"""
from __future__ import annotations

import os
import re
import time
from datetime import date

import streamlit as st
import streamlit.components.v1 as components

import quick_entry
import report_input
from common import apply_profile, disclaimer, profile_form, render_result, reset
from trialink import anonymize, extraction
from trialink.demo_cases import CASES
from trialink.extraction import MAX_REPORT_CHARS, ExtractionError
from trialink.lay_terms import TOOLTIPS
from trialink.matching import STATUS_CANDIDATE, STATUS_EXCLUDED, STATUS_VERIFY, match
from trialink.ocr import OcrError
from trialink.referral import build_referral, simulate_send

ss = st.session_state
DOCTOR, PATIENT = "lekar", "pacient"
OTHER_STUDIES = "Studie pro více druhů nádorů"
STEP_TITLES_DOC = {
    "role": "Kdo jste?", "doc_choice": "Způsob zadání údajů", "report": "Lékařská zpráva",
    "review": "Kontrola a anonymizace", "detail": "Parametry pacienta", "quick": "Rychlé zadání",
    "results": "Výsledky párování",
}
STEP_TITLES_PAT = {
    "role": "Kdo jste?", "report": "Lékařská zpráva",
    "review": "Kontrola a anonymizace", "detail": "Údaje o nemoci", "results": "Nalezené studie",
}
STEP_TITLES = STEP_TITLES_DOC


def _step_title(step: str) -> str:
    titles = STEP_TITLES_DOC if ss.get("wiz_role") == DOCTOR else STEP_TITLES_PAT
    return titles.get(step, "")


# --- navigation --------------------------------------------------------------------------------------------------

def _go(step: str) -> None:
    ss.setdefault("wiz_history", []).append(ss.get("wiz_step", "role"))
    ss["wiz_step"] = step
    ss["wiz_scroll_top"] = ss.get("wiz_scroll_top", 0) + 1


def _back() -> None:
    history = ss.get("wiz_history", [])
    ss["wiz_step"] = history.pop() if history else "role"
    ss["wiz_scroll_top"] = ss.get("wiz_scroll_top", 0) + 1


def _restart() -> None:
    for prefix in ("wiz", "doc", "pat"):
        reset(prefix)


go_home = _restart


def _prefix() -> str:
    return "doc" if ss.get("wiz_role") == DOCTOR else "pat"


def _lay() -> bool:
    return ss.get("wiz_role") != DOCTOR


def _nav() -> None:
    step = ss.get("wiz_step", "role")
    c1, c2, c3 = st.columns([1, 3, 1])
    c1.button("← Zpět", on_click=_back, key="wiz_back", width="stretch")
    who = "👩‍⚕️ Lékař" if ss.get("wiz_role") == DOCTOR else "🙂 Pacient"
    c2.markdown(f"<div style='text-align:center'>{who} · <b>{_step_title(step)}</b></div>", unsafe_allow_html=True)
    c3.button("↺ Začít znovu", on_click=_restart, key="wiz_restart", width="stretch")
    st.divider()


# --- steps -------------------------------------------------------------------------------------------------------

CARD_KEYS = ("wiz_role_doc", "wiz_role_pat", "wiz_choice_report", "wiz_choice_form", "wiz_choice_quick")
CARD_CSS = "<style>" + ", ".join(f".st-key-{k} button" for k in CARD_KEYS) + """{
  min-height: 170px; padding: 1.4rem 1.6rem; border-radius: 1.1rem; justify-content: flex-start; align-items: flex-start;
  text-align: left; background: linear-gradient(180deg, rgba(255,255,255,0.92), rgba(236,246,251,0.9));
  border: 1px solid rgba(11,106,141,0.12); box-shadow: 0 12px 24px rgba(14, 46, 72, 0.08);
  transition: border-color .15s, box-shadow .15s, transform .15s, background .15s;}
""" + ", ".join(f".st-key-{k} button:hover" for k in CARD_KEYS) + """{
  border-color: rgba(11,106,141,0.28); box-shadow: 0 18px 32px rgba(14, 46, 72, 0.12); transform: translateY(-2px);
  background: linear-gradient(180deg, rgba(243,251,255,1), rgba(228,242,247,1));}
""" + ", ".join(f".st-key-{k} button p" for k in CARD_KEYS) + """{
  white-space: pre-line; font-size: 1rem; line-height: 1.55; text-align: left; color: #1d2d3d;}
""" + ", ".join(f".st-key-{k} button p strong" for k in CARD_KEYS) + """{
  display: block; font-size: 1.45rem; margin-bottom: 0.6rem; line-height: 1.2; color: #0b4d69;}
</style>"""
SEARCH_KEYS = ("wiz_search", "wiz_to_results")
SEARCH_CSS = "<style>" + ", ".join(f".st-key-{k} button" for k in SEARCH_KEYS) + """{
  background: linear-gradient(90deg, #0b6a8d, #1d8ab9); border-color: transparent; color: #fff; border-radius: 0.9rem;
  box-shadow: 0 14px 24px rgba(11,106,141,0.2);}
""" + ", ".join(f".st-key-{k} button:hover" for k in SEARCH_KEYS) + """{
  background: linear-gradient(90deg, #085d7f, #1978a5); border-color: transparent; color: #fff;}
""" + ", ".join(f".st-key-{k} button:disabled" for k in SEARCH_KEYS) + """{
  background: linear-gradient(90deg, #9cc7db, #aed8e8); border-color: transparent; color: rgba(255,255,255,0.9);}
</style>"""


def _card(column, key: str, title: str, text: str, on_click, args) -> None:
    column.button(f"**{title}**\n\n{text}", key=key, on_click=on_click, args=args, width="stretch")


def _choose_role(role: str) -> None:
    _restart()
    ss["wiz_role"] = role
    if role == DOCTOR:
        ss["doc_t0"] = time.time()
        _go("doc_choice")
    else:
        _go("report")


def step_role() -> None:
    st.info("**TriaLink pomáhá najít klinické studie Masarykova onkologického ústavu, které by mohly být vhodné "
            f"pro konkrétního pacienta.**\n\n**Co je klinická studie?** {TOOLTIPS['studie']}")
    st.markdown("### Kdo jste?")
    st.markdown(CARD_CSS, unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    _card(c1, "wiz_role_doc", "🩺 Jsem lékař",
          "Zadejte údaje ze zprávy nebo formuláře, získejte seznam vhodných studií a pošlete referral studijnímu týmu.",
          _choose_role, (DOCTOR,))
    _card(c2, "wiz_role_pat", ":red[✚] Jsem pacient",
          "Nahrajte svou lékařskou zprávu a zjistěte, o kterých studiích si můžete promluvit se svým onkologem.",
          _choose_role, (PATIENT,))
    st.caption("Aplikace neukládá žádné údaje o pacientech. Vše existuje jen v paměti po dobu relace a smaže se "
               "tlačítkem „Začít znovu“ nebo po 15 minutách nečinnosti.")


def _go_quick() -> None:
    quick_entry.load_profile("doc", ss.get("doc_profile"))
    _go("quick")


def step_doc_choice() -> None:
    st.markdown("### Vyberte způsob zadání údajů pacienta")
    st.markdown(CARD_CSS, unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    _card(c1, "wiz_choice_quick", "⚡ Rychlé zadání",
          "Jeden řádek bez scrollování: aplikace se ptá vždy jen na další potřebný parametr.", _go_quick, ())
    _card(c2, "wiz_choice_form", "📝 Vyplnit formulář",
          "Krokový formulář: diagnóza → rozsah onemocnění → předchozí léčba → biomarkery.", _go, ("detail",))
    _card(c3, "wiz_choice_report", "📄 Vložit lékařskou zprávu",
          "Text, soubor nebo fotografie. Parametry se z anonymizované zprávy předvyplní automaticky.", _go, ("report",))


def _load_report(prefix: str) -> None:
    """Callback: take the upload, photo or typed text, convert locally and continue to review."""
    nonce = ss.get("wiz_nonce", 0)
    mode = ss.get(f"{prefix}_mode", "file")
    upload, photo = ss.get(f"{prefix}_upload_{nonce}"), ss.get(f"{prefix}_photo_{nonce}")
    typed = ss.get(f"{prefix}_draft", "")
    try:
        if mode == "file" and upload is not None:
            text = report_input.text_from_upload(upload.name, upload.getvalue())
        elif mode == "photo" and photo is not None:
            text = report_input.text_from_upload("foto.jpg", photo.getvalue())
        elif mode == "text" and typed.strip():
            text = typed
        else:
            raise OcrError({"file": "Nejprve vyberte soubor se zprávou.", "photo": "Nejprve zprávu vyfoťte.",
                            "text": "Vložte text zprávy."}[mode])
    except OcrError as exc:
        ss["wiz_error"] = str(exc)
        return
    finally:
        upload = photo = None
    ss["wiz_nonce"] = nonce + 1
    ss.pop(f"{prefix}_draft", None)
    ss[f"{prefix}_report_text"] = text
    ss.pop(f"{prefix}_report_ok", None)
    _go("review")


def _load_demo(prefix: str) -> None:
    name = ss.get(f"{prefix}_demo")
    if not name:
        return
    t0 = ss.get("doc_t0")
    reset(prefix)
    if t0:
        ss["doc_t0"] = t0
    profile = CASES[name]()
    apply_profile(prefix, profile)
    ss[f"{prefix}_profile"] = profile
    ss[f"{prefix}_prefilled"] = True
    _go("detail")


def step_report() -> None:
    p, lay = _prefix(), _lay()
    nonce = ss.get("wiz_nonce", 0)
    st.markdown("### " + ("Nahrajte svou lékařskou zprávu" if lay else "Vložte lékařskou zprávu"))
    st.info("Fotografie i soubor se převádějí na text **lokálně na serveru aplikace** (nic se neukládá). "
            "V dalším kroku text zkontrolujete a odstraníte osobní údaje – teprve potom se odešle k automatickému "
            "vyhodnocení do cloudové služby OpenAI.")
    modes = {"file": "📁 Nahrát soubor", "text": "✍️ Vložit text", "photo": "📷 Vyfotit zprávu"}
    if not lay:  # doctors usually paste text from the hospital system
        modes = {"text": modes["text"], "file": modes["file"], "photo": modes["photo"]}
    mode = st.radio("Jak chcete zprávu vložit?", list(modes), format_func=modes.get, key=f"{p}_mode", horizontal=True)
    if mode == "file":
        st.file_uploader("Textový soubor (.txt), PDF nebo fotografie / scan (JPG, PNG), max. 10 MB",
                         type=report_input.UPLOAD_TYPES, key=f"{p}_upload_{nonce}")
    elif mode == "text":
        st.text_area("Text zprávy", key=f"{p}_draft", height=220, max_chars=MAX_REPORT_CHARS,
                     placeholder="Zkopírujte sem text lékařské zprávy…")
    else:
        # Rendered only on demand – camera_input switches the camera on as soon as it is shown.
        st.camera_input("Vyfoťte zprávu tak, aby byl text ostrý a celý v záběru", key=f"{p}_photo_{nonce}")
    if ss.get("wiz_error"):
        st.error(ss.pop("wiz_error"))
    c1, c2 = st.columns(2)
    if lay:
        c1.button("Nemám zprávu – vyplním údaje ručně", on_click=_go, args=("detail",), key="wiz_no_report", width="stretch")
    c2.button("Pokračovat ke kontrole textu →", on_click=_load_report, args=(p,), type="primary", key="wiz_load",
              width="stretch")
    if os.getenv("TRIALINK_DEMO") != "1":
        return
    with st.expander("🧪 Ukázkové případy (demo bez AI)"):
        st.selectbox("Testovací kazuistika", [None, *CASES], key=f"{p}_demo",
                     format_func=lambda v: "— vybrat —" if v is None else v)
        st.button("Načíst ukázkový případ", on_click=_load_demo, args=(p,), key="wiz_demo")


def _mask(key: str) -> None:
    ss[key] = anonymize.mask(ss.get(key, ""))


def _extract(p: str, then: str) -> None:
    text = ss.get(f"{p}_report_text", "")
    try:
        with st.spinner("Vyhodnocuji zprávu…"):
            result = extraction.extract_profile(text)
    except ExtractionError as exc:
        st.error(str(exc))
        return
    finally:
        text = None
    t0 = ss.get("doc_t0")
    reset(p)
    if t0:
        ss["doc_t0"] = t0
    apply_profile(p, result.profile)
    ss[f"{p}_profile"] = result.profile
    ss[f"{p}_prefilled"] = True
    ss[f"{p}_dropped"] = result.dropped
    ss["wiz_edit_step"] = "detail"
    _go(then)
    st.rerun()


def step_review() -> None:
    p, lay = _prefix(), _lay()
    key = f"{p}_report_text"
    st.markdown("### Zkontrolujte text zprávy")
    if key not in ss:
        st.info("Text zprávy byl po vyhodnocení z paměti odstraněn. Pro nové vyhodnocení se vraťte a vložte zprávu znovu.")
        return
    st.write("Opravte případné chyby rozpoznávání a **odstraňte jméno, rodné číslo, adresu, telefon a další osobní údaje**.")
    text = st.text_area("Text zprávy", key=key, height=320, max_chars=MAX_REPORT_CHARS)
    found = anonymize.find(text)
    if found:
        st.warning("Text pravděpodobně obsahuje osobní údaje: " + ", ".join(f"{k} ({n}×)" for k, n in found.items()))
        st.button("🛡️ Nahradit je značkou [ANONYMIZOVÁNO]", on_click=_mask, args=(key,), key="wiz_mask")
    else:
        st.success("Bylo anonymizováno. **Přesto text prosím projděte a ověřte, že neobsahuje žádné citlivé údaje** – "
                   "jméno, rodné číslo, datum narození, adresu, telefon, e-mail ani číslo pojištěnce.")
    ok = st.checkbox("Zkontroloval(a) jsem text a neobsahuje jméno ani jiné identifikační údaje. "
                     "Souhlasím s jeho odesláním do cloudové služby k vyhodnocení.", key=f"{p}_report_ok")
    ready = ok and bool(text.strip())
    st.markdown(SEARCH_CSS, unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    label_detail = "📝 Zkontrolovat vyčtené údaje →" if lay else "📝 Zkontrolovat parametry →"
    if c1.button(label_detail, disabled=not ready, key="wiz_to_detail", width="stretch"):
        _extract(p, "detail")
    if c2.button("🧬 Rovnou vyhledat studie", disabled=not ready, type="primary", key="wiz_to_results", width="stretch"):
        _extract(p, "results")
    if not lay:
        st.caption("Vyhodnocení textu provádí jazykový model; údaje bez doslovné citace ze zprávy se zahodí.")


def step_detail() -> None:
    p, lay = _prefix(), _lay()
    stored = ss.get(f"{p}_profile")
    # Widget state is dropped while other steps are shown; the doctor's profile may also have been changed in quick
    # entry. Re-apply only on a real difference – apply_profile resets the data editors.
    if stored is not None and (f"{p}_extent" not in ss or (not lay and stored != ss.get(f"{p}_form_profile"))):
        apply_profile(p, stored)
    st.markdown("### " + ("Údaje o vaší nemoci" if lay else "Parametry pacienta"))
    if ss.get(f"{p}_dropped"):
        st.info("Vynecháno (údaj nebyl doložen citací ze zprávy): " + ", ".join(ss[f"{p}_dropped"]))
    if stored is not None and (lay or ss.get(f"{p}_prefilled")):
        st.caption("Formulář je předvyplněný ze zprávy – zkontrolujte jej, automatické vyplnění může obsahovat chyby.")
    profile = profile_form(p, lay=lay)
    if not lay:  # single source of truth shared with quick entry (RAM only)
        ss[f"{p}_profile"] = ss[f"{p}_form_profile"] = profile
    st.divider()
    st.markdown(SEARCH_CSS, unsafe_allow_html=True)
    if lay:
        search = st.button("🧬 Vyhledat klinické studie", type="primary", key="wiz_search")
    else:
        c1, c2 = st.columns([3, 1], vertical_alignment="center")
        search = c1.button("🧬 Vyhledat klinické studie", type="primary", key="wiz_search")
        if c2.button("⚡ Přepnout na rychlé zadání", key="wiz_to_quick", width="stretch"):
            _go("quick")
            st.rerun()
    if search:
        ss[f"{p}_profile"] = profile
        ss["wiz_edit_step"] = "detail"
        _go("results")
        st.rerun()


def step_quick() -> None:
    """Doctor-only compact entry: one row whose active parameter changes as the profile is filled in."""
    if ss.get("wiz_role") != DOCTOR:
        step_detail()
        return
    quick_entry.sync_profile("doc", ss.get("doc_profile"))
    profile = quick_entry.quick_form("doc")
    ss["doc_profile"] = profile
    st.markdown(SEARCH_CSS, unsafe_allow_html=True)
    c1, c2 = st.columns([1, 3], vertical_alignment="center")
    if c1.button("📝 Plný formulář", key="wiz_quick_full", width="stretch"):
        _go("detail")
        st.rerun()
    if c2.button("🧬 Vyhledat klinické studie", type="primary", key="wiz_search", width="stretch"):
        ss["wiz_edit_step"] = "quick"
        _go("results")
        st.rerun()


# Doctor result groups: (key, icon, label, colour) – one coloured list, excluded studies are not shown.
DOC_GROUPS = (("cand", "🟢", "Odpovídá", "#1f8a70"), ("verify", "🟡", "K ověření", "#d4a017"),
              ("other", "🟠", OTHER_STUDIES, "#e07b16"))


def _doc_results_css() -> None:
    rules = "".join(
        f'[class*="st-key-doc_res_{g}_"] {{ border-left: 7px solid {c} !important; background: {c}12 !important; '
        f'border-radius: .6rem !important; padding: .45rem .8rem !important; gap: .3rem !important; }}'
        for g, _, _, c in DOC_GROUPS)
    st.markdown(f"<style>{rules} .st-key-doc_reslist {{ gap: .5rem !important; }}</style>", unsafe_allow_html=True)


def _safe_key(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", text)


def _select_referral(study_id: str) -> None:
    ss["doc_referral"] = study_id


def _close_referral() -> None:
    ss.pop("doc_referral", None)


def _doctor_results(studies, profile) -> list:
    results = match(studies, profile)
    groups = {s: [r for r in results if r.status == s] for s in (STATUS_CANDIDATE, STATUS_VERIFY, STATUS_EXCLUDED)}
    items = {"cand": groups[STATUS_CANDIDATE],
             "verify": [r for r in groups[STATUS_VERIFY] if r.targeted],
             "other": [r for r in groups[STATUS_VERIFY] if not r.targeted]}
    sections = [(label, items[g]) for g, _, label, _ in DOC_GROUPS]
    _doc_results_css()
    if any(items.values()):
        st.markdown(" · ".join(f"{icon} {label} **{len(items[g])}**" for g, icon, label, _ in DOC_GROUPS))
    else:
        st.info("Žádná vhodná ani k ověření vhodná studie pro zadané údaje.")
    i = 0
    with st.container(key="doc_reslist"):
        for g, icon, label, _ in DOC_GROUPS:
            for r in items[g]:
                sid = r.study.study_id
                with st.container(border=True, key=f"doc_res_{g}_{_safe_key(sid)}_{i}"):
                    render_result(r, profile=profile, collapsed=True, icon=icon, group_label=label)
                    st.button("✉️ Připravit referral", key=f"doc_ref_{sid}", on_click=_select_referral, args=(sid,))
                    if ss.get("doc_referral") == sid:
                        _referral_section(r, profile)
                i += 1
    st.caption(f"Vyloučené studie ({len(groups[STATUS_EXCLUDED])}) nejsou zobrazeny.")
    return sections


def _referral_section(selected, profile) -> None:
    p = "doc"
    st.markdown(f"#### ✉️ Referral – {selected.study.acronym}")
    st.button("✕ Zavřít", key=f"{p}_ref_close", on_click=_close_referral)
    ref = build_referral(selected, profile)
    for c in selected.study.contacts:
        ext = f" · kl. {c.extension}{' (simulováno)' if c.extension_simulated else ''}" if c.extension else ""
        st.markdown(f"- **{c.role}** {c.name or '❓ jméno chybí'} <{c.email or '❓ e-mail chybí'}>{ext}")
    if ref.blockers:
        st.error("Odeslání blokováno: " + "; ".join(ref.blockers))
    sid = selected.study.study_id
    subj_key, body_key = f"{p}_ref_subject_{sid}", f"{p}_ref_body_{sid}"
    if st.button("↺ Obnovit text ze šablony", key=f"{p}_ref_reset_{sid}") or subj_key not in ss:
        ss[subj_key], ss[body_key] = ref.subject, ref.body
    subject = st.text_input("Předmět", key=subj_key)
    body = st.text_area("Text zprávy (lze upravit)", key=body_key, height=360)
    st.caption("Nevkládejte jméno, rodné číslo ani jiné identifikační údaje pacienta. Odeslání je v prototypu pouze simulované.")
    if st.button("Odeslat (simulace)", type="primary", key=f"{p}_ref_send",
                 disabled=bool(ref.blockers) or not body.strip()):
        ref.subject, ref.body = subject, body
        st.success(simulate_send(ref))
        with st.expander("Text, který by byl odeslán", expanded=True):
            st.text(f"Předmět: {ref.subject}\n\n{ref.body}")
        st.metric("Čas od začátku do odeslání referral", f"{time.time() - ss.get(f'{p}_t0', time.time()):.0f} s")
        for k in (f"{p}_referral", subj_key, body_key):
            ss.pop(k, None)


def _patient_results(studies, profile) -> list:
    results = [r for r in match(studies, profile) if r.status in (STATUS_CANDIDATE, STATUS_VERIFY)]
    targeted = [r for r in results if r.targeted]
    others = [r for r in results if not r.targeted]
    if not profile.icd10:
        st.info("Chybí diagnóza – doplňte ji v údajích o nemoci, pak vám ukážeme odpovídající studie.")
    elif not targeted:
        st.info("Pro zadané údaje jsme nenašli studii zaměřenou na vaši diagnózu. Zeptejte se svého onkologa, "
                "zda by pro vás nebyla vhodná jiná studie.")
    for r in targeted:
        with st.container(border=True):
            render_result(r, lay=True, profile=profile)
            sc = [c for c in r.study.contacts if c.role == "SC" and c.email]
            if sc:
                st.caption("Kontakt na studijní tým (doporučujeme nejprve probrat s ošetřujícím lékařem): "
                           + ", ".join(f"{c.name} <{c.email}>" for c in sc))
    with st.expander(f"{OTHER_STUDIES} ({len(others)})"):
        st.caption("Tyto studie (často fáze I) nejsou zaměřené na jednu konkrétní diagnózu. Jejich vhodnost může posoudit jen lékař.")
        for r in others:
            st.markdown(f"- **{r.study.acronym}** – {r.study.indication}")
    return [("Studie zaměřené na vaši diagnózu", targeted), (OTHER_STUDIES, others)]


def _pdf_download(profile, sections, lay: bool) -> None:
    try:
        from trialink.report_pdf import build_pdf
        data = build_pdf(profile, sections, lay=lay)
    except ImportError:
        st.caption("Export do PDF není dostupný (chybí knihovna fpdf2).")
        return
    st.download_button("📄 Stáhnout výsledky (PDF)", data=data, mime="application/pdf", key="wiz_pdf",
                       file_name=f"trialink-vysledky-{date.today():%Y-%m-%d}.pdf")


def step_results(studies) -> None:
    p, lay = _prefix(), _lay()
    profile = ss.get(f"{p}_profile")
    st.markdown("### " + ("Studie, o kterých si můžete promluvit s lékařem" if lay else "Výsledky párování"))
    if profile is None:
        st.info("Nejprve zadejte údaje o nemoci.")
        return
    edit_step = "detail" if lay else ss.get("wiz_edit_step", "detail")
    st.button("✏️ Upravit údaje", on_click=_go, args=(edit_step,), key="wiz_edit")
    if lay:
        sections = _patient_results(studies, profile)
    else:
        sections = _doctor_results(studies, profile)
        st.caption(f"Datum hodnocení: {date.today():%d. %m. %Y}")
    st.divider()
    _pdf_download(profile, sections, lay)


def _scroll_to_top() -> None:
    # The counter makes the snippet unique per step change, otherwise Streamlit would not re-run it.
    components.html(f"""<script>/* {ss.get('wiz_scroll_top', 0)} */
      const d = window.parent.document;
      (d.querySelector('[data-testid="stMain"]') || d.scrollingElement).scrollTo(0, 0);
      window.parent.scrollTo(0, 0);</script>""", height=0)


def run(studies) -> None:
    step = ss.get("wiz_step", "role")
    if step != "role":
        _nav()
    if ss.get("wiz_scroll_top", 0) != ss.get("wiz_scrolled", 0):
        ss["wiz_scrolled"] = ss.get("wiz_scroll_top", 0)
        _scroll_to_top()
    {"role": step_role, "doc_choice": step_doc_choice, "report": step_report, "review": step_review,
     "detail": step_detail, "quick": step_quick}.get(step, lambda: step_results(studies))()
    disclaimer(lay=_lay() and step != "role")
