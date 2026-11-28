# Vyhodnocení shody projektu Robinsoni se `Zadani.md` a plán dopracování

## Shrnutí zjištění

Projekt [Robinsoni](file:///c:/test/Robinsoni) vznikl postupnou implementací podle [prompt.md](file:///c:/test/Robinsoni/prompt.md) (etapy 1–7 jsou dokončené), nikoli ještě v plném rozsahu [Zadani.md](file:///c:/test/Zadani.md) ani navazujících klinických požadavků v [pozadavky_na_kontrolu.md](file:///c:/test/Robinsoni/pozadavky_na_kontrolu.md).

**Aktuální stav vůči `Zadani.md`:** projekt **splňuje technický základ** (lokální DICOM Storage SCP listener bez závislosti na cloudu, normalizace standardních metadat, seskupování sérií, verzovaný YAML validační engine v režimu `DEMO`, SQLite historie a jednoduché PySide6 semaforové GUI), ale **nesplňuje klíčové funkční a klinické body `Zadani.md`**:

| Požadavek z [Zadani.md](file:///c:/test/Zadani.md) | Stav v `Robinsoni` | Hlavní mezera v kódu |
|---|---|---|
| Průběžný lokální příjem DICOM a párování ke studii/pacientovi (§4) | **Splněno** | [listener.py](file:///c:/test/Robinsoni/backend/mr_quality_monitor/dicom/listener.py), [storage.py](file:///c:/test/Robinsoni/backend/mr_quality_monitor/dicom/storage.py) a [series_aggregator.py](file:///c:/test/Robinsoni/backend/mr_quality_monitor/analysis/series_aggregator.py) fungují spolehlivě (omezeno na nekomprimované jednorámcové MR/SC). |
| Provoz on-site bez cloudu (§4) | **Splněno** | Vše běží lokálně nad SQLite a lokálním diskem ([app_config.yaml](file:///c:/test/Robinsoni/config/app_config.yaml)). |
| Analýza DICOM hlaviček dle klinického protokolu prostaty / mozku (§4, §5) | **Částečně** | [models.py](file:///c:/test/Robinsoni/backend/mr_quality_monitor/protocols/models.py#L158) povoluje pouze `mode: Literal["DEMO"]`; [run_windows.ps1](file:///c:/test/Robinsoni/tools/run_windows.ps1#L70) spouští pouze ukázkový [demo_mr.yaml](file:///c:/test/Robinsoni/config/protocols/demo_mr.yaml) (`T2_DEMO`). Chybí protokoly pro prostatu a mozek i privátní tagy pro `b-value` ([siemens.py](file:///c:/test/Robinsoni/backend/mr_quality_monitor/vendors/siemens.py)). |
| Obrazová analýza kvality snímků a rozšiřitelná detekce artefaktů (§4, §5) | **Nesplněno** | [metadata_parser.py](file:///c:/test/Robinsoni/backend/mr_quality_monitor/dicom/metadata_parser.py#L22) čte pouze hlavičky (`stop_before_pixels=True`), [validation_engine.py](file:///c:/test/Robinsoni/backend/mr_quality_monitor/analysis/validation_engine.py#L213) vrací vždy `image_status="GRAY"` a `clinical_quality_evaluated=False`. |
| Semaforové GUI v zorném poli obsluhy (§4, §5) | **Částečně** | [main_window.py](file:///c:/test/Robinsoni/backend/mr_quality_monitor/gui/main_window.py) má semafor (`GREEN`/`ORANGE`/`RED`/`GRAY`), ale bez obrazových výsledků končí reálná data převážně jako `GRAY`, chybí kompaktní režim "always-on-top" vedle NISu a chybí tlačítko pro uvolnění chybné série do PACS. |
| Automatické propouštění bezchybných sekvencí do PACS a ruční potvrzení chybných (§4, §5) | **Nesplněno** | Chybí konfigurace cílového PACS i automatický C-STORE SCU forwarder ([sender.py](file:///c:/test/Robinsoni/backend/mr_quality_monitor/dicom/sender.py) slouží jen k manuálnímu odeslání testovacích souborů do vlastního listeneru). |
| Závěrečný certifikát vyšetření (PDF / DICOM SR / SC) odeslaný do PACS (§4, §5) | **Nesplněno** | Nevzniká žádný certifikát ani exportní objekt do PACS. |
| Ukládání kvality a typů artefaktů pro statistické zpracování (§5 + [pozadavky_na_kontrolu.md](file:///c:/test/Robinsoni/pozadavky_na_kontrolu.md)) | **Částečně** | [models.py](file:///c:/test/Robinsoni/backend/mr_quality_monitor/database/models.py) ukládá historii běhů a metadatových odchylek, ale neukládá typy artefaktů, `OperatorsName` `(0008,1070)`, hmotnost/BMI ani nenabízí statistické přehledy. |

---

## User Review Required

> [!IMPORTANT]
> **Rozsah a směr dopracování:** Projekt `Robinsoni` záměrně drží přísné oddělení `mode: DEMO` a neobsahuje žádná klinická pravidla ani pixelovou analýzu. Schválení tohoto plánu znamená rozšíření aplikace z DEMO monitoru metadat na plnohodnotného **MR.buddy** dle [Zadani.md](file:///c:/test/Zadani.md) (a případně MOÚ pravidel z [pozadavky_na_kontrolu.md](file:///c:/test/Robinsoni/pozadavky_na_kontrolu.md)).

> [!WARNING]
> **Klinická validace a PACS integrace:** Automatické odesílání do PACS a klinické prahy artefaktů (pohyb, SNR, geometrické prahy) vyžadují konfiguraci konkrétního PACS uzlu (`host`, `port`, `called_aet`) a schválení prahových hodnot odborníkem (radiologem/fyzikem) před produkčním nasazením.

---

## Open Questions

> [!IMPORTANT]
> Před zahájením implementace prosím potvrďte následující body:
> 1. **Klinický rozsah protokolů:** Máme implementovat pouze protokoly pro **prostatu** (podle konkrétních parametrů v [pozadavky_na_kontrolu.md](file:///c:/test/Robinsoni/pozadavky_na_kontrolu.md) + data v `c:\test\DICOMDIR_MR50102`), nebo i základní protokol pro **mozek** (který je zmíněn v [Zadani.md](file:///c:/test/Zadani.md), ale nemá v repozitáři specifikaci sekvencí)?
> 2. **Způsob detekce obrazových artefaktů:** Má být obrazová analýza postavena na deterministických metrikách nad `pixel_array` (např. background/foreground SNR, gradientová/frekvenční ostrost a detekce ghostingu pro pohybové artefakty, mezirezová kontinuita u multi-slabů, kontrola pokrytí vůči `AA_Prostate_Segm`), nebo plánujete připojit i lokální ML/ONNX model?
> 3. **Formát závěrečného certifikátu do PACS:** Preferujete **DICOM Secondary Capture** (obrázek se souhrnnou tabulkou), **DICOM Encapsulated PDF**, nebo **DICOM Structured Report (SR)**?
> 4. **Privátní tagy pro b-value a parametry pacienta:** V `DICOMDIR_MR50102` jsou data Siemens (`MAGNETOM Vida`). Máme v `SiemensAdapter` napojit čtení b-value primárně z `(0019,100C)` a záložně `(0021,1105)` s konfigurací pořadí v YAML (dle [pozadavky_na_kontrolu.md](file:///c:/test/Robinsoni/pozadavky_na_kontrolu.md)), a případně ověřit i tagy pro hmotnost/BMI na dodaných datech?
> 5. **PACS routing a finální uzavření vyšetření:** Jak pozná aplikace, že vyšetření skončilo (tlačítko obsluhy *"Uzavřít vyšetření a odeslat certifikát"*, nebo konfigurovatelný časový timeout po poslední přijaté sérii)?

---

## Proposed Changes

Návrh je rozdělen do 6 modulárních komponent tak, aby zachoval stávající architekturu a všechny existující testy (`227` testů dle [implementation_log.md](file:///c:/test/Robinsoni/docs/implementation_log.md)).

### 1. Rozšíření metadat, vendor adaptérů a detekce dokončení série

Doplnění extrakce polí potřebných pro [Zadani.md](file:///c:/test/Zadani.md) a [pozadavky_na_kontrolu.md](file:///c:/test/Robinsoni/pozadavky_na_kontrolu.md) bez prolomení ochrany PHI v logování.

#### [MODIFY] [siemens.py](file:///c:/test/Robinsoni/backend/mr_quality_monitor/vendors/siemens.py)
- Přidat konfigurovatelný extraktor b-value z privátních tagů `(0019,100C)` a `(0021,1105)` s respektováním priority definované v konfiguraci aplikace (`b_value_tag_order`).
- Doplnit podporu pro výpočet odvozených geometrických polí (FOV z `PixelSpacing * Rows/Columns`, efektivní rozlišení ve směru fáze a frekvence, `SliceGap = SpacingBetweenSlices - SliceThickness`).

#### [MODIFY] [metadata_parser.py](file:///c:/test/Robinsoni/backend/mr_quality_monitor/dicom/metadata_parser.py)
- Rozšířit `FIELDS` o `OperatorsName` `(0008,1070)`, `PatientWeight` `(0010,1030)`, `PatientSize` `(0010,1020)`, `PatientAge` `(0010,1010)` (pro anonymizované statistiky) a vypočítaná pole `FieldOfView`, `SliceGap`, `PhaseResolution`, `FrequencyResolution`.

---

### 2. Klinické protokoly a validační pravidla na úrovni vyšetření

#### [MODIFY] [models.py](file:///c:/test/Robinsoni/backend/mr_quality_monitor/protocols/models.py)
- Povolit `mode: Literal["DEMO", "CLINICAL"]` s povinným uvedením schvalovatele/zdroje u `CLINICAL` protokolů.
- Přidat podporu studie-úrovňových pravidel (`study_rules`):
  - `min_valid_from_group` (např. alespoň 2 validní T2W sekvence + kontrola přítomnosti axiální `TRA`),
  - `fallback_sequences` (pokud chybí nebo selže `ep2d_diff_zoomit_*`, ověřit přítomnost a validitu `RESOLVE`),
  - `geometry_alignment` (porovnání `ImagePositionPatient`, `ImageOrientationPatient`, rozteče a směru akvizice mezi `DWI` a `t2_tse_tra` s konfigurovatelnou tolerancí),
  - `anatomical_coverage` (ověření pokrytí segmentace `AA_Prostate_Segm`).

#### [NEW] prostate_mou.yaml (`config/protocols/prostate_mou.yaml`)
- Definovat protokol dle [pozadavky_na_kontrolu.md](file:///c:/test/Robinsoni/pozadavky_na_kontrolu.md):
  - T2W: `t2_tse_sag_DRB`, `t2_tse_cor`, `t2_tse_tra` (tloušťka ≤ 3 mm, gap 0 mm, FOV 120–200 mm, rozlišení ≤ 0,7 mm).
  - DWI: `low_b` (0–100), `intermediate_b` (800–1000), `high_b` (≥ 1400), `adc` (`*ez_b50_800`), tloušťka ≤ 4 mm, gap 0 mm, fallback `RESOLVE`.
  - Pomocné série: `localizer`, `AA_Prostate_Segm` (SN 2–7).

#### [MODIFY] [validation_engine.py](file:///c:/test/Robinsoni/backend/mr_quality_monitor/analysis/validation_engine.py)
- Napojit vyhodnocení studie-úrovňových pravidel (`T2W Compliance`, `DWI + RESOLVE Fallback`, `DWI–T2 TRA Geometry Alignment`, `B-Value Range Validator`).

---

### 3. Rozšiřitelný modul obrazové analýzy a detekce artefaktů (`image_quality`)

Nový modul čistě oddělený od DICOM I/O, spouštěný ve workeru nad uloženými soubory.

#### [NEW] image_quality (`backend/mr_quality_monitor/analysis/image_quality/`)
- `base.py`: Rozhraní `ArtifactDetector` (`detector_id`, `artifact_type`, `supported_modalities`, `evaluate(series_slices) -> ArtifactFinding`), aby bylo možné přidávat nové detektory bez zásahu do enginu.
- `detectors.py`: Počáteční sada deterministických detektorů na `numpy` (z `pydicom.Dataset.pixel_array`):
  - **Pohybový artefakt / ghosting:** analýza energie mimo anatomický objekt ve směru fázového kódování a rozmazání hran (gradient/Laplacian variance).
  - **Snížený SNR:** poměr signálu ROI ku šumu v pozadí.
  - **Artefakty rozhraní / postprocessing / multi-slab návaznost:** skoky intenzity a ostré diskontinuity mezi sousedními řezy či slaby.
- Výsledky se v `validation_engine.py` promítnou do `image_status` (`GREEN`/`ORANGE`/`RED`) a seznamu zjištěných typů artefaktů místo stávajícího pevného `GRAY`.

---

### 4. PACS Forwarding a generování certifikátu vyšetření

#### [MODIFY] [config.py](file:///c:/test/Robinsoni/backend/mr_quality_monitor/config.py) & [app_config.yaml](file:///c:/test/Robinsoni/config/app_config.yaml)
- Přidat volitelnou sekci `pacs`: `enabled`, `host`, `port`, `called_aet`, `calling_aet`, `auto_forward_clean` (`bool`), `require_manual_approval_on_issues` (`bool`).

#### [NEW] pacs_forwarder.py (`backend/mr_quality_monitor/services/pacs_forwarder.py`)
- Samostatná fronta v DB (`PacsExportJob`) zajišťující spolehlivé C-STORE SCU odesílání schválených sérií do PACS:
  - `GREEN` série → automatické zařazení k odeslání (pokud `auto_forward_clean: true`).
  - `ORANGE` / `RED` série → stav `awaiting_operator_approval`; odeslání až po explicitním pokynu obsluhy v GUI.

#### [NEW] certificate.py (`backend/mr_quality_monitor/services/certificate.py`)
- Po uzavření vyšetření vygeneruje souhrnný **certifikát kontroly kvality** (např. DICOM Secondary Capture nebo Encapsulated PDF se seznamem sekvencí, stavem semaforu, zjištěnými odchylkami a artefakty) a odešle jej do PACS jako součást téže studie (`StudyInstanceUID`).

---

### 5. Databáze, anonymizované statistiky a administrace

#### [NEW] `v0003_artefacts_pacs_analytics.py` (`backend/mr_quality_monitor/database/migrations/versions/`)
- Nová Alembic migrace rozšiřující DB o:
  - evidenci typů artefaktů na úrovni výsledků (`ValidationIssue` / nová tabulka `artifact_findings`),
  - stav exportu do PACS (`pacs_exports`),
  - anonymizované statistické atributy studie (`operator_name`, `patient_weight_kg`, `patient_bmi`, `patient_age_years`) oddělené od pacientské identity.

#### [NEW] analytics.py (`backend/mr_quality_monitor/analysis/analytics.py`)
- Dotazy nad DB pro reporty požadované v [pozadavky_na_kontrolu.md](file:///c:/test/Robinsoni/pozadavky_na_kontrolu.md) §6 a [Zadani.md](file:///c:/test/Zadani.md) §5:
  - úspěšnost a nejčastější chyby podle laboranta (`OperatorsName`),
  - chybovost podle typu sekvence a trend v čase,
  - korelace artefaktů (např. DWI/SNR) s hmotností/BMI/věkem.

---

### 6. Rozšíření GUI pro obsluhu a administraci

#### [MODIFY] [main_window.py](file:///c:/test/Robinsoni/backend/mr_quality_monitor/gui/main_window.py)
- Přepínač kompaktního režimu („mini semafor“ vedle okna NISu, volitelně *Vždy navrchu* / `WindowStaysOnTopHint`).
- Akční tlačítko u sérií se stavem `ORANGE`/`RED`: **Schválit odeslání do PACS** (zapíše požadavek do DB, GUI zůstává read-only vůči výsledkům analýzy, povolí pouze explicitní akci operátora do samostatné tabulky schválení).
- Tlačítko **Uzavřít vyšetření a vystavit certifikát** + přehledový dialog/záložka **Statistiky & Nastavení tagů** (výběr pořadí privátních `b-value` tagů pro administrátora).

---

## Verification Plan

### Automated Tests
1. **Regrese stávající sady:**
   ```powershell
   .\.venv\Scripts\python.exe -m pytest -q
   .\.venv\Scripts\python.exe -m ruff check backend tests tools
   ```
   *(Pozn.: v aktuálním sandboxu byl spuštěn pouze statický audit kódu; testy je nutné spouštět z lokálního PowerShellu mimo sandbox kvůli WindowsApps aliasu Pythonu 3.12.)*
2. **Nové unit a integrační testy:**
   - `tests/test_prostate_rules.py`: T2W minimum (2 sekvence + upozornění na chybějící TRA), DWI + `RESOLVE` fallback, čtení privátních b-value tagů `(0019,100C)` / `(0021,1105)` v nakonfigurovaném pořadí, geometrická shoda DWI vs `t2_tse_tra`.
   - `tests/test_image_quality.py`: syntetické MR řezy s umělým pohybovým ghostingem, nízkým SNR a slabovým skokem vs čisté snímky; ověření doby běhu (řádově sekundy na studii).
   - `tests/test_pacs_export_and_certificate.py`: lokální testovací PACS Storage SCP uzel — ověření automatického přeposlání `GREEN` série, blokace `RED` série do potvrzení operátorem a přijetí vygenerovaného DICOM certifikátu do testovacího PACS.
   - `tests/test_analytics_and_migrations.py`: migrace z `v2` na `v3` bez ztráty dat, výpočet statistik podle `OperatorsName` a sekvencí.

### Manual Verification
1. Přehrát reálnou sadu `c:\test\DICOMDIR_MR50102\` přes `Odeslat DICOM.cmd` (nebo CLI sender) proti spuštěnému `Spustit aplikaci.cmd` s aktivním profilem `prostate_mou.yaml`:
   - ověřit rozpoznání všech 14 sérií včetně rozdělení DWI `*ez_b50t` a `*ez_b800t`,
   - zkontrolovat zobrazení naměřených parametrů (TR, TE, SliceThickness, SliceGap, FOV, b-value, geometrická shoda DWI/T2 TRA, obrazové metriky) v GUI,
   - ověřit vystavení certifikátu a odeslání čistých sérií proti lokálnímu testovacímu PACS SCP (`mr_quality_monitor.cli` na jiném portu).
