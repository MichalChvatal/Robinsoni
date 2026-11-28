# Validační engine a DEMO protokoly — etapa 4

Engine propojuje normalizovaná metadata s konfigurovatelnými požadavky. Neprovádí
obrazovou analýzu a nevydává skóre PI-QUAL. Aktuální schéma přijímá výhradně
protokoly s explicitním `mode: DEMO`. Číselné hodnoty ukázky nejsou klinické limity.

## Protokol a jeho snapshot

Ukázka: `config/protocols/demo_mr.yaml`. Obsahuje identifikátor a verzi,
autora/zdroj, seznam sekvencí, identifikátory, parametry a podmínky dokončení.
Definice každé sekvence má `order`, povinnost, závažnost chybějící sekvence,
požadavek na obrazovou kontrolu a politiku geometrických nálezů.

```python
from pathlib import Path
from mr_quality_monitor.protocols.manager import load_protocol, ProtocolManager

snapshot = load_protocol(Path("config/protocols/demo_mr.yaml"))
manager = ProtocolManager()
manager.register(snapshot)
manager.select(snapshot.protocol.protocol_id, snapshot.protocol.version)
study_snapshot = manager.begin("1.2.3")
# Zvolený snapshot držet pro celé vyšetření.
manager.end("1.2.3")
```

Snapshot ukládá neměnný kanonický JSON a jeho SHA-256. Změna obsahu pod stejným
ID/verzí je chyba. Novou verzi lze registrovat, ale přepnutí je blokované, dokud
existuje aktivní vyšetření. Tento samostatný `ProtocolManager` drží stav v paměti.
Od etapy 5 poskytuje `HistoryRepository` trvalé připnutí protokolu a historii
přehodnocení; pro obnovu po restartu používejte API popsané v `database.md`.

Načítání YAML/JSON odmítá duplicitní klíče, neznámá pole a soubory nad 1 MiB.
YAML používá bezpečný loader. Pydantic kontroluje typy, jednotky, operátory,
rozsahy, tolerance, násobnost, unikátnost ID a pořadí sekvencí. Jednotky musí
odpovídat normalizaci; klíče jsou DICOM keywords, například `RepetitionTime`.

## Rozpoznání sekvence

Definice potřebuje nejméně dvě odlišná metadata a alespoň jeden údaj mimo názvy
sekvencí/popisy. Engine zkontroluje všechny identifikační podmínky u všech členů.
SeriesDescription samotné k identifikaci nestačí.

Každý kandidát má stav `match`, `mismatch` nebo `unknown`, důkazy, skutečné
hodnoty a zdrojové cesty. Známý nesouhlas kandidáta vyloučí, chybějící údaj nikoliv.
Jediný potvrzený kandidát bez konkurence znamená `identified`; žádný potvrzený
kandidát je `unknown`; více možných kandidátů je `ambiguous`. Také nevyřešený
konkurenční kandidát brání falešné jednoznačnosti. Pořadí definic nerozhoduje shodu.

## Parametrová pravidla

| `constraint.kind` | Význam |
| --- | --- |
| `exact` | Přesná hodnota, bez automatické tolerance |
| `one_of` | Jedna z explicitních hodnot |
| `contains` | Podřetězec textu, s explicitní citlivostí na velikost písmen |
| `range` | Uzavřený interval minimum–maximum |
| `target` | Cíl ± absolutní tolerance nebo `relative_percent` |

`range` může mít širší `warning_minimum`/`warning_maximum`; `target` širší
`warning_tolerance`. Hodnota v základním pásmu je GREEN, jen v širším pásmu ORANGE,
mimo něj rozhoduje `severity: critical|warning`. U procentní tolerance se používá
absolutní velikost cíle; například 10 % z nuly je nula. Hranice jsou inkluzivní.
Porovnání cílových tolerancí používá Decimal z desetinného zápisu, aby například
3,3 vůči 3 ± 0,3 nebylo odmítnuto kvůli binární reprezentaci float.

Vektorové/vícehodnotové pole musí explicitně zvolit `components: all|any`.
Skalární pole používá `scalar`. Engine nevkládá vlastní agregaci průměrem.

Každé pravidlo se vyhodnotí proti všem členům akvizice. Výsledek obsahuje skutečnost,
požadavek, jednotku, SOP/frame, zdrojové cesty, důvod a doporučenou akci.
Chybějící povinný údaj se řídí `on_missing: unknown|critical|warning`; chybějící
nepovinný údaj se přeskočí. Neplatná nebo konfliktní metadata zůstávají GRAY.
Geometrické nálezy se řídí `geometry_severity`, výchozí hodnota je `unknown`.

