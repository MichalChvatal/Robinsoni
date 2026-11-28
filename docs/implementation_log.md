# Průběžný log implementace

## 2026-11-27 — etapa 2: DICOM příjem

### 2.1 — příprava

- Přečten `docs/architecture.md`; další práce je omezena na samostatný receiver a nezbytnou trvalou evidenci příjmu.
- Repozitář dosud neobsahoval implementaci, pouze `backend/listener/.gitignore`.
- Existující změny README a dodaná data ponechány beze změny.
- Systémový alias Pythonu není spustitelný; nalezen Python v lokálním runtime. Projekt dostane vlastní virtuální prostředí.
- Výchozí konfigurace bude pouze loopback a explicitně povolený testovací AE/IP. Síťové parametry reálného MR zatím nejsou známé.
- Klinická validace, GUI a služba OS přijdou v dalších etapách.

### 2.2 — plánovaný ověřitelný výstup

- Konfigurovatelný C-STORE SCP pro MR Image Storage a Secondary Capture, C-ECHO.
- Atomické ukládání, SHA-256, kontrola identity a základní integrity nekomprimovaných pixelů.
- SQLite/SQLAlchemy intake journal, instance, příjmové události a trvalá fronta pro budoucí worker.
- Duplicity, konflikty v karanténě, recovery po přerušení zápisu a detekce chybějících souborů.
- Syntetické unit/integrační testy včetně skutečného localhost DICOM spojení.

Výsledky provedených kontrol a zbývající omezení budou doplněny po ověření.

### 2.3 — implementace a obnovení prostředí

- Vytvořen balík `backend/mr_quality_monitor`, striktní YAML konfigurace,
  samostatný konzolový listener, SQLite schéma verze 1 a procesový zámek.
- Implementován journal před zápisem, staging + fsync + atomické zveřejnění,
  kontrola UID a nativních pixelů, SHA-256 a transakční založení analytické úlohy.
- Přidáno uchování konfliktů v karanténě a kontrola potvrzených souborů při startu.
- Při první práci byla dostupná pouze kontrola syntaxe; instalaci blokovalo
  připojení a následovalo odpojení prostředí. Testy tehdy nebyly spuštěné.
- Po obnovení prostředí pokračuje instalace závislostí do `.venv`.
- Při kontrole kódu doplněno zachycení `InvalidDicomError` při obnově neúplného
  souboru a čekání na zachycený seznam handlerů při ukončení listeneru.
- Návod k běhu a hranice podporovaného profilu jsou v `docs/receiver.md`.

### 2.4 — první ověření a opravy

- Instalace závislostí po obnovení přístupu uspěla.
- První běh: **37 testů prošlo, 1 selhal**. Příčina byla proměnná `syntax`
  omylem použitá v testu restartu místo v parametrizovaném testu syntaxí.
  Opraveno; testy transfer syntaxí nyní vynucují konkrétní vyjednanou syntaxi.
- Prošly i testy skutečného ukončení pomocného procesu přes `os._exit(91)`
  po zápisu staging souboru a po zveřejnění souboru před finální DB transakcí.
- Statická kontrola Ruff našla nesjednocené importy; opraveny a kód formátován.
  Následná kontrola Ruff prošla.
- Přidán `tools/smoke_dataset.py`: explicitní lokální integrační kontrola ZIPu
  přes DICOM localhost, včetně dvou duplicit a restartu. Dataset se nerozbaluje
  do zdrojového adresáře a testovací úložiště se po ukončení uvolní.
- Běží opakování celé sady a kontrola MR50000. Závěrečný výsledek bude níže.

### 2.5 — závěrečné ověření etapy 2

- **38 automatizovaných testů prošlo** (48,95 s). Jediné varování je záměrně
  neplatný syntetický UID v testu odmítnutí vadného datasetu.
