# Robinsoni

Výchozí spouštění používá pro rozpoznaná vyšetření prostaty protokol
**PROSTATE_PIRADS_V21** ze souboru `config/protocols/prostate_pirads_v2_1.yaml`.

Aplikace pracuje vždy s jedním vyšetřením. První platný MR snímek nového
vyšetření nebo jednoznačně rozpoznaného jiného protokolu zahájí nové
měření. Monitor načte nový plán a odstraní předchozí snímky i výsledky
z provozního úložiště.

Lokální MR Quality Monitor — postupná implementace podle `prompt.md`.

## Rychlé spuštění ve Windows

1. Dvakrát klikni na **Spustit aplikaci.cmd**. Skript podle potřeby připraví Python prostředí,
   zahájí novou relaci příjmu a analýzy a otevře prázdný monitor.
2. Pro přenos otevři **Odeslat DICOM.cmd**, vyber soubory, složku nebo ZIP a potvrď přenos.
3. Příjem a analýzu ukončíš přes **Zastavit prijem.cmd**. Samotné zavření monitoru je nezastaví.

Při prvním spuštění potřebuješ Python 3.12. Pokud chybějí závislosti, skript je
stáhne nebo použije připravené instalační balíčky v adresáři `wheels`.
[Podrobnosti ke spuštění a přenosu](docs/integration.md#spuštění-ve-windows)

- [Návrh architektury](docs/architecture.md)
- [Průběžný log implementace](docs/implementation_log.md)
- [Instalace a spuštění receiveru](docs/receiver.md)
- [Metadata a normalizace](docs/metadata.md)
- [DEMO protokoly a validační engine](docs/validation.md)
- [Databáze, migrace a historie](docs/database.md)
- [Spuštění GUI a syntetické ukázky](docs/gui.md)
- [Integrované lokální spuštění](docs/integration.md)
- [Popis dodaných testovacích dat](data/README.md)

DICOM příjem, automatická analýza a GUI běží jako samostatné procesy.
`PROSTATE_MOU` hodnotí metadata a u určených sekvencí spouští nad pixely
podporovaných MR snímků kontroly SNR, ghostingu a návaznosti řezů. Aktivní ID,
verzi a režim protokolu najdete v `runtime/logs/analysis.log`. Po dokončení
příjmu lze vyšetření ručně uzavřít v jeho podrobnostech. Pokrytí prostaty
a PI-QUAL zatím vyžadují samostatné posouzení.
Prahy detektorů obrazové kvality jsou odděleně v `config/image_quality.yaml`;
umístění souboru lze změnit přes `image_quality_path` v `config/app_config.yaml`.
