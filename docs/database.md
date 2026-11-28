# Databáze a historie — etapa 5

Příjem DICOM používá nadále stejnou SQLite databázi. Nové schéma verze 2 doplňuje
trvalé protokoly, vazby vyšetření, metadata a historii kontrol. Automatický worker
zatím frontu nevybírá; ukládání historie je samostatné API pro další integraci.

## Migrace

`open_database(path)` nastaví cizí klíče, WAL, synchronní zápis FULL a čekání na
zapisovatele. Před kontrolou a migrací získá SQLite rezervaci zapisovatele pomocí
`BEGIN IMMEDIATE`. Alembic provede DDL i změnu verze v této explicitní transakci.

Podporované vstupy:

- Prázdná databáze: migrace `0001_intake` a `0002_history`.
- Původní schéma verze 1 bez Alembic: ověření tabulek/sloupců, primárních a unikátních
  klíčů a FK, záloha, přiřazení revize `0001_intake` a upgrade.
- Revize Alembic `0001_intake`: záloha a upgrade.
- Revize `0002_history`: otevření bez dalšího upgradu.

Neznámé či poškozené schéma se odmítne. Při chybě se změny schématu i jeho
přiřazené verze vrátí zpět. Stávající příjmové tabulky se nepřestavují a jejich
řádky zůstávají zachované; mění se záznam verze a přidávají tabulky historie.

Před upgradem existující databáze vznikne vedle ní záloha
`<databaze>.pre-history-<nahodne_UUID>.sqlite3`. Používá se SQLite backup API,
které zahrne potvrzený obsah WAL. Záloha se nepřepisuje a automaticky nemaže.
Nejde o zálohu DICOM souborů — ty je nutné zálohovat také. Automatický destruktivní
downgrade není podporovaný; návrat vyžaduje zastavení všech procesů a obnovu
ověřené zálohy s odpovídající verzí aplikace. Současně spuštěné staré binární
verze neumějí nové schéma; při prvním upgradu je nejprve ukončete.

Migrační soubory jsou součástí balíku v `database/migrations/`. Upgrade se spouští
přes `open_database()`, nikoliv přes samostatný Alembic příkaz bez připojení.

## Tabulky a vazby

| Tabulka | Úloha |
| --- | --- |
| `patients` | Náhodný lokální token; bez jména a PatientID |
| `studies`, `series` | Studie a fyzické série podle UID, aktuální stav a revize studie |
| `protocol_versions`, `sequence_definitions` | Kanonický snapshot protokolu, hash a definice sekvencí |
| `analysis_branches` | Původní vazba `main` nebo explicitní přehodnocení s autorem/důvodem |
| `metadata_snapshots` | Neměnná metadata instance/rámce, raw zdroje, jednotky a verze parserů |
| `logical_acquisitions`, `acquisition_members` | Přesné složení a geometrické nálezy akvizice |
| `validation_runs` | Celé vyhodnocení studie, čas, autor, protokol a předchozí běh |
| `validation_results` | Dílčí akvizice i přítomnost požadovaných sekvencí |
| `validation_issues` | Nezelené kontroly, důvody a požadavky, včetně PENDING/UNKNOWN |
| `history_events` | Události připnutí protokolu, vytvoření větve a uložení běhu |

Původní `dicom_instances`, `intake_records`, `reception_events`, `analysis_jobs`
a `system_events` nadále zajišťují příjem. Metadata musí odkazovat na skutečně
přijatou instanci se stavem integrity `stored`, se souhlasnou studií, sérií
a SOP Class. Tabulka instancí zůstala kvůli kompatibilitě beze změny; vazba
do nového doménového modelu vede přes metadata snapshot.

Žádný pacient se automaticky neslučuje napříč studiemi. Každá studie dostane
vlastní náhodný pacientský token, dokud nebude schválena identifikační politika.
Repository odmítá metadata se zkopírovaným PatientID/PatientName/issuer/accession;
používejte standardní parser s potlačenými identifikátory. To není anonymizace:
UID, popisy sérií, ostatní volné texty i DICOM originály mohou být citlivé.

## Použití API

```python
from pathlib import Path
from mr_quality_monitor.database.database import open_database
from mr_quality_monitor.database.repository import HistoryRepository
from mr_quality_monitor.protocols.manager import load_protocol

engine, sessions = open_database(Path("runtime/intake.sqlite3"))
try:
    history = HistoryRepository(sessions)
    snapshot = load_protocol(Path("tests/fixtures/protocols/demo_mr.yaml"))
    branch = history.begin_study(study_uid, snapshot, actor="local_operator")
    # groups obsahuje výstup parseru/seskupování skutečně přijatých souborů.
    run_id = history.save_evaluation(
        study_uid, groups, request_id="unique-operation-id", actor="local_operator"
    )
    saved = history.load_evaluation(run_id)
    timeline = history.history(study_uid)
    summary = history.study_summary(study_uid)
finally:
    engine.dispose()
```

