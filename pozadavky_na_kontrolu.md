# Požadavky na úpravu aplikace pro kontrolu kvality MRI prostaty

## Úvod

Na základě nových požadavků a připomínek od MOÚ je potřeba rozšířit logiku hodnocení kvality MRI prostaty. Tento dokument shrnuje požadované změny aplikace, validační pravidla a doporučení pro budoucí rozvoj systému.

---

# 1. Kontrola T2W sekvencí

## Podporované sekvence

| ProtocolName | SequenceName |
|-------------|-------------|
| t2_tse_sag_DRB | *tse2d1_17 |
| t2_tse_cor | *tse2d1_16 |
| t2_tse_tra | *tse2d1_16 |

## Validační pravidla

Vyšetření splňuje požadavky pouze v případě, že jsou správně naměřeny alespoň dvě T2W sekvence.

Preferovaná kombinace:

- TRA + COR
- TRA + SAG

Ideálně by měla být vždy přítomna axiální (TRA) sekvence.

### Požadované parametry

| Parametr | Hodnota |
|-----------|----------|
| Slice Thickness | ≤ 3 mm |
| Slice Gap | 0 mm |
| FOV | 120 až 200 mm |
| Phase Resolution | ≤ 0,7 mm |
| Frequency Resolution | ≤ 0,7 mm |

## Požadovaná implementace

Aplikace musí:

- identifikovat všechny podporované T2W sekvence,
- vyhodnotit jejich technické parametry,
- ověřit přítomnost minimálně dvou validních T2W sekvencí,
- upozornit na chybějící axiální (TRA) sérii.

---

# 2. Kontrola DWI (Diffusion) sekvencí

## Podporované sekvence

| ProtocolName | SequenceName |
|-------------|-------------|
| ep2d_diff_zoomit_b50_800_tra_DRB | *ez_b50t |
| ep2d_diff_zoomit_b50_800_tra_DRB | *ez_b800t |
| ep2d_diff_zoomit_b50_800_tra_DRB | *ez_b50_800 |
| ep2d_diff_zoomit_b50_800_tra_DRB | *ez_calc_b2000 |

## Povinné série

Vyšetření musí obsahovat všechny následující série:

- low b-value
- intermediate b-value
- high b-value
- ADC mapu (`*ez_b50_800`)

### Požadované parametry

| Parametr | Hodnota |
|-----------|----------|
| Slice Thickness | ≤ 4 mm |
| Slice Gap | 0 mm |

## Kontrola kvality DWI

Všechny čtyři DWI série musí být:

- přítomné,
- technicky správné,
- bez kritických chyb.

Pokud některá z požadovaných DWI sérií:

- chybí,
- nesplňuje validační kritéria,
- nebo je vyhodnocena jako nekvalitní,

musí být provedena a zkontrolována sekvence RESOLVE.

### Implementace

Doplnit validační logiku:

```text
Pokud není kompletní a validní sada DWI,
ověřit přítomnost odpovídající RESOLVE sekvence.
```

---

# 3. Kontrola b-value

## Důležitá poznámka

Skutečnou hodnotu b-value nelze spolehlivě získávat z názvu sekvence (`SequenceName`).

Hodnota uvedená v názvu série nemusí odpovídat reálné hodnotě použité při měření.

## Zdroj dat

Aplikace musí číst b-value z privátních DICOM tagů.

Podporované tagy:

```text
(0019,100C)
(0021,1105)
```

## Požadované rozsahy

| Kategorie | Rozsah |
|------------|---------|
| Low | 0–100 |
| Intermediate | 800–1000 |
| High | ≥ 1400 |

## Administrátorské nastavení

Doplnit možnost konfigurace zdroje b-value.

Například:

```text
Pořadí čtení b-value tagů:

1. (0019,100C)
2. (0021,1105)
```

Admin musí mít možnost:

- změnit pořadí tagů,
- zvolit preferovaný tag,
- definovat záložní tag.

