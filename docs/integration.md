# Lokální workflow DEMO — etapa 7

Příjem, analýza a GUI sdílejí lokální úložiště z téhož konfiguračního souboru.
Každá komponenta má vlastní proces. Zavření okna ponechá příjem a analýzu v běhu.

## Spuštění ve Windows

V kořeni projektu použijte **Spustit aplikaci.cmd**. Skript podle potřeby vytvoří
prostředí `.venv` a doinstaluje závislosti. Vyžaduje nainstalovaný Python 3.12.
Pokud jsou závislosti již dostupné, spustí se bez internetu. Pro první instalaci
bez internetu vložte odpovídající instalační balíčky do adresáře `wheels`.

Příjem a analýza běží na pozadí a při dalším spuštění se použijí už spuštěné
procesy. **Zastavit prijem.cmd** ukončí procesy spuštěné těmito skripty.

## Odeslání DICOM

1. Otevřete **Odeslat DICOM.cmd**. Skript podle potřeby spustí příjem a analýzu.
2. Vyberte DICOM soubory, složku nebo ZIP. Fungují i DICOM soubory bez přípony.
   Pro první zkoušku lze vybrat menší ukázkový ZIP z adresáře `data`.
3. Zkontrolujte zobrazený cíl a potvrďte přenos. Po dokončení se zobrazí počty
   potvrzených, přeskočených a chybných položek. Potvrzené položky zahrnují
   duplikáty, které přijímač přijal bez vytvoření nové instance.

Odesílač používá cíl z `config/app_config.yaml`. Výchozí cíl je tento počítač,
`127.0.0.1:11112`, s přijímačem `MR_QUALITY` a odesílatelem `MR_TEST`.
Přenáší podporované MR Image Storage a Secondary Capture objekty přes C-STORE.
Soubory DICOMDIR, ostatní nedicomové soubory a nepodporované typy či přenosové
syntaxe přeskočí. Pokud je přijímač nastavený jen pro IP adresu MR přístroje,
pro místní odesílač použijte výchozí testovací konfiguraci.

## Ruční spuštění

V kořeni repozitáře nainstalujte závislosti včetně podpory GUI. Pro spuštění bez
internetu připravte odpovídající balíčky předem.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-stage6.lock.txt
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
```

Z kořene repozitáře spusťte každý příkaz v samostatném terminálu.

```powershell
.\.venv\Scripts\python.exe -m mr_quality_monitor.cli --config config/app_config.yaml
.\.venv\Scripts\python.exe -m mr_quality_monitor.worker_cli --config config/app_config.yaml --protocol config/protocols/prostate_mou.yaml
.\.venv\Scripts\python.exe -m mr_quality_monitor.gui --config config/app_config.yaml
```

Výchozí konfigurace přijímá lokální C-STORE na `127.0.0.1:11112` s cílovým AE
`MR_QUALITY` a odesílatelem `MR_TEST`. Pro kontrolovanou zkoušku s přístrojem použijte
jeho schválené síťové údaje podle [návodu k přijímači](receiver.md).

## Chování analýzy

- Po potvrzeném příjmu MR objektu zůstane jeho úloha ve frontě SQLite. Analýza
  zjišťuje práci přibližně každé dvě sekundy a pro každé dotčené vyšetření sestaví
  akvizice z dostupných MR objektů. Ověřuje soubory kontrolním součtem a čte hlavičky.
- První analýza uloží verzi protokolu k vyšetření. Jiná volba protokolu při restartu
  se použije pro nově zakládaná vyšetření.
- Historie a potvrzení úloh se ukládají atomicky. Po přerušení proces pokračuje
  z fronty. Duplicitní přenos netvoří novou úlohu. Pozdější snímky spustí nové
  vyhodnocení a GUI mezi příjmem a dokončením označí předchozí výsledek jako zastaralý.
- Secondary Capture se ukládá a počítá mezi přijaté objekty. Do MR akvizic
  se nezařazuje a neblokuje kontrolu metadat.
- Protokol s explicitním potvrzením úplnosti nechá akvizici čekat na důkaz.
  Ukončení přenosu ani prodleva mezi snímky takový důkaz neposkytují. Analýza pixelů
  a klinické hodnocení PI-QUAL zůstávají mimo režim DEMO.
- Jednotlivá akvizice může splnit DEMO pravidla ještě během příjmu. Celkový stav
  vyšetření zůstává bez explicitního uzavření čekající, pokud už není kritický.

## Provoz a ověření

Chyby čtení nebo integrity objektu se zobrazí v GUI a zaznamenají do databáze.
Provozní zprávy analýzy jsou v `runtime/logs/analysis.log` při výchozí konfiguraci.
Dočasné chyby zpracování se opakují v dalším průchodu. Po obnově původního souboru
lze při příštím spuštění přidat `--retry-failed`. Pokud přijímač dříve označil
soubor jako poškozený, nejprve restartujte přijímač, aby ověřil obnovené úložiště.
Parametr `--once` zpracuje jeden
průchod frontou a vrátí nenulový kód, pokud v něm zpracování selže.

Syntetická integrační zkouška používá DICOM C-STORE na localhostu. Ověřuje cestu
od přijetí přes automatickou analýzu až po stav čtený GUI, včetně restartu,
pozdějších snímků a duplicit. Spusťte ji z kořene repozitáře.

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_stage7_integration.py
```

Menší dodaný archiv lze zpracovat vlastním lokálním testem. Výstup uvádí počty
objektů a úloh. Test po skončení odstraní své dočasné úložiště.

```powershell
.\.venv\Scripts\python.exe tools/smoke_dataset.py data/DICOMDIR_MR50000_ukazka_jednoho_mereni.zip --protocol config/protocols/demo_mr.yaml
```
