# ROLE A ZADÁNÍ PRO AI

Jsi seniorní Python software architect a developer se zkušenostmi s vývojem medicínských aplikací, DICOM standardem, MRI imaging, PACS systémy a zpracováním medicínských obrazových dat.

Tvým úkolem je navrhnout a postupně implementovat lokální desktopovou aplikaci v Pythonu, která bude fungovat jako DICOM listener pro příjem MRI snímků a automatickou kontrolu správnosti jednotlivých MRI sekvencí podle definovaného protokolu PI-QUAL. Tato aplikace ma byt velmi jednoducha jednookenni aplikace, ktera bude zobrazovat pouze text jednotlivych sequenci a bude oznacovat ikonkama zda obahuje chybu v metadatech nebo v obrazku

Aplikace musí být modulární, rozšiřitelná, spolehlivá a připravená na budoucí rozšíření o pokročilejší analýzu DICOM metadat a případně samotných obrazových dat.

# 1. HLAVNÍ CÍL APLIKACE

Aplikace poběží lokálně na počítači, který bude připojen do nemocniční sítě.

Bude komunikovat s MRI přístrojem prostřednictvím DICOM protokolu.

Jejím úkolem bude:

1. Přijímat DICOM objekty z MRI přístroje.
2. Identifikovat pacienta a konkrétní vyšetření.
3. Identifikovat jednotlivé MRI sekvence.
4. Analyzovat DICOM metadata jednotlivých snímků.
5. Porovnávat získané parametry s požadavky definovaného MRI protokolu PI-QUAL.
6. Vyhodnotit, zda sekvence splňuje požadované parametry.
7. Upozornit na kritické nebo méně závažné odchylky.
8. Zobrazit aktuální stav vyšetření v grafickém uživatelském rozhraní.

Cílem je umožnit kontrolu kvality MRI vyšetření již během jeho provádění, aby bylo možné včas identifikovat chybně provedené sekvence a případně opakovat jejich měření.

# 2. TECHNOLOGICKÝ STACK

Preferovaný programovací jazyk: Python.

Navrhni vhodné technologie a zdůvodni jejich výběr.

Předběžně preferované knihovny:

- Python 3.12 nebo novější.
- pynetdicom – DICOM networking a C-STORE SCP.
- pydicom – čtení a analýza DICOM metadat.
- PySide6 – desktopové grafické rozhraní.
- SQLite – lokální databáze.
- SQLAlchemy – databázová vrstva.
- Pydantic – validace dat a konfigurace.
- pytest – automatizované testování.
- logging – systémové logování.

Aplikace musí fungovat bez závislosti na cloudových službách a bez nutnosti připojení k internetu.

# 3. DICOM LISTENER

Implementuj DICOM listener, který bude fungovat jako lokální DICOM Storage SCP.

Požadavky:

- Konfigurovatelná IP adresa.
- Konfigurovatelný TCP port.
- Konfigurovatelné AE Title.
- Podpora DICOM C-STORE.
- Správné zpracování příchozích DICOM objektů.
- Ukládání přijatých souborů do lokálního úložiště.
- Kontrola integrity přijatých souborů.
- Zpracování chyb při příjmu.
- Evidence času přijetí každého objektu.
- Evidence odesílajícího AE Title.
- Ochrana proti duplicitnímu zpracování DICOM objektů.

Listener musí běžet nezávisle na GUI, aby případné zamrznutí nebo restart uživatelského rozhraní neovlivnily příjem dat.

Při přijetí nového DICOM objektu musí být událost předána analytickému modulu.

# 4. ANALÝZA DICOM METADAT

Vytvoř samostatný modul pro extrakci a normalizaci DICOM metadat.

Zpracovávej zejména následující informace:

Identifikace vyšetření:
- PatientID
- PatientName
- StudyInstanceUID
- StudyDate
- StudyTime
- AccessionNumber

Identifikace série:
- SeriesInstanceUID
- SeriesDescription
- SeriesNumber
- ProtocolName
- SequenceName
- Modality
- ImageType

