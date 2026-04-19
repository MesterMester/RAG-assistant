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
