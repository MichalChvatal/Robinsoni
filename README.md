# Robinsoni

Lokální MR Quality Monitor — postupná implementace podle `prompt.md`.

## Rychlé spuštění ve Windows

1. Dvakrát klikni na **Spustit aplikaci.cmd**. Skript podle potřeby připraví Python prostředí,
   spustí příjem a analýzu na pozadí a otevře monitor.
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

V lokálním režimu DEMO běží DICOM příjem, automatická analýza metadat a jednookenní
GUI jako samostatné procesy. Klinické hodnocení PI-QUAL a analýza pixelů zatím
nejsou k dispozici.