## Dokončení a barvy

Samotné `AcquisitionGroup.complete`, konec ZIPu ani pauza přenosu nejsou důkaz
dokončení. Definice má jednu ze dvou podmínek:

- `explicit`: volající dodá `CompletionEvidence(source, reference)` z externího
  workflow nebo potvrzení obsluhy.
- `expected_count`: protokol obsahuje důvěryhodný očekávaný počet členů. Počítají
  se unikátní instance/rámce; jiný počet, duplicita nebo nejasná identita je PENDING.
  Pozdě doručený člen vyžaduje nové vyhodnocení a může dokončení znovu zneplatnit.

```python
from mr_quality_monitor.analysis.validation_engine import evaluate, CompletionEvidence

# group je výstup group_acquisitions(). Toto je pouze programová ukázka potvrzení.
proof = CompletionEvidence(source="demo_operator", reference="explicit_confirmation")
result = evaluate(group, study_snapshot, completion=proof)
```

| Celkový stav | Agregace |
| --- | --- |
| RED | Prokázaný kritický nález, i při jiných nehotových kontrolách |
| GRAY | Bez kritického nálezu, ale povinná kontrola nebo dokončení není vyřešené |
| ORANGE | Povinné kontroly dokončené, nalezena tolerovatelná odchylka |
| GREEN | Povinné kontroly daného DEMO rozsahu dokončené a splněné |

Metadata, dokončení a obrazová kontrola mají vlastní stavy. Obrazový stav je
v této etapě vždy GRAY. Při `image_required: true` blokuje zelený celkový stav.
Explicitní `image_required: false` umožňuje zelený metadata-only DEMO výsledek;
`clinical_quality_evaluated` přesto vždy zůstává `false`. Varování se zachová,
i když celkový stav je GRAY. Žádná barva není automatickým klinickým doporučením.

## Vyšetření a opakování

`evaluate_study(groups, snapshot, completions=..., finalization=...)` doplní přehled
povinných sekvencí. Bez explicitní finalizace je nepřijatá povinná sekvence PENDING;
po finalizaci dostane konfigurovanou závažnost. Nejednoznačná identifikace není
prokázaná absence a zůstává GRAY. Finalizace vyšetření sama nepotvrzuje dokončení
jednotlivých akvizic. Nepřijaté nepovinné sekvence se zobrazují, ale neblokují výsledek.

Všechna přijatá opakování se zachovávají a hodnotí; žádné se automaticky nevybere
jako nejlepší. Také neznámé přijaté skupiny zůstávají viditelné a nevyhodnocené.
Více studií nebo překrývající se členství skupin je vstupní chyba. `order` určuje
pořadí výpisu, nikoliv kontrolu skutečné chronologie měření; tu nelze odvodit
z pořadí síťového doručení a v této etapě není implementovaná.

Výsledky obsahují čas UTC, verzi enginu, identifikátor/verzi/hash protokolu a hash
vstupní akvizice včetně metadat a důkazu dokončení. Pro reprodukovatelný test lze
předat `evaluated_at` s časovým pásmem. Volající odpovídá za uložení výsledků;
engine sám nezapisuje do DB ani provozních logů. Detailní výsledky mohou obsahovat
citlivé hodnoty a nejsou určené pro veřejné logování.

## Lokální DEMO nad ZIPem

```powershell
.\.venv\Scripts\python.exe tools\validate_demo.py data\DICOMDIR_MR50000_ukazka_jednoho_mereni.zip --report test-results\validation-stage4.json
.\.venv\Scripts\python.exe tools\validate_demo.py data\DICOMDIR_MR50101-MR40132.zip --report test-results\validation-stage4-batch.json
```

Nástroj zpracuje hlavičky po případech, nic neposílá ven a reportuje pouze souhrnné
počty bez pacientských hodnot/UID. Nepotvrzuje dokončení akvizic ani vyšetření.
`technical_run: passed` znamená úspěšný běh softwaru, nikoliv splnění DEMO limitů
nebo PI-QUAL. Ukázkový protokol rozpoznává jen obecné T2 kandidáty; není seznamem
všech klinicky požadovaných sekvencí a neporovnává výsledky s `tier` z reportů.
