# Robinsoni
Rackathon 2026

# Team
Robinson 1

Robinson 2

## Lokální OCR zpráv

V lékařském i pacientském rozhraní lze načíst JPG/PNG, PDF nebo UTF-8 `.txt`.
Tlačítko **Načíst text do zprávy (lokálně)** vyplní upravitelnou kolonku
**Anonymizovaná zpráva**; nevolá OpenAI. Uživatel opraví rozpoznaný text a
odstraní identifikátory. OCR samo zprávu neanonymizuje.
Až tlačítko **Extrahovat parametry** (lékař) nebo **Vyplnit formulář ze zprávy**
(pacient) odešle obsah této kolonky do současné cloudové extrakce.
Fotografie ani PDF se do OpenAI neposílají.

- Nainstalujte Python závislosti: `python -m pip install -r requirements.txt`.
- Na serveru aplikace nainstalujte Tesseract OCR s českými jazykovými daty `ces`.
  OCR používá výhradně češtinu (`-l ces`) a LSTM engine (`--oem 1`).
  Přesnější model `ces.traineddata` z `tesseract-ocr/tessdata_best` je přibalen
  v `trialink/data/ocr` společně s licencí a původem modelu. Použije se automaticky
  bez stahování při zpracování fotografie. Vlastní model lze nastavit adresářem
  `TESSDATA_PREFIX`. Přesnější model může být pomalejší.
  Na Windows musí být `tesseract.exe` na `PATH`, případně nastavte `TESSERACT_CMD`
  v `.env` na jeho úplnou cestu. Dostupnost jazyků ověří `tesseract --list-langs`.
- Pro Streamlit Community Cloud jsou systémové balíčky uvedeny v `packages.txt`.
- PDF: použije se textová vrstva každé stránky; stránky bez textu se vykreslí
  na 300 DPI a přečtou českým OCR. Volba **Vynutit OCR všech stránek PDF**
  ignoruje textovou vrstvu (například nekvalitní dřívější OCR).
  Zcela bílé stránky se přeskočí; selhání jiné stránky odmítne celý import,
  aby se do kolonky nevložila neúplná zpráva. Původní text kolonky zůstane zachován.
- Limity: 10 MB, 20 megapixelů na obrázek/stránku, 60 sekund na OCR stránky,
  40 000 znaků zprávy. PDF navíc nejvýše 10 stránek a 120 sekund na celý dokument,
  včetně načítání a vykreslování. Zaheslovaná PDF a HEIC nejsou podporovány.
- PDF zpracovává `pypdfium2` (text i vykreslování), bez potřeby Poppleru.
  Dokument běží v odděleném lokálním procesu s časovým limitem, stránky postupně
  v RAM. Při nasazení stačí znovu nainstalovat Python závislosti z `requirements.txt`.

OCR používá lokální proces Tesseractu s obrazem na standardním vstupu a textem
na standardním výstupu, bez dočasných souborů. Fotografie, PDF a text zůstávají pouze
v RAM, nejsou logovány ani cachovány. Text v kolonce podléhá stejnému vymazání
a kontrole nečinnosti jako ostatní údaje relace a při odeslání se kolonka vyčistí.
Požadavek na lokální LLM stále není splněn: následná extrakce používá OpenAI cloud.

## Nasazení na Streamlit Community Cloud

1. Nahrajte změny do GitHubu, včetně `requirements.txt`, `pyproject.toml`,
   `packages.txt` a celé složky `trialink/data/ocr` (model má přibližně 11 MB).
   Neodesílejte `.env`, pacientské zprávy, fotografie ani lokální složku `data`.
2. V Community Cloud vyberte repozitář a větev; jako **Main file path** zadejte
   `app/Home.py`. Zvolte Python **3.12**.
3. Do **Settings → Secrets** vložte konfiguraci podle
   `.streamlit/secrets.toml.example`: skutečné připojení k IRIS a případně
   OpenAI API klíč a heslo správce ve formě hashe. IRIS musí být ze serveru
   Streamlitu dostupný a obsahovat importované referenční studie ve schématu
   `TriaLink`; lokální export SONIS se do nasazení nepřibaluje.
4. `TESSERACT_CMD`, `TESSDATA_PREFIX` a lokální `TRIALINK_EXPORT` do cloudových
   Secrets nekopírujte. Tesseract se nainstaluje přes `packages.txt`; cesta
   k přibalenému českému modelu se odvodí od umístění aplikace.
5. Nasaďte aplikaci, případně zvolte **Reboot app**. Vyzkoušejte anonymní testovací
   JPG/PNG: lokální import musí vyplnit kolonku, aniž by se zavolalo OpenAI.
   Poté ověřte samostatné extrakční tlačítko.

Na Community Cloud znamená „lokální OCR“ proces na serveru Streamlitu,
nikoli na počítači uživatele nebo uvnitř nemocnice. Fotografie tento server
opustit nemusí, ale už nahráním odchází mimo nemocnici. Používejte pouze
anonymizované snímky. Cloudová infrastruktura není řešením požadavku
na zpracování výhradně uvnitř nemocnice.
