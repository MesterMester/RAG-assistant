# Thunderbird Import Phase 2

## Cél

Az első cél nem a teljes email-szöveg RAG-ba tolása, hanem egy használható email-preview és döntési felület:

- a Thunderbird mappák szűrhető beolvasása
- soronkénti email-lista
- metaadat alapú triage
- későbbi csoportos import a RAG-be

## Kiinduló állapot

Már működik:

- `THUNDERBIRD_IMPORT_MD` alapú mailbox root config
- `THUNDERBIRD_FOLDERS_MD` alapú include / exclude folder szabály
- mailbox inventory
- preview alap
- explicit `Upsert` gomb

## Phase 2 MVP

### 1. Folder-driven preview

A Thunderbird tabon:

- a szűrt mailboxok jelenjenek meg
- lehessen kiválasztani, melyik mailbox(ok)ból készüljön preview
- lehessen szűrni:
  - időszakra
  - darabszámra
  - Thunderbird tagekre
  - feladóra
  - címzettre
  - tárgy-kifejezésre

### 2. Email preview table

Egy email = egy sor.

Javasolt oszlopok:

- `selected`
- `mailbox`
- `account`
- `date`
- `from`
- `to`
- `cc`
- `subject`
- `thunderbird_tags`
- `has_attachment`
- `message_id`
- `importance_level`
- `suggested_scope`
- `suggested_action`
- `preview`

### 3. Fontossági skála

Minden email kapjon 1..5 figyelmi szintet:

- `1 = no importa`
- `2 = good to know`
- `3 = todo`
- `4 = urgent`
- `5 = danger zone`

Ez lehet:

- először kézi
- később AI-javasolt

## Mi menjen a RAG-be

### Ne minden email külön `source_item` legyen

Első javasolt modell:

- az egyes emailek preview-szintű rekordok maradnak a Thunderbird pipeline-ban
- a RAG-be csoportosított email-node-ok kerülnek

Példák csoportos node-ra:

- `Emailek / Sárga pöttyös / Közösségi / 2026-04`
- `Emailek / XY projekt / 2026-15. hét`
- `Emailek / adott ügy / nyitott threadek`

### A csoportos node tartalma

Egy node-on belül listaformában:

- dátum
- feladó
- tárgy
- Thunderbird tagek
- importance
- message_id
- Thunderbird jump link / keresőkulcs
- rövid preview

### 1-es és 2-es szint

Igen, ezek metaadatai is kerüljenek be a RAG-be.

Indok:

- kontextusnak hasznosak
- későbbi hasonló levelek besorolásánál mintát adnak
- “mi minden történt körülöttem” jellegű háttértudást adnak

Különbség:

- `1-2`: lightweight kontextus
- `3-5`: operatív figyelem / task / esetleges task vagy event generálás

## Message-ID és Thunderbird ugrás

MVP szinten biztosan tároljuk:

- `message_id`
- `mailbox_path`
- `account`

Ez már önmagában jó keresőkulcs.

Későbbi cél:

- kattintható “Open in Thunderbird” akció

Legstabilabb út:

- egy helyi helper / launcher script
- ami Message-ID alapján Thunderbird keresést indít

Tehát Phase 2-ben:

- még nem kell kész közvetlen deep link
- de a `message_id` mindenképp legyen bent

## Thunderbird tagek

Kötelezően olvasandó mező.

Felhasználás:

- szűrés
- importance-javaslat
- scope-javaslat
- későbbi routing

## Javasolt import pipeline

### Step A

Mailbox inventory a `TH_folders.md` alapján.

### Step B

Sor-alapú email-preview tábla metaadatokkal.

### Step C

Kézi vagy AI-javasolt mezők:

- `importance_level`
- `scope`
- `route_to`

### Step D

Batch import a RAG-be:

- nem külön email/node szinten
- hanem scope szerinti csoportos node-okba

### Step E

Később opcionális:

- teljes body import csak kijelölt emaileknél
- thread grouping
- task/event/decision javaslat

## Következő konkrét fejlesztési feladatok

1. Thunderbird tagek kinyerése a preview táblába.
2. Mailbox kiválasztás a preview előtt.
3. Időszak / limit / tag szűrők a Thunderbird tabon.
4. Soronkénti `importance_level` mező.
5. `scope` mező:
   - organization
   - team
   - project
   - case
6. `message_id` biztos megjelenítése.
7. Később `Open in Thunderbird` helper akció.

## Konkrét végrehajtási sorrend

Az alábbi sorrend arra van optimalizálva, hogy minél hamarabb legyen használható triage-felület, és csak utána jöjjön az intelligensebb besorolás.

### Phase 2A - Mailbox inventory -> olvasható preview

Ez az első valóban használható mérföldkő.

Mit csinálunk:

1. A Thunderbird tabon a mailbox inventory jelenjen meg kiválasztható listaként.
2. A `TH_folders.md` alapján csak az `Included Paths`-ban szereplő, és nem kizárt mailboxok jelenjenek meg.
3. A preview ne az összes mailboxból fusson, hanem csak a kijelölt mailboxokból.
4. Legyen állítható:
   - `since_days`
   - `max_messages_per_mailbox`
   - opcionálisan egy teljes preview limit is

Technikai feladatok:

- `thunderbird_importer.py`
  - mailbox kiválasztás támogatása
  - preview futtatása csak a kiválasztott mailboxokra