Vyhodnocení se počítá před transakcí zápisu. Uložení všech vstupních snapshotů,
členství, výsledků, issues a auditní události je jedna transakce. Při chybě nevznikne
část historie; již přijaté soubory a příjmové řádky zůstávají nedotčené.

Stejný `request_id` a stejné vstupy vrátí původní běh i jeho čas, bez dalších řádků.
Stejný request s jinými vstupy je chyba. Nový request přidá nový běh s vazbou
`supersedes_id`. Pořadí historie určuje monotónní `sequence_number` v rámci větve,
nikoliv UUID nebo uživatelem dodaný čas hodnocení. Rozhodnutí i vložení jsou
serializované mezi zapisovateli pomocí `BEGIN IMMEDIATE`.

Protokol původního vyšetření se nesmí změnit, ani po restartu. `create_branch()`
umožní explicitní nové přehodnocení se jménem větve, autorem a důvodem. Původní
větev, protokol a výsledky zůstanou zachované. Stejná verze protokolu s jiným
obsahem je vždy chyba; je nutná nová verze.

`load_group(sha256)` obnoví členy ze snapshotů včetně jednotek/provenance a poloh.
`get_protocol()` a čtení výsledků/metadat ověřují kontrolní součty. Uložené vstupy
lze znovu předat enginu a ověřit shodu jeho vstupních hashů. Obnova v současnosti
podporuje formát metadat `metadata/1`.

## Zastaralý výsledek a finalizace

Nový metadatový snapshot zvýší revizi studie. Nová metadata uzavřené studie ji
znovu otevřou. Také přijatá instance, která ještě nemá metadata snapshot, označí
souhrn za zastaralý: `stale=true`, `unindexed_instances>0`. Historický výsledek se
nezmění, ale zelená se nepředkládá jako aktuální; `effective_status` je GRAY.
Již zjištěná RED má prioritu a při nehotových dalších datech zůstane RED.

`recorded_status` je původní uložený stav, `effective_status` je aktuální přehled.
`state` zohledňuje nezpracovaná příchozí data, `recorded_state` je stav poslední
uložené lifecycle změny. Pro GUI je nutné ukazovat také příznak zastaralosti.

Při explicitní finalizaci musí předané skupiny obsahovat metadata všech přijatých
instancí studie. Repository nepovolí označit zatím nezpracovaná data za chybějící
sekvenci. Finalizace a dokončení akvizic jsou stále oddělené důkazy podle etapy 4;
žádný konec souboru ani dokončený DB zápis je nenahrazuje.

## Ochrana historie a provoz

SQLite triggery odmítají UPDATE/DELETE tabulek historie a změnu původního navázání
studie. Aktuální stav/revize studie jsou projekce, které se měnit mohou. Kontrolní
součty odhalují poškození čtených snapshotů. Tyto mechanismy nenahrazují OS práva,
šifrovaný disk, zálohy ani ochranu proti administrátorovi, který může změnit samotné
schéma. `actor` je auditní údaj dodaný volajícím; autentizace/role zatím nejsou hotové.

Automatické mazání/retence historie ani záloh nejsou implementované. Nevkládejte
citlivé hodnoty do veřejných logů a chraňte i adresář se zálohami. Zápisy nepoužívají
dlouhodobě sdílenou Session; každý požadavek má vlastní transakci.

## Integrační kontrola

```powershell
.\.venv\Scripts\python.exe tools\smoke_history.py data\DICOMDIR_MR50000_ukazka_jednoho_mereni.zip --report test-results\history-stage5.json
```

Nástroj přijme jednu studii do dočasného lokálního úložiště, uloží protokol/metadata/
DEMO výsledek, zopakuje request, znovu otevře databázi a ověří opakovatelnost
vstupních hashů. Po skončení dočasná data odstraní; report obsahuje jen souhrnné
počty bez pacientských hodnot. Neodesílá data do sítě a nepotvrzuje klinickou kvalitu.

Pro reprodukovatelnou instalaci této etapy slouží `requirements-stage5.lock.txt`.
Historický `requirements-stage2.lock.txt` zůstává záznamem původní etapy a sám
o sobě již neobsahuje všechny požadované závislosti současného kódu.