- Ruff lint i kontrola formátování prošly; 15 Python souborů je formátovaných.
- CLI `python -m mr_quality_monitor.cli --help` prošlo.
- `pip check` nezjistil konflikty závislostí; `git diff --check` prošel.
- Síťový test celého `DICOMDIR_MR50000_ukazka_jednoho_mereni.zip` prošel:

  | Kontrola | Výsledek |
  | --- | ---: |
  | Unikátní uložené objekty | 321 |
  | MR Image Storage | 320 |
  | Secondary Capture | 1 |
  | Fyzické série podle SeriesInstanceUID | 14 |
  | Trvalé analytické úlohy | 321 |
  | Příjmové události včetně dvou duplicit | 323 |
  | C-STORE chyby při přenosu vstupní sady | 0 |
  | Restart a následná duplicita | úspěch |

- Výstup testů je v ignorovaném `test-results/pytest-stage2.xml`, souhrn příjmu
  v `test-results/dataset-stage2.json`. Výsledky jsou zachyceny i zde pro Git historii.
- Přímý následný audit všech 321 hlaviček potvrdil chybu původní dokumentace:
  existuje **14 SeriesInstanceUID**, ale jen **13 SeriesNumber**. Obě TRACEW
  skupiny mají vlastní UID, stejný SeriesNumber 12 a po 26 instancích.
  Opraveny `data/README.md` a příslušné předpoklady v `docs/architecture.md`.
  Architektura nadále podporuje logické podskupiny pro jiné či budoucí vstupy.
- Ověřená sada závislostí uzamčena v `requirements-stage2.lock.txt`.

### Stav po tomto kroku

Etapa 2 je dokončená v rozsahu lokálního vývojového receiveru a výše uvedeného
podporovaného profilu. C-STORE se potvrzuje po uložení souboru i transakční evidence.
Příjem je nezávislý na GUI a fronta přežije restart.

Další krok: etapa 3 — standardní metadata, normalizace s provenance a rozhraní
vendor parserů. Zatím nejsou implementované klinické limity, obrazové hodnocení,
analytický worker, GUI, TLS, role, retence ani instalace služby OS. Nebyl proveden
test na skutečném MR ani hardwarový test výpadku napájení. Větší 32případový ZIP
v tomto kroku nebyl znovu kompletně přenášen.

## 2026-11-27 — etapa 3: metadata a normalizace

### 3.1 — rozsah

- Navazuje na ověřený receiver etapy 2, jeho síťové chování se nemění.
- Vznikne samostatný parser standardních metadat, typované hodnoty s jednotkou,
  stavem a zdrojovou cestou, rozhraní vendor adaptérů a seskupování geometrií.
- Soukromé vendor tagy se nebudou odhadovat: pouze explicitní mapování s private
  creator a identitou přístroje. Chybějící údaje zůstávají neznámé.
- Ověření: syntetická metadata včetně neplatných hodnot, konfliktů a funkčních
  skupin; následně přímý průchod hlavičkami dodané sady. Bez klinických limitů.

### 3.2 — implementace a průběžné ověření

- Přidány `dicom/metadata_parser.py`, `analysis/metadata_normalizer.py`,
  `analysis/series_aggregator.py` a vendor adaptéry pro Siemens, Philips, GE a Canon.
- Vybraná standardní pole mají stav, jednotku, původní hodnoty a zdrojové cesty.
  Explicitní mapování funkčních skupin zachovává hodnoty jednotlivých rámců.
- Privátní pole vyžadují private creator, model a verzi SW. Hash mapování je
  součástí identifikátoru adaptéru. Nejsou přednastavené neověřené vendor tagy.
- Při prvním testování opraveno čtení chybějícího tagu: pydicom `data_element`
  může vyvolat KeyError. Další testy odhalily, že PersonName je iterovatelný
  skalár a `original_string` může být None nebo bytes; doplněna správná konverze.
- Samostatná sada metadat po opravách: **41 testů prošlo**. Zahrnuje jednotky,
  NaN, soukromé tagy, konflikty, funkční skupiny, opt-in identifikátory,
  axiální/sagitální řazení řezů, rozdělení difuzních skupin a duplicity.
- Doplněn `tools/audit_metadata.py`: streamuje hlavičky ze ZIPu po případech,
  nealokuje PixelData a zapisuje souhrnné reporty bez pacientských hodnot/UID.