Technické parametry:
- RepetitionTime (TR)
- EchoTime (TE)
- InversionTime (TI)
- FlipAngle
- SliceThickness
- SpacingBetweenSlices
- PixelSpacing
- Rows
- Columns
- AcquisitionMatrix
- MagneticFieldStrength
- NumberOfAverages
- EchoTrainLength
- ScanningSequence
- SequenceVariant
- MRAcquisitionType
- PhaseEncodingDirection
- PixelBandwidth

Počítej s tím, že některé parametry nemusí být přítomny ve standardních DICOM a mohou být uloženy v privátních DICOM tagech výrobce.

Architektura proto musí umožňovat implementaci vendor-specific parserů pro výrobce:

- Siemens
- Philips
- GE
- Canon

Důležité:
Nepředpokládej, že všechny parametry lze získat ze standardních DICOM tagů. Vytvoř systém normalizace, který bude schopen pracovat s různými výrobci MR přístrojů.

# 5. ENGINE PRO VYHODNOCENÍ PI-QUAL

Vytvoř samostatný validační engine.

Jeho úkolem bude porovnávat parametry přijaté MRI sekvence s definovanými požadavky protokolu.

Požadavky jednotlivých sekvencí nesmí být pevně zakódovány přímo v Python kódu.

Musí existovat konfigurační soubor, například JSON nebo YAML, ve kterém bude možné definovat:

- Název sekvence.
- Pořadí sekvence.
- Povinné parametry.
- Očekávané hodnoty parametrů.
- Minimální a maximální přípustné hodnoty.
- Toleranci jednotlivých parametrů.
- Kritičnost případných odchylek.
- Informaci, zda je sekvence povinná.
- Identifikátory používané pro rozpoznání sekvence.

Příklad konfigurační definice:

{
  "sequence_id": "T2_AX",
  "display_name": "T2 Axial",
  "required": true,
  "parameters": {
    "TR": {
      "target": 4000,
      "min": 3500,
      "max": 4500,
      "severity": "critical"
    },
    "TE": {
      "target": 100,
      "min": 85,
      "max": 115,
      "severity": "warning"
    },
    "SliceThickness": {
      "target": 3.0,
      "min": 2.5,
      "max": 3.5,
      "severity": "critical"
    }
  }
}

Uvedené hodnoty jsou pouze ilustrativní a nesmí být považovány za skutečné požadavky PI-QUAL.

Validační engine musí podporovat:

- Přesnou shodu hodnot.
- Rozmezí hodnot.
- Toleranci.
- Povinné a nepovinné parametry.
- Chybějící metadata.
- Kritické chyby.
- Varování.
- Neznámé sekvence.
- Nejednoznačné rozpoznání sekvence.

# 6. VYHODNOCOVACÍ LOGIKA

Každá sekvence bude mít jeden ze čtyř stavů:

GREEN – OK:
Sekvence splňuje všechny kritické požadavky a přípustné tolerance.

ORANGE – WARNING:
Sekvence obsahuje menší odchylku, která je podle definovaného protokolu tolerovatelná.

RED – CRITICAL:
Sekvence nesplňuje kritický požadavek a její opakování je doporučeno nebo vyžadováno podle nastavených pravidel.

GRAY – PENDING / UNKNOWN:
Sekvence dosud nebyla přijata, případně není možné její parametry spolehlivě vyhodnotit.

Každé vyhodnocení musí obsahovat:
- Výsledný stav.
- Identifikaci sekvence.
- Skutečné naměřené hodnoty.
- Požadované hodnoty.
- Popis nalezených odchylek.
- Důvod výsledného hodnocení.
- Čas vyhodnocení.

Důležité: Samotná přítomnost jednoho DICOM snímku nemusí znamenat, že je celá sekvence dokončena. Navrhni mechanismus, který umožní agregovat více snímků do jedné série a vyhodnotit ji až po splnění definovaných podmínek dokončení.

# 7. GRAFICKÉ UŽIVATELSKÉ ROZHRANÍ

Použij PySide6.

GUI musí být přehledné, moderní a optimalizované pro rychlé používání během MRI vyšetření.

Navrhni hlavní obrazovku rozdělenou na následující části:

A. INFORMACE O VYŠETŘENÍ

Zobraz:
- Patient ID.
- Patient Name (pokud je dostupné a povolené).
- Accession Number.
- Study Date.
- MR přístroj.
- Aktuální stav vyšetření.

B. SEZNAM POŽADOVANÝCH SEKVENCÍ

Zobraz tabulku všech sekvencí definovaných v protokolu PI-QUAL.

Sloupce:
- Pořadí.
- Název sekvence.
- Stav.
- Zjištěné TR.
- Zjištěné TE.
- Slice Thickness.
- Počet snímků.
- Výsledek kontroly.
- Doporučená akce.

Barevné označení:
- Zelená: sekvence v pořádku.
- Oranžová: tolerovatelná odchylka.
- Červená: kritická chyba.
- Šedá: dosud nepřijato nebo nevyhodnoceno.

C. DETAIL SEKQUENCE

Po kliknutí na sekvenci zobraz:
- Kompletní seznam relevantních DICOM metadat.
- Očekávané hodnoty.
- Skutečné hodnoty.
- Jednotlivé odchylky.
- Výsledek validačních pravidel.
- Historii změn stavu.

D. STAV DICOM LISTENERU

Zobraz:
- Stav připojení.
- IP adresu.
- Port.
- AE Title.
- Počet přijatých snímků.
- Poslední přijatý snímek.
- Případné chyby komunikace.

# 8. SPRÁVA PROTOKOLŮ

Vytvoř samostatný modul pro správu validačních protokolů.

Umožni:
- Načíst protokol z YAML nebo JSON.
- Vybrat aktivní protokol.
- Zobrazit jeho požadavky.
- Validovat správnost konfigurace.
- Udržovat různé varianty protokolů.
- Evidovat verzi protokolu.
- Zabránit nechtěným změnám během aktivního vyšetření.

Protokoly musí být oddělené od zdrojového kódu aplikace.

# 9. DATABÁZE A HISTORIE

Použij SQLite a SQLAlchemy.

Navrhni datový model obsahující minimálně:

- Patients
- Studies
- Series
- DicomInstances
- SequenceDefinitions
- ValidationResults
- ValidationIssues
- ProtocolVersions
- SystemEvents

Umožni zpětně dohledat:
- Jaké sekvence byly přijaty.
- Jaké parametry obsahovaly.
- Jak byly vyhodnoceny.
- Jaká verze protokolu byla použita.
- Kdy došlo k vyhodnocení.
- Jaké chyby byly nalezeny.

Zohledni ochranu osobních a zdravotních údajů, auditovatelnost a řízení přístupu.

# 10. ARCHITEKTURA A STRUKTURA PROJEKTU

Navrhni čistou modulární architekturu.

Preferovaná struktura:

mr_quality_monitor/
│
├── main.py
├── config/
│   ├── app_config.yaml
│   └── protocols/
│       └── pi_qual.yaml
│
├── dicom/
│   ├── listener.py
│   ├── receiver.py
│   ├── storage.py
│   └── metadata_parser.py
│
├── analysis/
│   ├── sequence_identifier.py
│   ├── metadata_normalizer.py
│   ├── validation_engine.py
│   └── validation_rules.py
│
├── vendors/
│   ├── base.py
│   ├── siemens.py
│   ├── philips.py
│   ├── ge.py
│   └── canon.py
│
├── database/
│   ├── models.py
│   ├── repository.py
│   └── database.py
│
├── gui/
│   ├── main_window.py
│   ├── sequence_table.py
│   ├── sequence_detail.py
│   └── listener_status.py
│
├── services/
│   ├── study_service.py
│   └── validation_service.py
│
├── tests/
│
├── logs/
│
└── requirements.txt

Strukturu můžeš upravit podle vlastního odborného uvážení.

# 11. TESTOVÁNÍ

Vytvoř automatizované testy pro:

- Příjem DICOM objektů.
- Parsování DICOM metadat.
- Identifikaci sekvencí.
- Normalizaci metadat.
- Validační engine.
- Vyhodnocování kritických chyb.
- Vyhodnocování tolerovatelných odchylek.
- Chybějící metadata.
- Neznámé sekvence.
- Agregaci snímků do sérií.
- Databázové operace.

