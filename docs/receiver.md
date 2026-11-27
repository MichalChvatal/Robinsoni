# Spuštění DICOM receiveru — etapa 2

Receiver je samostatná konzolová aplikace, nezávislá na samostatném GUI. Zatím pouze
přijímá a eviduje soubory; nehodnotí PI-QUAL, nevytváří klinické závěry a nemá
instalátor systémové služby.

## Instalace pro vývoj

Z kořene repozitáře, s Pythonem 3.12 nebo novějším:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -q
```

Ověřené konkrétní verze jsou zaznamenané v
`requirements-stage5.lock.txt`. V prostředí bez internetu je nutné připravit
wheelhouse na odpovídajícím OS/verzi Pythonu a instalovat s `--no-index
--find-links <wheelhouse>`; instalace závislostí není součástí běžného příjmu.

Pro instalaci ověřených verzí místo volného výběru verzí:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-stage5.lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
```

Explicitní integrační kontrola dodaného ZIPu (pouze lokální přenos na localhost):

```powershell
.\.venv\Scripts\python.exe tools\smoke_dataset.py data\DICOMDIR_MR50000_ukazka_jednoho_mereni.zip --report test-results\dataset-stage2.json
```

Skript používá dočasné úložiště pod `test-results/`, po testu jej odstraní a
ponechá pouze souhrnný JSON report bez pacientských údajů. Data ze ZIPu se v testu
neanonymizují; vstup proto používejte pouze v prostředí oprávněném pro jejich čtení.

## Konfigurace a běh

```powershell
.\.venv\Scripts\python.exe -m mr_quality_monitor.cli --config config\app_config.yaml
```

Ukončení: Ctrl+C. Výchozí konfigurace naslouchá pouze na `127.0.0.1:11112`,
volaný AE je `MR_QUALITY`, povolený volající AE je `MR_TEST` a povolená IP
odesílatele je `127.0.0.1`. Pro přístroj upravte všechny tyto hodnoty podle
jeho konfigurace a Conformance Statement; otevření firewallu aplikace neprovádí.
Jedna konfigurace povoluje jeden MR peer. AE Title neplní roli autentizace.

`storage_root` je relativní k adresáři konfiguračního souboru, nikoliv aktuálnímu
pracovnímu adresáři. Výchozí umístění je `runtime/` v kořeni projektu, ignorované
Gitem. Pro nasazení zvolte vyhrazený místní adresář s přístupovými právy a
šifrováním disku. `minimum_free_bytes` určuje zachovanou diskovou rezervu.

| Umístění pod storage_root | Obsah |
| --- | --- |
| `objects/<interní UUID>.dcm` | Potvrzené DICOM objekty, bez pacientských údajů v názvu |
| `staging/` | Rozpracované zápisy |
| `quarantine/` | Konfliktní, neúplné nebo osiřelé soubory; neodstraňovat bez vyřešení příčiny |
| `intake.sqlite3`, soubory WAL/SHM | Evidence příjmu, událostí a fronta pro budoucí analýzu |
| `receiver.lock` | OS zámek proti dvěma receiverům nad jedním úložištěm |
| `logs/receiver.log` | Rotovaný provozní log bez datasetů a pacientských hodnot |

Soubor zámku může po ukončení zůstat; rozhodující je OS zámek, který se uvolní
uzavřením procesu. Od etapy 5 databáze obsahuje také tabulky historie a při otevření
prochází verzovanými migracemi; podrobnosti jsou v `database.md`. Samotný receiver
nadále zapisuje příjmovou část. `analysis_jobs` drží nezpracované úlohy — automatický
analytický worker se implementuje později.

## Podporovaný DICOM profil

- Verification / C-ECHO.
- MR Image Storage a Secondary Capture Image Storage.
- Explicit VR Little Endian a Implicit VR Little Endian.
- Jednorámcové nativní obrazy, 8 nebo 16 bitů, MONOCHROME1/2.
- Secondary Capture navíc podporuje RGB s 8 bity a PlanarConfiguration 0/1.

Enhanced MR, komprimované syntaxe, multiframe, palette color a další obrazové
profily se zatím nevyjednávají nebo jsou explicitně odmítnuty. Nejde o obecný PACS.
Kontrola pixelů v této etapě ověřuje rozměry a délku dat, nikoliv artefakty obrazu
ani splnění celého DICOM IOD. PatientID ani PatientName se neindexují, původní
soubor však jejich obsah zachovává.

| C-STORE status | Význam v této implementaci |
| --- | --- |
| `0x0000` | Objekt trvale uložen nebo identická duplicita; DB transakce dokončena |
| `0xA700` | Příjem nelze bezpečně dokončit, např. chyba disku nebo DB |
| `0xC210` | Dataset je pro podporovaný profil neplatný nebo nesouhlasí s příkazem |
| `0xC211` | UID již existuje s odlišným uloženým obsahem; nová kopie je v karanténě |

Duplicita se určuje pomocí SOPInstanceUID a SHA-256 deterministicky uloženého
souboru. File meta se sjednocují. Rozdílné transfer syntaxe nebo jiné ekvivalentní
serializace přesto mohou skončit jako konzervativní konflikt. Receiver nepřepisuje
původní soubor a zatím neprovádí sémantické porovnávání pixelů.

## Restart a zotavení

Před otevřením portu receiver projde journal a soubory. Platný dokončený zápis
dokončí v DB, neúplné zápisy dá do karantény, obnoví chybějící úlohy a ověří
SHA-256 uložených objektů. Pokud chybí nebo je poškozen už potvrzený objekt,
start zastaví a zaznamená příčinu v `system_events`; původní evidence zůstává.
Zpracování po pádu může dokončit i objekt, pro který odesílatel ještě nedostal
úspěšnou odpověď — jeho opakované poslání se zpracuje idempotentně.

Úspěšné C-STORE není implementací DICOM Storage Commitment. Obnova po pádu
procesu nenahrazuje ověření odolnosti proti výpadku napájení na cílovém zařízení.
Na Windows používáme fsync souboru a atomický přesun na stejném svazku;
odolnost metadat adresářů při výpadku závisí na souborovém systému a úložišti.

Zálohujte konzistentní SQLite snapshot a soubory společně; nekopírujte pouze živý
`.sqlite3` bez zohlednění WAL. Před ruční opravou zastavte receiver a uchovejte
kopii evidence. Automatická retence/mazání, řízení rolí a TLS zatím nejsou hotové.