- Dokumentace použití a omezení je v `docs/metadata.md`.

### 3.3 — kontrola dodaných dat

| Kontrola | MR50000 | MR50101–MR50132 |
| --- | ---: | ---: |
| Objekty / záznamy metadat | 321 | 11 108 |
| Případy / studie | 1 | 32 |
| Fyzické série podle UID | 14 | 465 |
| Kandidátní geometrické skupiny | 16 | 529 |
| Skupiny s neúplnou geometrií | 1 | 32 |
| Skupiny s nesouladem deklarované rozteče | 0 | 15 |

- Oba průchody dokončeny bez dekódování pixelů a bez klinického hodnocení.
- V MR50000 má 320 MR objektů standardní TR, TE, PixelSpacing a orientaci;
  standardní difuzní b-hodnoty i gradientové směry chybí ve všech 321 objektech.
  Názvy sekvencí nejsou používány jako náhrada chybějících hodnot.
- Geometrické nálezy ve větší sadě jsou technická zjištění, nikoliv PI-QUAL skóre;
  nebyly dosud porovnány s klinickým významem anotací reportů.
- Souhrny jsou v `test-results/metadata-stage3.json` a
  `test-results/metadata-stage3-batch.json`; výsledky jsou zachyceny i zde.

### 3.4 — závěrečné ověření a stav

- **79 testů prošlo** (22,65 s), včetně všech 38 původních testů receiveru.
  Dvě varování pocházejí ze záměrně neplatných syntetických hodnot NaN a UID.
- Ruff lint i formátování prošly pro 27 Python souborů; `git diff --check` prošel.
- Po finální opravě PersonName zopakován průchod MR50000 se stejnými výsledky.
- Žádná nová externí závislost; platí dosavadní `requirements-stage2.lock.txt`.
- Etapa 3 je dokončená v rozsahu standardních metadat, podporované podmnožiny
  funkčních skupin, normalizace, vendor rozhraní a kandidátního geometrického
  seskupování. Privátní mapování reálného MR vyžaduje ověření výrobce/modelu/SW.
- Další krok: etapa 4 — verzované DEMO protokoly, identifikace sekvencí a
  validační engine. Klinické PI-QUAL limity ani automatická obrazová kvalita
  se zatím nevyhodnocují; analytický worker a DB snapshoty nejsou zapojené.

## 2026-11-27 — etapa 4: DEMO protokoly a validační engine

### 4.1 — rozsah a rozhodnutí

- Navazuje na metadata modul; receiver a jeho databáze se v tomto kroku nemění.
- Přidám striktně validované YAML/JSON protokoly, neměnné snapshoty s hashem,
  rozpoznání sekvencí s kandidáty a nejednoznačností a kontrolu všech členů skupiny.
- Konfigurace zůstane výslovně DEMO. Žádné použité číselné hodnoty nejsou limity PI-QUAL.
- Dokončení vyžaduje explicitní důkaz nebo očekávaný počet definovaný protokolem;
  samotná geometrická skupina ani pauza v přenosu neznamená dokončení.
- Obrazová kontrola bez analyzátoru zůstane nevyhodnocená. DEMO může explicitně
  vyžadovat pouze metadata; zelený výsledek pak má tento omezený význam.

### 4.2 — implementace a průběžné testy

- Přidány Pydantic modely protokolů v `protocols/models.py`, bezpečné načítání
  YAML/JSON a správa neměnných snapshotů v `protocols/manager.py`.
- Konfigurace odmítá neznámé položky, duplicitní ID/klíče, neplatné jednotky,
  záporné tolerance, nekompatibilní operátory, špatné rozsahy a záměny typů.
- Snapshot je kanonický JSON + SHA-256. Obsah existující verze se nesmí změnit;
  aktivní vyšetření drží původní snapshot a blokuje přepnutí protokolu.
  Tato správa je zatím v paměti procesu, persistenci doplní etapa 5.
- Identifikace hodnotí všechny členy a alespoň dvě odlišná metadata.
  Neznámý konkurenční kandidát brání falešné jednoznačné identifikaci.