Vytvoř také sadu syntetických DICOM testovacích dat.

Testovací data nesmí obsahovat skutečné identifikovatelné údaje pacientů.

# 12. BEZPEČNOST A SPOLEHLIVOST

Aplikace musí:

- Fungovat lokálně.
- Neodesílat DICOM data mimo lokální počítač.
- Minimalizovat ukládání osobních údajů.
- Podporovat bezpečné ukládání dat.
- Logovat chyby a systémové události.
- Zvládnout restart bez ztráty již přijatých dat.
- Oddělit příjem DICOM dat od analytických operací.
- Zabránit blokování GUI při příjmu nebo vyhodnocování.

Vyhodnocení musí být reprodukovatelné a dohledatelné.

# 13. POSTUP IMPLEMENTACE

Nezačínej okamžitým generováním celého projektu.

Pracuj v následujících etapách:

ETAPA 1 – ARCHITEKTURA
- Navrhni kompletní architekturu.
- Popiš jednotlivé moduly.
- Popiš datové toky.
- Navrhni databázové schéma.
- Popiš komunikaci mezi jednotlivými komponentami.
- Identifikuj technická rizika.

ETAPA 2 – DICOM LISTENER
- Implementuj funkční DICOM Storage SCP.
- Otestuj příjem DICOM objektů.
- Zajisti jejich bezpečné lokální uložení.

ETAPA 3 – METADATA ANALÝZA
- Implementuj extrakci metadat.
- Vytvoř normalizační vrstvu.
- Připrav rozhraní pro vendor-specific parsery.

ETAPA 4 – VALIDAČNÍ ENGINE
- Implementuj načítání protokolů.
- Implementuj rozpoznávání sekvencí.
- Implementuj vyhodnocování parametrů.
- Otestuj všechny čtyři výsledné stavy.

ETAPA 5 – DATABÁZE
- Implementuj databázové modely.
- Ulož historii vyšetření a výsledků kontrol.

ETAPA 6 – GUI
- Implementuj hlavní obrazovku.
- Přidej barevné označování sekvencí.
- Přidej detailní zobrazení výsledků.
- Přidej monitoring DICOM listeneru.

ETAPA 7 – INTEGRACE
- Propoj všechny komponenty.
- Otestuj kompletní workflow.
- Připrav aplikaci pro lokální spuštění.

# 14. PRAVIDLA PRO AI BĚHEM VÝVOJE

Dodržuj následující pravidla:

1. Piš kvalitní, typovaný a dokumentovaný Python kód.
2. Používej asynchronní nebo oddělené pracovní procesy tam, kde je to vhodné.
3. Nevytvářej zbytečně složitou architekturu.
4. Dodržuj principy SOLID a separation of concerns.
5. Neumisťuj validační pravidla přímo do GUI.
6. Nepředpokládej, že všechny DICOM tagy jsou vždy dostupné.
7. Nezaměňuj SeriesDescription za spolehlivou identifikaci sekvence.
8. Každé validační pravidlo musí být konfigurovatelné.
9. Každou důležitou funkci doplň automatizovanými testy.
10. Při změnách vždy zachovej funkčnost již implementovaných modulů.

Pokud některá část zadání není dostatečně specifická, nejprve se zeptej na potřebné informace.

Nevymýšlej klinické parametry ani limity PI-QUAL. Pokud nejsou k dispozici přesné specifikace protokolu, vytvoř pouze ukázkovou konfiguraci jasně označenou jako DEMO.

# 15. PRVNÍ ÚKOL

Nyní proveď pouze ETAPU 1.

Připrav:
- Detailní návrh softwarové architektury.
- Diagram komponent.
- Diagram datového toku.
- Návrh databázových tabulek.
- Návrh DICOM workflow.
- Návrh validačního enginu.
- Doporučený technologický stack.
- Seznam rizik a jejich řešení.

Zatím negeneruj kompletní zdrojový kód.

Na závěr uveď seznam informací, které potřebuješ ode mě před zahájením implementace.