# Technická kontrola MRI prostaty MOÚ

Tento návod popisuje referenční technické zkoušky se starším profilem
`tests/fixtures/protocols/mou_prostate.yaml` v režimu DEMO. Běžné spuštění
používá `config/protocols/prostate_pirads_v2_1.yaml`. Dříve založená vyšetření
mají verzi protokolu uloženou v historii. Pro zopakování stejného archivu
pod jiným protokolem použijte samostatné testovací úložiště.

1. Přijměte vyšetření obvyklým způsobem. Kritické odchylky technických hodnot
   se mohou zobrazit už během přenosu.
2. Po dokončení přenosu otevřete **Podrobnosti vyšetření → Uzavřít vyšetření**.
   Aplikace ověří, že má zpracované všechny přijaté MR objekty. Poté vyhodnotí
   úplnost T2W a DWI sady. Pokud přijdou další snímky, vyšetření se po jejich
   zpracování opět otevře.
3. Klikněte na sekvenci. Přehled ukáže konkrétní nález, skutečnou hodnotu,
   požadavek protokolu a počet dotčených snímků, pokud je k dispozici.

### Jak se čtou technické údaje

- FOV znamená fyzický rozsah rekonstruovaného obrazu v obou osách. Počítá se
  z rozměru obrazu a Pixel Spacing, s rozlišením výpočtu na 0,01 mm. Rozlišení
  obou os je čtené z Pixel Spacing s přiřazením podle In-plane Phase Encoding
  Direction. Jde o rozlišení rekonstruovaného obrazu. Zdroj odlišuje rozměry
  po rekonstrukci i akviziční matici před rekonstrukcí.
  [DICOM MR Modules](https://dicom.nema.org/medical/dicom/2020c/output/chtml/part03/sect_C.8.3.html) ·
  [DICOM Image Plane Module](https://dicom.nema.org/MEDICAL/DICOM/current/output/chtml/part03/sect_C.7.6.2.html)
- Nominální mezera mezi řezy se určuje z deklarované rozteče mezi středy řezů
  po odečtení tloušťky. Kontrola skutečných poloh a roztečí je v geometrické
  kontrole. Chybějící údaj zůstává šedý.
- Pořadí privátních zdrojů b-hodnot nastavuje `prostate_checks.private_b_values`.
  Výchozí konfigurace byla ověřena pro Siemens MAGNETOM Vida, syngo MR XA60.
  Při rozporu mezi zdroji přehled upozorní na nejednoznačnou hodnotu.
- Pro stanovení shody DWI s axiální T2W je nutné doplnit schválené poziční a
  úhlové tolerance. Bez nich přehled ukazuje naměřené odchylky bez verdiktu
  geometrie. Pro kontrolu přítomnosti RESOLVE se konfigurují ověřené kombinace
  `ProtocolName` a `SequenceName`. Nalezenou sérii musí posoudit obsluha.
- Technické hodnocení neobsahuje obrazové vady ani klinické skóre. U technických
  změn v referencích bez předepsaného limitu, například TE nebo TR, je potřeba
  pravidlo nejprve doplnit do požadavků a následně verzovat protokol.

### Ověření reportů

Semafor MOÚ ukazuje sedm sekvencí. Opakovaná měření najdete v detailu sekvence
na kartě **Jednotlivé akvizice**. Souhrnné kontroly a další přijaté série jsou
v **Podrobnostech vyšetření**.

Čtyři regresní případy MR50000, MR50116, MR50123 a MR50111 lze spustit příkazem
`.venv/Scripts/python.exe -m pytest -q tests/test_reference_measurements.py`.
Test MR50000 používá referenční soubory z `data/mereni`.

| Měření | Co ukázat po uzavření |
|---|---|
| MR50000 | Po příjmu 321 objektů je vyšetření zelené. Kliknutím na červenou sagitální T2W se zobrazí FOV 230 × 230 mm při povoleném rozsahu 120–200 mm. Koronální a axiální rovina splnily požadované minimum dvou rovin. Report obsahuje pouze celkový verdikt. |
| MR50116 | Nízká DWI ukáže b-hodnotu 251 s/mm² při maximu 100 s/mm². Střední DWI má vlastní výsledek, přestože má stejné číslo série. |
| MR50123 | V detailu červené axiální T2W je nález u tloušťky řezu. Karta jednotlivých akvizic ukáže i výsledek opakování. |
| MR50111 | Sagitální a koronální T2W nebyly přijaty. V podrobnostech vyšetření je jedna vyhovující rovina ze dvou požadovaných. |

Z kořene projektu spusťte místní porovnání. Výstup uvádí párování a porovnání
stavů, doložené důvody a anotace mimo rozsah technických kontrol.

```powershell
.venv/Scripts/python.exe tools/verify_prostate_references.py data/DICOMDIR_MR50000_ukazka_jednoho_mereni.zip data/DICOMDIR_MR50101-MR40132.zip --output test-results/mou-reference-results.json
```

### Výsledek porovnání dodaných 33 měření

Z 248 referenčních řádků pro sekvence a jejich kopie bylo 43 ve shodě a 7 se
lišilo. Párování bylo ve všech případech jednoznačné. U 182 řádků report
neuváděl verdikt sekvence. Dalších 16 mělo verdikt založený na parametru,
pro který protokol MOÚ neudává mez. Všech šest sérií označených jako
odstraněné skutečně v datech chybělo.

- **MR50125 až MR50131** mají shodný rozpor. Report označuje druhou
  sagitální T2W za vyhovující. Ve všech sedmi případech aplikace naměřila
  FOV 230 × 230 mm a použila předepsaný rozsah 120–200 mm.
- **Celé vyšetření** se shodovalo ve 14 případech, u 15 report celkový
  verdikt neuváděl. U čtyř byl výsledek aplikace zelený a report uváděl
  `inadequate`. Referenční nález se týkal velikosti pixelu DWI (MR50115 a
  MR50119), času opakování TR (MR50118) nebo času echa TE (MR50122).
  K hodnocení těchto změn v aplikaci chybějí předepsané limity DWI.