- Engine podporuje přesnou hodnotu, množinu, interval, absolutní/procentní
  toleranci, varovná pásma, explicitní agregaci složek a politiku chybějících dat.
- Samostatně se ukládají stavy metadat, dokončení a neprovedené obrazové kontroly.
  Kritický nález má prioritu před nehotovými kontrolami; varování se neztrácí.
- Přidána kontrola přítomnosti sekvencí. Povinná nepřijatá sekvence je během
  vyšetření PENDING; až po explicitní finalizaci jde o chybu/varování dle konfigurace.
  Nejednoznačný kandidát se nepovažuje za prokázanou absenci.
- Všechna přijatá opakování zůstávají ve výsledku; dobré opakování nesmaže špatné.
- Prvních 61 testů prošlo; po rozšíření hranic tolerancí, množin a opakování
  prošlo 79 nových testů. Ještě probíhá plná regrese s dalšími vstupními kontrolami.
- Přidány `config/protocols/demo_mr.yaml` a souhrnný lokální nástroj
  `tools/validate_demo.py`. Žádné načtení ZIPu samo nepotvrzuje dokončení akvizic.

### 4.3 — závěrečné ověření

- **163 testů prošlo** (19,35 s): všech 79 předchozích testů a 84 nových.
  Dvě varování zůstávají z úmyslně neplatných syntetických NaN/UID.
- Ověřeny všechny čtyři stavy, inkluzivní hranice včetně 3,3 proti 3 ± 0,3,
  procentní tolerance, množiny, vektory, chybějící/konfliktní metadata,
  neznámé/nejednoznačné sekvence, opožděné členy a zachování chybných opakování.
- Testovány neměnné snapshoty, zamknutí protokolu během vyšetření,
  bezpečné načítání, duplicitní klíče a limit velikosti souboru.
- Ruff lint a formátování prošly; výstup testů je
  `test-results/pytest-stage4.xml`. Žádná nová externí závislost.
- Lokální průchod hlavičky → skupiny → identifikace → DEMO validace prošel
  pro obě dodané sady:

  | Ukazatel | MR50000 | MR50101–MR50132 |
  | --- | ---: | ---: |
  | Objekty | 321 | 11 108 |
  | Případy | 1 | 32 |
  | Geometrické skupiny | 16 | 529 |
  | Identifikovaní obecní T2 kandidáti | 3 | 113 |
  | Neznámé skupiny pro tento omezený DEMO protokol | 13 | 416 |
  | RED proti ukázkovým metadatovým požadavkům | 2 | 80 |
  | GRAY včetně nedokončených/neznámých skupin | 14 | 449 |

- Protokol `MR_DEMO`, verze `1.0-demo`, hash
  `c2a8a3cee24b41687bac63bbe64e279ed433eee36076b433d9e253f8cb4b1863`.
- Reporty: `test-results/validation-stage4.json` a
  `test-results/validation-stage4-batch.json`. Nebyl dekódován obraz, potvrzováno
  dokončení ani prováděna klinická interpretace barev. Výsledky se neporovnávají
  s reportovým `tier`; DEMO hodnoty nemají status klinických PI-QUAL požadavků.
- Dokumentace API, konfigurace, barev a omezení je v `docs/validation.md`.

### Stav po etapě 4

Samostatný validační engine a správa DEMO protokolů jsou hotové. Receiver zůstává
funkční, ale frontu dosud automaticky nevybírá analytický worker. Výsledky jsou
vracené jako strukturovaná data; jejich historie a vazba studií na snapshot se
zatím neukládají do DB. Pořadí sekvencí v protokolu je pořadí zobrazení,
nikoliv ověření skutečné chronologie měření.

Další krok: etapa 5 — migrace a rozšíření databázového modelu, perzistentní
snapshoty protokolů/metadat a historie výsledků s issues. Klinické limity,
obrazový analyzátor a GUI nadále nejsou implementované.

## 2026-11-27 — etapa 5: databáze a historie

### 5.1 — rozsah

- Navazuji na stávající příjmové schéma verze 1; příjmové tabulky a jejich data
  zůstanou zachované. Přidám verzované migrace a kontrolu kompatibility schématu.
