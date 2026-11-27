# Metadata a normalizace — etapa 3

Modul funguje samostatně, bez GUI, dekódování pixelů a klinických limitů.
Receiver nadále pouze přijímá data a vytváří trvalé úlohy. Od etapy 5 ukládá
metadatové snapshoty samostatné API `HistoryRepository`, popsané v `database.md`.
Automatické napojení na analytický worker přijde v další integraci.

## Použití

```python
from pathlib import Path
from mr_quality_monitor.dicom.metadata_parser import parse_file
from mr_quality_monitor.analysis.series_aggregator import group_acquisitions

records = parse_file(Path("soubor_bez_pripony"))
tr = records[0].fields["RepetitionTime"]
print(tr.state, tr.value, tr.unit)
groups = group_acquisitions(records)
```

`parse_dataset(dataset)` pracuje s již načteným datasetem. `parse_file(path)`
používá `stop_before_pixels=True`; nikdy nepřistupuje k `pixel_array`.
Výstup obsahuje jeden `MetadataRecord` pro každý podporovaný rámec.

## Hodnoty a provenance

| Stav | Význam |
| --- | --- |
| `known` | Hodnota má platný typ a dostupné zdroje se shodují |
| `missing` | Prázdný/chybějící údaj nebo potlačený identifikátor |
| `invalid` | Neplatný typ, násobnost, jednotka, ne konečná číselná hodnota nebo nepodporovaný binární obsah |
| `conflicting` | Zdroje si odporují; prioritní hodnota zůstává dostupná společně s konfliktem |

`record.known(keyword)` vrací hodnotu pouze ve stavu `known`, jinak `None`.
`fields[keyword]` poskytuje také jednotku, důvod a všechny zdrojové tagy/cesty.
Nula se nikdy nezaměňuje za chybějící údaj. Raw zdroj zachovává původní desetinný
zápis, pokud jej pydicom poskytuje; normalizované numerické hodnoty používají float.
`to_dict()` je snapshot vhodný pro JSON, obsahuje však skutečné hodnoty a není
určený k provoznímu logování.

Zpracovány jsou identifikátory studie/série/instance a parametry ze zadání:
TR, TE, TI, úhel, tloušťka/rozteč řezů, PixelSpacing, rozměry, akviziční matice,
síla pole, počet průměrování, echo train, typ/směr akvizice a bandwidth. Názvy
klíčů jsou DICOM keywords, například `RepetitionTime`, `EchoTime`,
`InPlanePhaseEncodingDirection`. Poslední údaj se nevydává za anatomický směr.

Jednotky: časové intervaly ms, vzdálenosti mm, úhel deg, pole T, bandwidth
Hz/pixel, difuzní b-hodnota s/mm². Explicitní vendor převody podporují s → ms
a cm → mm; neznámé jednotky se neodhadují. DICOM datum/čas zůstává původním
textem se samostatnou informací o zóně; chybějící zóna se nedoplňuje.

PatientID, PatientName, IssuerOfPatientID a AccessionNumber jsou standardně
potlačené. Jejich explicitní načtení umožňuje `include_identifiers=True`.
Toto nastavení není anonymizace: UID, jiné volné texty a původní soubor mohou
stále obsahovat nebo umožnit dohledat citlivé údaje.

## Funkční skupiny

Podporována je explicitní podmnožina Shared/Per-frame Functional Groups:
TR a flip angle, EffectiveEchoTime, pixel measures, poloha/orientace, difuze
a TemporalPositionIndex. Priority jsou per-frame, shared, top-level a vendor.
Odlišné neprázdné hodnoty vedou ke konfliktu místo tichého přepsání.

Počet per-frame položek musí souhlasit s NumberOfFrames. Sdílená skupina má
nejvýše jednu položku a podporované makro právě jednu položku; chybná struktura
vyvolá `MetadataStructureError`. Nejde o úplnou implementaci Enhanced MR IOD.
Například alternativní enhanced formy inversion timing zatím nejsou mapované.
Rozsah podpory listeneru se tím nerozšiřuje: nadále platí `receiver.md`.

