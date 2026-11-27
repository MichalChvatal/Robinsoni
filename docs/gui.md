# Jednookenní GUI — etapa 6

GUI je samostatný PySide6 proces nad lokální databází a uloženou historií.
Neprovádí klinické hodnocení PI-QUAL, nespouští receiver a nemění jeho databázi.
Zobrazuje výhradně výsledky DEMO protokolů a nevyhodnocená příchozí data.
Kompletní spuštění včetně automatické analýzy popisuje [etapa 7](integration.md).

## Instalace a spuštění

Z kořene repozitáře:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[gui,test]"
.\.venv\Scripts\python.exe -m mr_quality_monitor.gui --config config\app_config.yaml
```

GUI samo nevytvoří databázi ani neprovede migraci. Pokud databáze ještě neexistuje,
ukáže čekání na data. Přijímač se spouští samostatně v dalším terminálu:

```powershell
.\.venv\Scripts\python.exe -m mr_quality_monitor.cli --config config\app_config.yaml
```

Zavření nebo restart GUI neukončí receiver. PySide6 je volitelná závislost;
samotný receiver ji neimportuje. Ověřený kompletní seznam verzí této etapy je
v `requirements-stage6.lock.txt`, předchozí backend-only seznam je v
`requirements-stage5.lock.txt`.

## Syntetická ukázka všech stavů

```powershell
.\.venv\Scripts\python.exe tools\seed_gui_demo.py --root runtime\gui_demo
.\.venv\Scripts\python.exe -m mr_quality_monitor.gui --config runtime\gui_demo\viewer_config.yaml
```

`seed_gui_demo.py` vyžaduje nový/prázdný adresář. Existující úložiště nepřepíše.
Vytvoří 11 syntetických jednorámcových DICOM objektů, protokol `GUI_DEMO` a čtyři
řádky: GREEN, ORANGE, RED a GRAY. Veškeré identity i pixely jsou syntetické,
číselné hodnoty neznamenají klinické limity PI-QUAL. Pro další ukázku zvolte nový
adresář; nástroj záměrně nemaže starší data.

Ukázka nepředstírá běžící receiver. Bez skutečného čerstvého heartbeat ukáže
„stav neznámý“. Samotná naplněná databáze není důkaz aktivního DICOM příjmu.

## Hlavní okno

- Výběr studie zachovává aktuální vyšetření i při příchodu nové studie.
- Výchozí přehled ukazuje celkový stav a jednosloupcový semafor. Barvu a symbol
  každého řádku vysvětluje legenda nad tabulkou.
- Tlačítko **Podrobnosti vyšetření** rozbalí místní pacientský token, datum,
  přístroj, protokol, stav příjmu a volbu větve historie.
- Více dílčích akvizic jedné definice je dostupných v samostatných řádcích.
- Neznámé a právě přijaté nezpracované série se zobrazí samostatně šedě.
- Kliknutí na řádek nebo tlačítko **Detail sekvence** otevře skutečné a očekávané
  hodnoty, počet snímků, nálezy, zdrojové tagy a historii stavů vybrané sekvence.
- U červené sekvence se nejdřív zobrazí přehled **Proč je sekvence červená?**
  Uvede konkrétní kritické nálezy a jejich požadavky podle protokolu. Opakování
  téhož nálezu sloučí a označí zastaralý výsledek.

Barvu doplňuje slovní stav a symbol. Zelená znamená pouze splnění metadatového
rozsahu konkrétního DEMO protokolu. V detailu jsou oddělené stavy metadat,
dokončení a neprovedené obrazové kontroly. Výsledek nepřikazuje klinické opakování
sekvence; akce pocházejí z DEMO pravidel.

Metadata lze rozbalovat po parametrech na jednotlivé instance/rámce.
Dětské řádky se vytvářejí až při
rozbalení, aby velká studie nezablokovala vykreslení tisíci položek.

PatientID, PatientName a AccessionNumber se v tomto režimu nezobrazují, protože
pro jejich kopírování nebyla stanovena oprávnění a identifikační politika.
Pacientský token se výslovně neoznačuje jako skutečný PatientID. Jde o lokální
pseudonym, nikoliv anonymizaci; UID, popisy a ostatní DICOM data mohou být citlivá.

## Obnovování a chyby čtení

Snapshot se čte přibližně jednou za sekundu v odděleném Qt vlákně. Databáze se
otevírá `mode=ro` a `query_only`; čtení používá jednu konzistentní transakci,
nikoliv spojení sdílené s receiverem. Ukládané kontrolní součty se ověřují při
načtení protokolu, výsledku a metadatových/akvizičních snapshotů. Neměnná metadata
posledního běhu se pro rychlou obnovu mohou cachovat v paměti.

V běhu je nejvýše jeden požadavek na čtení. Další obnovy se sloučí. Každá změna
výběru má vlastní generaci: opožděná odpověď původní studie se nezobrazí nad novým
pacientem. Při změně studie se předchozí detail hned odstraní. Při obnově stejných
dat zůstává zachovaný řádek i rozbalený detail.

Pozdní příchozí data nebo chyba čtení se viditelně označí. Stará zelená se zobrazí
šedě, historická kritická odchylka zůstává červená. Chyba čtení nevynutí přepnutí
na jinou studii a do UI se nepřenáší text výjimky obsahující citlivé hodnoty.
Zavření okna počká na dokončení čtení asynchronně, bez blokujícího `wait()` v UI.

GUI zobrazuje nejnovější běh zvolené větve a posledních 100 běhů historie.
Seznam nabízí posledních 500 studií; již zvolenou starší studii zachová.

## Stav receiveru

Receiver zapisuje atomický `receiver_status.json` každé dvě sekundy. Obsahuje
čas heartbeat, identifikátor běhu, skutečný bind/AE, počet aktivních asociací,
počet unikátních objektů, poslední příjem a pevný kód poslední provozní chyby.
Neobsahuje pacientské údaje, SOP Instance UID ani obsah datasetů.

GUI rozlišuje naslouchání bez asociace, aktivní asociace, zastavení, starý heartbeat
a neznámý stav. Zpráva starší než sedm sekund nebo výrazně v budoucnosti se
nepovažuje za důkaz aktuálně běžící služby. Nejde o ověřování sítě TCP sondou;
samotný stav „naslouchá“ neznamená trvalé spojení s MR ani dokončení vyšetření.
Čítače a počty asociací mohou mít prodlevu danou intervalem heartbeat.

Selhání zápisu monitoringu neovlivní potvrzení uloženého C-STORE. GUI místo toho
uvidí chybějící/zastaralou zprávu. Historická poslední chyba se zobrazuje s časem,
není automaticky interpretována jako stále nefunkční přijímač.

## Testy a skutečný render

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\render_gui.py --config runtime\gui_demo\viewer_config.yaml --output test-results\gui-semafor.png
```

Qt testy používají offscreen platformu a syntetická data. Je-li PySide6 nepřítomné,
Qt testy se přeskočí; backend a čisté datové testy lze provozovat samostatně.
`render_gui.py` vytvoří obraz skutečného okna bez startu receiveru. Pro prostředí
bez nativní detekce písem GUI načte dostupné systémové Unicode písmo explicitně.

Ověřeno: čtyři barvy a detaily, líné rozbalování metadat, responzivní event loop
při pomalém čtení, sloučení požadavků, zahazování staré odpovědi, zachování výběru,
maskování zastaralé zelené a skutečný C-STORE po uzavření GUI.

Při běžící analýze se výsledky nově přijatých dat objeví po automatickém obnovení
přehledu. Spuštění příjmu a analýzy popisuje [návod k integraci](integration.md).