- Protokoly, metadata a výsledky budou uložené jako neměnné snapshoty s hashem,
  společně s relačními vazbami a jednotlivými nalezenými odchylkami.
- Vyšetření bude trvale vázané na protokol; nové přehodnocení pod jiným protokolem
  vznikne jako explicitní větev, nikoliv přepsání historie.
- Pacientský index standardně ukládá jen lokální náhodný token, nikoliv jméno/ID.
- Automatický worker, GUI a klinické PI-QUAL hodnocení nejsou součástí tohoto kroku.

### 5.2 — migrace a doménová historie

- Přidán Alembic a balené revize `0001_intake` a `0002_history`.
- Původní neoznačené schéma verze 1 se ověří podle tabulek, sloupců, PK/UNIQUE/FK
  a konzistence referencí. Před upgradem vzniká záloha přes SQLite backup API.
- Migrace běží pod `BEGIN IMMEDIATE`; při selhání se vrátí DDL i změna revize.
  Testy ověřují i zachování původních řádků a odmítnutí neznámého schématu.
- Přidány pacientské tokeny, studie/série, protokoly/definice, větve přehodnocení,
  metadatové snapshoty, logické akvizice/členství a běhy/výsledky/issues/události.
- SQLite triggery brání přepsání/smazání historie a změně původního navázání studie.
  Nejde o ochranu proti administrátorovi, který může změnit schéma.
- Repository počítá validaci před zápisem a ukládá vstupy i výsledky atomicky.
  Při selhání nevznikne částečná historie; příjmová data zůstávají zachovaná.
- Stejný request + stejné vstupy je idempotentní i při souběhu zapisovatelů.
  Nový request přidá nový běh; pořadí určuje monotónní pořadové číslo, ne čas/UUID.
- Přidána obnova skupin a kontrola hashů; metadata roundtrip zachovává tuple i zdroje.
- Nové nepopsané příchozí instance značí zastaralost a brání prezentaci staré zelené
  jako aktuální. Historická RED při nehotových dalších datech zůstává viditelná.
- Explicitní finalizace vyžaduje metadata všech přijatých instancí studie.
- Do indexu pacientů se ukládají náhodné tokeny; kopie přímých identifikátorů
  v metadatových snapshotech repository odmítá. Identita se mezi studiemi neslučuje.

### 5.3 — průběžné ověření

- 20 původních testů bezpečného ukládání/obnovy prošlo s novým migračním otevřením DB.
- Prvních 43 testů migrací a historie prošlo. Po doplnění detekce pozdních dat
  prošla celá sada 207 testů; následně doplněna přísnější kontrola legacy PK/UNIQUE/FK
  a test schématu se správnými sloupci, ale chybějícím primárním klíčem.
- Ověřeny transakční rollbacky, opakované requesty, souběh zápisů, restart,
  explicitní přehodnocení, append-only triggery a trvalé navázání protokolu.
- Lokální průchod MR50000 do dočasného úložiště a historie prošel:

  | Ukazatel | Počet |
  | --- | ---: |
  | Přijaté DICOM instance | 321 |
  | Metadatové snapshoty | 321 |
  | Akviziční snapshoty | 16 |
  | Běhy vyhodnocení | 1 |
  | Dílčí výsledky včetně přítomnosti sekvence | 17 |
  | Nezelené nálezy / záznamy stavu | 95 |

- Po znovuotevření DB načten stejný protokol i výsledek. Obnovené akvizice
  reprodukovaly vstupní hashe enginu a celkový DEMO stav. Opakovaný request
  nevytvořil druhý běh. Neproběhla klinická interpretace ani automatická finalizace.
- `tools/smoke_history.py` používá dočasné úložiště a zapisuje jen souhrnný report
  `test-results/history-stage5.json`; žádné identifikátory se nevypisují do reportu.
- Nové závislosti jsou uzamčené v `requirements-stage5.lock.txt`; `pip check` prošel.
- Aktuální API, migrace, obnova a provozní hranice jsou popsány v `docs/database.md`.

### 5.4 — závěrečné ověření a stav