## Vendor adaptéry

`vendors/base.py` poskytuje rozhraní, `siemens.py`, `philips.py`, `ge.py`
a `canon.py` konkrétní rozšiřující třídy. Automatická volba používá přesné
normalizované názvy výrobců; neznámý výrobce dostane generic parser.
Žádné produkční privátní tagy nejsou přednastavené bez ověření na přístroji.

`PrivateBinding` obsahuje normalizované pole, lichou skupinu, private creator,
offset, jednotku, model a verzi softwaru. Pole se hledá přes private creator,
nikoliv podle předpokládaného umístění bloku. Binding vyžaduje shodu modelu
a verze softwaru; nesouhlas výrobce je chyba. Identifikátor adaptéru obsahuje
i hash jeho explicitních mapování pro reprodukovatelnost konfigurace.

Standardní hodnota má přednost, nesouhlas vendor hodnoty však zůstane viditelný
jako `conflicting`. Binární CSA struktury a podobné formáty vyžadují vlastní
ověřený dekodér; obecný adaptér je neinterpretuje. Všechna privátní mapování
v unit testech jsou syntetická a nesmí se převzít jako mapování skutečného MR.

## Geometrické skupiny

`group_acquisitions()` rozlišuje UID studie/série, geometrii, rozměry, pixel
spacing, image type, echo, dostupné difuzní/časové dimenze a číslo akvizice.
SeriesNumber ani SeriesDescription nejsou klíčem. Více difuzních skupin uvnitř
jedné série se rozdělí podle skutečně dostupných b-hodnot a směrů.

Řezy řadí projekce ImagePositionPatient na normálu spočtenou z orientace;
InstanceNumber ani souborové názvy pořadí neurčují. Kontroluje se ortonormalita,
kladné rozměry/rozteče, opakovaná poloha, nepravidelná vzdálenost a nesouhlas
s deklarovanou SpacingBetweenSlices. Tolerance rozteče je argument funkce;
výchozí 0,05 mm je technická tolerance, nikoliv požadavek PI-QUAL.
Rozdílné orientace se konzervativně rozdělí do různých skupin.

Neúplná/konfliktní geometrie se izoluje. Identická opakovaná instance/rámec
nezvyšuje počet členů; odlišná metadata stejné identity jsou chyba.
Chybějící volitelné dimenze zůstávají neznámé. Jde o kandidátní skupiny,
nikoliv záruku správně sestaveného diagnostického objemu. Parser pixely
nestohuje a každá skupina má `complete=False`: geometrie nedokazuje konec série.

## Audit dat

```powershell
.\.venv\Scripts\python.exe tools\audit_metadata.py data\DICOMDIR_MR50000_ukazka_jednoho_mereni.zip --report test-results\metadata-stage3.json
.\.venv\Scripts\python.exe tools\audit_metadata.py data\DICOMDIR_MR50101-MR40132.zip --report test-results\metadata-stage3-batch.json
```

ZIP se nerozbaluje, čtou se pouze hlavičky a v paměti zůstávají záznamy právě
jednoho případu. Report obsahuje jen počty, stavy polí a technické nálezy,
bez pacientských hodnot a UID. `passed` znamená dokončený technický průchod,
nikoliv potvrzení všech parametrů ani klinické kvality.

V MR50000 má 320 MR objektů dostupné TR, TE, PixelSpacing a orientaci.
Ve všech 321 objektech chybí standardní DiffusionBValue a gradientový směr.
Parser je neodvozuje z SequenceName. DWI skupiny se stejným SeriesNumber
jsou v tomto ZIPu oddělené už skutečným SeriesInstanceUID.
Výsledky testů a auditů jsou v `implementation_log.md`.
