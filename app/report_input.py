"""Two explicit steps: local import/OCR, then user-approved cloud extraction."""
from __future__ import annotations

import streamlit as st

from trialink.extraction import MAX_REPORT_CHARS
from trialink.ocr import MAX_IMAGE_BYTES, OcrError, image_to_text


def _submit_report(prefix: str) -> None:
    st.session_state[f"{prefix}_submitted_report"] = st.session_state.get(f"{prefix}_report_text", "")
    st.session_state[f"{prefix}_report_text"] = ""


def report_input(prefix: str, *, lay: bool = False) -> str | None:
    text_key = f"{prefix}_report_text"
    st.warning("Nahrávejte pouze anonymizované snímky. Na Streamlit Community Cloud se fotografie "
               "nahrává na server mimo nemocnici; OCR ji samo neanonymizuje.")
    st.caption("1 · Volitelně načtěte fotografii nebo textový soubor. OCR běží lokálně na serveru aplikace; "
               "fotografie ani text se v tomto kroku neposílají do OpenAI. Nic se neukládá na disk.")
    with st.form(f"{prefix}_local_import", clear_on_submit=True):
        upload = st.file_uploader("Fotografie zprávy (JPG, PNG) nebo textový soubor (.txt)",
                                  type=["jpg", "jpeg", "png", "txt"], key=f"{prefix}_report_upload")
        load = st.form_submit_button("Načíst text do zprávy (lokálně)")
    if load:
        try:
            if upload is None:
                raise OcrError("Nejprve vyberte fotografii nebo textový soubor.")
            if upload.size > MAX_IMAGE_BYTES:
                raise OcrError("Soubor je příliš velký. Použijte nejvýše 10 MB.")
            if upload.name.lower().endswith(".txt"):
                try:
                    imported = upload.getvalue().decode("utf-8-sig")
                except UnicodeDecodeError:
                    raise OcrError("Textový soubor musí mít kódování UTF-8.") from None
            else:
                with st.spinner("Rozpoznávám text lokálně…"):
                    imported = image_to_text(upload.getvalue())
            if not imported.strip():
                raise OcrError("Soubor neobsahuje text.")
            if len(imported) > MAX_REPORT_CHARS:
                raise OcrError("Rozpoznaný text přesahuje 40 000 znaků. Nahrajte kratší část zprávy.")
            st.session_state[text_key] = imported
            st.success("Text je načten do kolonky níže. Zkontrolujte jej a odstraňte osobní údaje. "
                       "Do OpenAI zatím nebyl odeslán.")
        except OcrError as exc:
            st.error(str(exc))
        finally:
            upload = None
    st.caption("2 · Opravte chyby rozpoznávání a odstraňte jméno, rodné číslo, adresu a další osobní údaje. "
               "OCR zprávu neanonymizuje. Text zůstává pouze v paměti relace.")
    st.text_area("Anonymizovaná zpráva", height=180, max_chars=MAX_REPORT_CHARS, key=text_key)
    st.button("Vyplnit formulář ze zprávy" if lay else "Extrahovat parametry",
              on_click=_submit_report, args=(prefix,), key=f"{prefix}_extract_submit")
    return st.session_state.pop(f"{prefix}_submitted_report", None)