- **208 testů prošlo** (44,76 s): všech 163 původních testů a 45 nových.
  Dvě varování jsou záměrně neplatné syntetické NaN/UID z předchozích etap.
- Ruff lint i formátování prošly pro 47 Python souborů. Po finální úpravě čtení
  ZIPu zopakován integrační test historie bez varování a se stejnými počty/hashovými
  kontrolami. JUnit výstup je v `test-results/pytest-stage5.xml`.
- Etapa 5 je dokončená: migrace, trvalé navázání protokolu, větve přehodnocení,
  metadata/akvizice a historie výsledků/issues jsou dostupné přes repository API.
- Databázový průchod byl ověřen na MR50000; větší 32případová sada se v této etapě
  znovu celá neukládala do databáze. Dřívější metadata/DEMO průchody zůstávají platné.
- Další krok: etapa 6 — jednookenní PySide6 GUI nad historií a aktuálními stavy.
  Automatické vybírání příjmové fronty a kompletní provozní propojení budou součástí
  integrace. Klinické PI-QUAL hodnocení, obrazový analyzátor a role stále nejsou hotové.

## 2026-11-27 — etapa 6: jednookenní GUI

### 6.1 — rozsah

- Přehled vyšetření, požadovaných sekvencí, jednotlivých akvizic, metadat a historie.
- PySide6 zůstane volitelnou závislostí, aby receiver nepotřeboval GUI knihovny.
- GUI bude databázi pouze číst, nebude provádět migrace ani startovat/vlastnit receiver.
- Čtení snapshotů poběží v samostatném Qt vlákně, s jedním souběžným požadavkem.
- Monitoring receiveru doplní samostatný heartbeat bez pacientských údajů;
  stará nebo chybějící zpráva se nebude zobrazovat jako aktivní spojení.
- Pro ověření vznikne syntetická DEMO studie se všemi čtyřmi barvami a testy
  neblokujícího GUI, zachování výběru a příjmu po uzavření okna.

### 6.2 — implementace

- Přidán samostatný modul `gui/` s read-only datovým readerem, hlavním oknem,
  tabulkou sekvencí, detailem kontrol, líně rozbalovanými metadaty a historií.
- GUI otevírá SQLite pouze `mode=ro` + `query_only`, bez migrací nebo zásahů do receiveru.
- Jeden Qt worker čte konzistentní snapshot; další požadavky se slučují. Generace
  výběru brání použití opožděné odpovědi pro předchozí studii.
- Při obnově stejných dat zůstává vybraný řádek i rozbalená metadata zachovaná.
  Při přepnutí studie se starý detail ihned odstraní. Chyby čtení maskují starou
  zelenou, ale nezakrývají historickou kritickou odchylku.
- Tabulka zobrazuje přijaté nezpracované série i bez analytické historie. Více
  akvizic jedné definice má vlastní dílčí řádky a nejednoznačné přiřazení má důkazy.
- Header používá výslovně lokální pacientský token; PatientID/jméno/accession
  nejsou zobrazené. Všechny texty se vykreslují jako prostý text.
- Přidán lokální atomický `receiver_status.json`, perioda 2 s, hranice zastaralosti
  7 s. Stav naslouchání je oddělený od počtu aktivních asociací. Soubor obsahuje
  pouze provozní stav a čítače, nikoliv pacientské hodnoty nebo instance UID.
- Selhání heartbeat nesmí zablokovat příjem. Ukončení GUI je asynchronní a nevlastní
  životní cyklus receiveru; vlákno čtení při zavření doběhne bez blokování UI.
- Přidány syntetická ukázka `config/protocols/gui_demo.yaml`, bezpečný seed nástroj
  a render skutečného Qt okna pro vizuální kontrolu. Seed odmítá neprázdné úložiště.

### 6.3 — ověření a vizuální kontrola

- Prvních 16 datových/Qt testů prošlo. Následně doplněna skutečná kontrola heartbeat
  při síťovém C-STORE: počty, duplicita, konflikt, aktivní asociace a zastavení.