- `streamlit_app.py`
  - inventory táblázat checkboxokkal
  - `Preview frissítése` gomb a kiválasztott mailboxokra

Elfogadási feltétel:

- a 3 fiók releváns mailboxai külön kijelölhetők
- a preview csak abból a körből készül
- a lista gyorsan, áttekinthetően használható

### Phase 2B - Email metaadatok kibővítése

Ez kell ahhoz, hogy valódi triage legyen.

Beolvasandó mezők:

- `message_id`
- `subject`
- `from`
- `to`
- `cc`
- `date`
- `thunderbird_tags`
- `mailbox_path`
- `account`
- `in_reply_to`
- `references`
- `has_attachment`
- `body_preview`

Fontos megjegyzés:

- a teljes email body még ekkor sem cél
- csak rövid preview kell

Technikai feladatok:

- `thunderbird_importer.py`
  - preview modell bővítése
  - header-ekből metaadatok kinyerése
  - Thunderbird tagek olvasásának bekötése
- `streamlit_app.py`
  - preview tábla oszlopainak bővítése

Elfogadási feltétel:

- minden preview sorból látszik, ki írta, kinek, mikor, melyik mailboxból, milyen tagekkel

### Phase 2C - Szűrhető triage tábla

Ettől válik napi használatra alkalmas felületté.

Szűrők:

- mailbox
- account
- date range
- tags
- from
- to / cc
- subject text
- attachment yes/no

Később jöhet:

- `reply state`
- `waiting_on_me`
- `waiting_on_them`

Technikai feladatok:

- `streamlit_app.py`
  - Thunderbird szűrőpanel
  - táblázat újraszűrése a preview adatokon

Elfogadási feltétel:

- a felhasználó gyorsan leszűkítheti a napi fontos levelek körét

### Phase 2D - Triage mezők

Ettől lesz döntési felület, nem csak olvasó.

Soronkénti mezők:

- `importance_level` (`1..5`)
- `suggested_scope_type`
- `suggested_scope_value`
- `suggested_action`
- `notes`

Javasolt `suggested_action` értékek:

- `context_only`
- `review`
- `todo`
- `urgent_followup`
- `decision_needed`

Technikai feladatok:

- preview sorokhoz lokális UI-state
- később menthető triage-state

Elfogadási feltétel:

- minden emailhez megadható, mennyire fontos és hova tartozik

### Phase 2E - Ember és scope felismerés

Ez az a rész, ami már elkezdi összekötni az emailt a rendszer többi adatával.

Feladat:

- email címek összekötése `person` rekordokkal
- `person` rekordokból scope-javaslat:
  - organization
  - team
  - project
  - case
- subject és mailbox alapján plusz javaslatok

MVP szint:

- egyszerű heurisztika
- nem AI, csak szabály + meglévő rekord-egyezés

Később:

- embedding / fuzzy matching
- AI-assisted routing

Elfogadási feltétel:

- a rendszer tudjon legalább kezdeti javaslatot tenni arra, hogy egy levél melyik ügyhöz / projekthez / teamhez tartozik

### Phase 2F - "Ki vár kire?" logika

Ez a legfontosabb operatív intelligencia.

Szükséges bemenet:

- saját email-címek listája
- `from`, `to`, `cc`
- `date`
- `message_id`
- `in_reply_to`
- `references`

Első szabályok:

- ha az utolsó releváns levél tőlem ment és nincs rá válasz: `waiting_on_them`
- ha az utolsó releváns levél bejött és nincs rá outbound válasz: `waiting_on_me`
- ha nagyon friss inbound és magas importance: feljebb kell sorolni

Kimeneti mezők:

- `reply_state`
- `attention_reason`

Elfogadási feltétel:

- a preview listában látszódjon, hol várnak a válaszomra, és hol várok én

### Phase 2G - Batch import a RAG-ba

Csak akkor jöjjön, ha a preview és a triage már jó.

Import modell:

- nem külön emailenként `source_item`
- hanem scope szerinti csoportos email-node

Példák:

- `Emailek / SP / KT / 2026-19. hét`
- `Emailek / XY ügy / nyitott válaszok`

A csoportos node tartalma:

- soronként az emailek metaadatai
- importance
- tags
- `message_id`
- mailbox/account
- rövid preview

Az `1` és `2` szintű emailek:

- igen, menjenek be
- de lightweight kontextusként

Az `3-5` szintű emailek:

- operatív figyelmet kapnak
- később task/event/decision javaslat is épülhet rájuk

## Mi a legjobb legközelebbi sprint?

Ha egyetlen rövid sprintre kell lebontani, akkor ez a legjobb:

1. Phase 2A
   Mailbox-választás a preview előtt.
2. Phase 2B
   Metaadatok bővítése: `cc`, `tags`, `message_id`, `in_reply_to`, `references`, `has_attachment`.
3. Phase 2C
   Szűrőpanel a Thunderbird tabon.
4. Phase 2D
   Soronkénti `importance 1..5`.

Ez már adna egy napi használható Thunderbird triage felületet.

## Mi nem cél még most

- teljes body-import minden emailhez
- teljes thread-rekonstrukció minden edge case-re
- közvetlen Thunderbird deep link kész megoldás
- AI-alapú automatikus routing első körben

Ezek jöhetnek később, de nem kellenek az első valóban hasznos MVP-hez.