---

# 4. Kontrola oblasti prostaty

## Segmentace prostaty

Ve vyšetření je k dispozici automaticky vytvořená segmentace prostaty:

| ProtocolName |
|-------------|
| AA_Prostate_Segm |

Obvykle:

```text
SeriesNumber = 7
```

## Využití v aplikaci

Segmentaci lze využít pro:

- kontrolu anatomického pokrytí,
- ověření, že prostata leží v zobrazované oblasti,
- kontrolu rozsahu měření,
- budoucí automatizované kontroly kvality.

---

# 5. Kontrola geometrické shody DWI a T2 TRA

## Požadavek

DWI řezy musí být geometricky zarovnány se sérií:

```text
t2_tse_tra
```

Počet řezů může být odlišný.

Požadováno je, aby řezy existující v obou sériích byly ve stejné poloze ("slice-to-slice").

## Kontrolované parametry

Aplikace musí porovnávat:

- Image Position Patient
- Image Orientation Patient
- rozteče mezi řezy
- směr akvizice

## Výsledek kontroly

Příklad:

```text
✓ DWI a T2 TRA jsou správně zarovnány
```

nebo

```text
✗ DWI a T2 TRA nejsou ve shodné poloze
```

## Otevřený bod

Je potřeba definovat maximální povolenou toleranci odchylky mezi řezy.

---

# 6. Rozšíření analytiky a administrace

## Statistiky podle laboranta

Zdroj:

```text
Operators Name (0008,1070)
```

Sledované metriky:

- počet vyšetření,
- počet nevalidních studií,
- úspěšnost,
- nejčastější chyby.

### Příklad reportu

| Operátor | Vyšetření | Chyby | Úspěšnost |
|----------|-----------|--------|----------|
| Operátor A | 120 | 8 | 93 % |
| Operátor B | 95 | 15 | 84 % |

---

## Statistiky podle sekvence

Sledovat:

- nejčastější chybové sekvence,
- dlouhodobé trendy,
- opakující se problémy.

Příklad:

| Sekvence | Počet chyb |
|-----------|-----------|
| T2 TRA | 35 |
| DWI | 22 |
| ADC | 9 |

---

## Korelace s charakteristikami pacienta

Doporučeno ukládat anonymizované údaje:

- hmotnost pacienta,
- BMI (pokud dostupné),
- věk.

Možné využití:

- analýza vlivu hmotnosti na kvalitu DWI,
- sledování výskytu artefaktů,
- identifikace rizikových faktorů vedoucích k opakování akvizice.

---

# Navrhované nové moduly aplikace

## T2W Compliance Checker

Kontrola:

- přítomnosti T2W sérií,
- technických parametrů,
- minimálního počtu validních sekvencí.

## DWI Compliance Checker

Kontrola:

- přítomnosti všech DWI sérií,
- kvality DWI,
- nutnosti použití RESOLVE.

## B-Value Validator

Kontrola:

- načtení hodnot z privátních tagů,
- zařazení do kategorií low/intermediate/high.

## Geometry Validator

Kontrola:

- shody DWI a T2 TRA,
- orientace a pozice řezů.

## Prostate Coverage Validator

Kontrola:

- anatomického pokrytí pomocí AA_Prostate_Segm.

## Quality Analytics Module

Přehledy:

- podle laborantů,
- podle sekvencí,
- podle typu chyb,
- trendové statistiky kvality.

---

# Otevřené body k dořešení

1. Přesná definice podmínek pro označení DWI série jako "nekvalitní".
2. Pravidla pro uznání RESOLVE jako náhradní sekvence.
3. Tolerance při porovnávání polohy řezů mezi DWI a T2 TRA.
4. Přesné DICOM tagy pro získání údajů o hmotnosti, BMI a dalších statistikách.
5. Návrh dashboardu pro administrátorské rozhraní a reporting kvality.