- Ověřen responzivní event loop při zablokovaném čtení, nejvýše jeden souběžný
  požadavek, zachování výběru, zahazování starého výsledku a maskování chyby čtení.
- Ověřeno, že skutečný localhost C-STORE funguje i po uzavření okna GUI.
- První offscreen render odhalil prázdnou Qt databázi systémových písem. Doplněno
  explicitní načtení dostupného Unicode písma pro prostředí bez nativní detekce.
  Nový render je čitelný a byl vizuálně zkontrolován.
- Stavová buňka zachovává barvu také při označení celého řádku; barvu doplňuje
  text/symbol. Náhled obsahuje všechny čtyři stavy a oddělené vrstvy hodnocení.
- Finální celá sada: **225 testů prošlo** (56,63 s), včetně všech 208 předchozích.
  Dvě varování jsou záměrně vadné syntetické NaN/UID z dřívějších etap.
- Ruff lint a formátování prošly pro 62 Python souborů; `pip check` a CLI `--help`
  prošly. GUI závislosti jsou uzamčené v `requirements-stage6.lock.txt`.
- JUnit: `test-results/pytest-stage6.xml`; vizuálně zkontrolovaný render:
  `test-results/gui-stage6.png`. Testovací studie obsahuje jen 11 syntetických objektů.

### Stav po etapě 6

Jednookenní GUI nad historií a provozním stavem je hotové. Spuštění, DEMO a hranice
jsou v `docs/gui.md`. Automatický analytický worker zatím nová data nezpracovává;
GUI ukazuje uložené výsledky a přijímaná data čekající na analýzu. Neproběhlo
klinické vyhodnocování ani test na skutečném MR.

Další krok: etapa 7 — automatický worker příjmové fronty, propojení listeneru,
metadat, enginu a historie, kompletní workflow a lokální spuštění komponent.

## 2026-11-27 — etapa 7: lokální integrace

- Samostatný proces analýzy čte příjmovou frontu SQLite, ověřuje uložené objekty,
  znovu sestaví dotčené vyšetření a ukládá výsledek pod původní verzí protokolu.
  Výsledek a dokončení úloh zapisuje v jedné transakci.
- GUI zobrazí změny po příchodu dalších snímků a rozliší selhání analýzy od
  čekajících dat. Secondary Capture zůstává v evidenci příjmu mimo MR hodnocení.
- Integrační zkoušky pokrývají skutečný lokální C-STORE, stav čtený GUI, restart,
  pozdější snímky, pevnou verzi protokolu a duplicitu. Další zkouška ověřila
  návrat po selhání databázového zápisu a obnovu poškozeného vstupu.
- Prošlo 227 testů. Dvě varování pocházejí ze záměrně vadných syntetických
  metadat. Prošly také kontroly Ruff a konzistence závislostí.
- Menší dodaný archiv prošel přes localhost. Přijímač přijal všech 321 objektů
  ze 14 fyzických sérií. Analýza zpracovala 320 MR objektů, přeskočila jeden
  Secondary Capture a GUI načetlo jedno vyšetření bez čekajících MR objektů.
  Přijímač po restartu správně rozpoznal duplicitní přenos.
- Lokální spuštění a obnova jsou popsány v [návodu k integraci](integration.md).
  Automatická kontrola používá DEMO protokol a neuzavírá vyšetření.

## 2026-11-28 - výchozí protokol PROSTATE_MOU

- Windows spouštění pro start i odesílání předává analytickému procesu
  `config/protocols/prostate_mou.yaml` místo `demo_mr.yaml`.
- README a příkaz pro ruční spuštění analýzy odpovídají protokolu PROSTATE_MOU.
- Změna vyžaduje zastavení a nové spuštění analýzy. Nová vyšetření dostanou
  PROSTATE_MOU; existující vyšetření zachovají uložený protokol a historii.
- Prošla kontrola PowerShell AST, argumentu --protocol, existence souboru,
  identifikátoru PROSTATE_MOU a git diff --check.
- Běhové ověření načtení YAML ani Python testy neproběhly: .venv/Scripts/python.exe
  odkazuje na nepřístupnou instalaci Pythonu z WindowsApps.
