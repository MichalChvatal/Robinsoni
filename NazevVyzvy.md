# 1 Název výzvy
TriaLink – inteligentní průvodce pro
rychlejší zapojení onkologických pacientů
do klinických studií léčiv.


# 2 Popis výzvy 
Postavte TriaLink: první český průvodce
klinickými studiemi, který onkologům
zkrátí hledání inovativní léčby pro jejich
pacienty z hodin na sekundy a současně
umožní pacientům podílet se na vyhledání
vhodné terapii osobně. Výsledkem bude
webová aplikace pro efektivní párování
onkologických pacientů s běžícími
klinickými studiemi léčiv v MOÚ. Nástroj
nabídne dvě samostatná rozhraní: pro
lékaře (rozhodovací strom k rychlému
výběru studie a přímé kontaktování
studijního týmu) i pro pacienty
(interaktivní průvodce s nápovědou,
případně využití lokálního LLM pro extrakci
parametrů z anonymizované zprávy).
Řešení odstraní administrativní zátěž
manuálního vyhledávání, usnadní přístup
pacientů k inovativní léčbě a vytvoří
validovaný základ pro budoucí rozšíření do
dalších onkologických center v ČR.

# 3 Současný stav/ řešení a jeho
nedostatky

Současný nemocniční informační systém
(NIS) nedokáže ve fázi klinické rozvahy
automaticky vyhodnocovat parametrická
data pacientů a nabízet lékařům
odpovídající klinické studie, přestože
obsahuje databázi aktivních studií s
náborem a základními parametry v MOÚ.
Onkologický tým je tak nucen spoléhat na
manuální, časově i administrativně vysoce
zatěžující náhradní postupy pro předvýběr
a referování pacientů.
Zároveň zcela chybí uživatelsky přívětivý
digitální nástroj v češtině, který by
pacientům umožnil orientaci v možnostech

# 4 Pozadavky na reseni

1. Architektura a obecné požadavky
• Webová aplikace: Plně
samostatné rozhraní bez nutnosti
přímého napojení na NIS,
optimalizované pro PC i mobilní
zařízení.
• Dvoustupňový přístup: Dvě
oddělená, uživatelská rozhraní –
přizpůsobená pro Lékaře /
Odborného pracovníka a pro
Pacienta.
2. Funkcionální požadavky pro roli Lékař
• Klinický rozhodovací strom:
Filtrování studií na základě
strukturovaného stromu (MKN-10
diagnóza, stádium, linie předchozí
terapie, biomarkery).
• Variant-level matching: Přesné
sémantické párování na úrovni
konkrétních genových variant a
biomarkerů (např. KRAS G12C,
stav HER2-low, TMB).
• Přímé kontaktování (Referral):
Generování strukturované referral
zprávy s přímým odesláním na
odpovědného hlavního
zkoušejícího (PI) a studijní
koordinátorku (SC) v MOÚ.
• Časová efektivita: Nalezení
vhodné studie a iniciace
kontaktování do 30 sekund.
3. Funkcionální požadavky pro roli
Pacient
• Interaktivní průvodce: Uživatelsky
přívětivé vyhledávání v češtině
doplněné o srozumitelnou
nápovědu (pop-up tooltips)
překládající medicínskou
terminologii do laického jazyka.
• Pokročilá AI funkce: Možnost
nahrát anonymizovanou
lékařskou/ambulantní zprávu s
využitím lokálního LLM pro
automatickou extrakci klíčových
parametrů do vyhledávacího
formuláře.
4. Bezpečnost a ochrana dat
• Aplikace v databázi nesmí ukládat
žádné osobní identifikační údaje
ani citlivá pacientská data.
• Zpracování nahrávaných textů
zpráv pro LLM probíhá výhradně v
operační paměti a data jsou po
extrakci okamžitě smazána.

# 5 Uspech v 48 hodinach

Funkční webový prototyp s
otestovaným importem lokálních
dat studií MOÚ (export ze SONIS).
• Ověřený průchod rozhodovacím
stromem pro lékaře i pacienta na
testovacím setu 5 kazuistik.
• Funkční generování a simulace
odeslání referral e-mailu na
zodpovědnému lékaři a studijnímu
koordinátorovi.
• Demonstrace funkční extrakce
parametrů z anonymizované
zprávy pomocí lokálního LLM. 
# 6 Vstupy/data 
1. Export běžících studií z NIS:
Formát: Soubor .csv – aktuální
export studií s otevřeným náborem
v MOÚ.
Specifikace: Obsahuje
strukturované parametry
otevřených studií. Sloupec pro
přímý telefonní kontakt/klapku na
studijní koordinátory (SC) je v
exportu prázdný a bude
nahrazen/simulován doplňkovým
adresářem.
2. Číselník biomarkerů:
Formát: Soubor .xlsx + náhled
formulářového rozhraní
Specifikace: Standardizovaný
číselník onkologických biomarkerů
a genomických alterací pro
rozhodovací strom a formuláře.
3. Zjednodušená TNM klasifikace:
Formát: Dvojice souborů
cis_stadium.csv a cis_skupina.csv.
4. Klasifikace MKN-10 (Registr ÚZIS):
Formát: Odkaz na oficiální
publikaci ÚZIS (MKN-10 ÚZIS).
5. Testovací set pacientských kazuistik:
Formát:
Anonymizované/syntetické profily
a textové nálezy minimálně 10
pacientů.
Využití: Validace přesnosti
rozhodovacího stromu a testování
pokročilé funkce extrakce
parametrů pomocí lokálního LLM

# 7 Potenciál dopadu:

1. Klinický dopad na pacienta
• Zrychlení přístupu k inovativní
terapii: Zkrácení cesty pacienta k
precizní onkologické léčbě a
novým molekulám v rámci KHLP z
týdnů na dny.
• Zvýšení počtu pacientů
zařazených do klinických studií:
Cílené zvýšení podílu dospělých
onkologických pacientů
zařazených do klinických studií v
ČR ze současných < 5 % na úroveň
předních evropských center (10–15
%).
• Aktivizace pacienta: Srozumitelné
rozhraní s nápovědou a možností
extrakce dat z nálezu zapojí
pacienta přímo do rozhodování o
možnostech jeho léčby.
2. Dopad na zdravotnický tým a MOÚ
Úspora času lékařů a studijních
koordinátorů: Radikální snížení
administrativní zátěže – zkrácení doby
předvýběru a vyhledání studie ze 45
minut na < 30 sekund na pacienta.
Zvýšení náboru na centru:
Maximizace naplnění náborových kvót
běžících studií v MOÚ, efektivnější
využití kapacit Oddělení klinických
studií.
Přímá finanční úspora: Náklady na
diagnostiku, experimentální i
standartní léčbu a provozní výdaje v
rámci studie hradí sponzor, což přímo
šetří rozpočet nemocnice i prostředky
veřejného zdravotního pojištění.
3. Celospolečenský a celonárodní přesah
(II. etapa)
Snížení geografických nerovností:
Snadná dostupnost informací o
studiích pro regionální onkology
vyrovná rozdíly v přístupu k moderní
péči mezi kraji.
Škálovatelnost na národní úroveň:
Validované PoC řešení z I. etapy
poslouží jako standardizovaný model
pro rozšíření do sítě všech
Komplexních onkologických center
(KOC) v ČR.