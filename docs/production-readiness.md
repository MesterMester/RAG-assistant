# RAG-AI-Asszisztens - Eles Indulas Elotti Minimum

## P0 - Kotelezo az eles indulas elott

### 1. Kulon privat RAG-DB-re koltozes
Celt:
- a valodi adat teljesen kulon legyen a kodrepotol
- a fejlesztoi `dev-rag-db` csak teszt maradjon

Kell:
- uj privat `RAG-DB` mappa
- `.env` atallitasa `RAG_SOURCE_DIR`-re
- a szukseges belso almappak automatikus letrehozasa
- ellenorzes, hogy minden mar ott irodik

Kimenet:
- az eles adat mar nem a repo alatt van

### 2. Backup rendszer
Celt:
- semmi fontos adat ne vesszen el torles, hiba vagy rossz import miatt

Kell:
- teljes `RAG-DB` mentes
- datumozott snapshotok
- egyszeru visszaallitasi folyamat
- legalabb kezi backup gomb vagy script

Minimum mentendo:
- nyers fajlok
- `.rag_assistant/manual_records.json`
- indexek
- Chroma allomanyok
- import run logok
- proposal queue
- event log

Kimenet:
- van visszaallithato biztonsagi mentes

### 3. Event log / history
Celt:
- minden fontos valtozas visszakovetheto legyen

Kell naplozni:
- rekord letrehozas
- rekord modositas
- rekord torles / archivalas
- parent valtozas
- relation valtozas
- statuszvaltas
- datumvaltozas
- import dontesek
- kesobb exportok es chat muveletek is

Esemenymezoek:
- `event_id`
- `timestamp`
- `action_type`
- `record_id`
- `before`
- `after`
- `source`
- `session/run id`

Kimenet:
- minden erdemi valtozasnak van nyoma

### 4. Undo alap
Celt:
- a hibas muveletek visszavonhatok legyenek

Elso verzio:
- linearis undo az utolso muveletekre
- plusz rekordonkent history megtekintese

Kesobbi verzio:
- konkret rekord egy korabbi verziora visszaallitasa
- celzott esemeny-visszavonas

Kimenet:
- nem kell felni a szerkesztestol es importtol

### 5. Obsidian export alap
Celt:
- a RAG-bol kifele is legyen hasznalhato ut
- az Obsidian maradhasson napi shell

Kell:
- rekord export markdownba
- frontmatter
- task export checkbox-kompatibilisen
- alap hierarchy megorzese
- source meta jelolese

Teszt:
- dummy adatokkal a jelenlegi rendszerbol

Kimenet:
- a RAG tartalma mar kiviheto Obsidianba

## P1 - Erosen ajanlott kozvetlenul ezutan

### 6. Torlesbiztonsag
Kell:
- torles elotti megerosites
- optionalis soft delete / archive
- torlesi log

### 7. Adatkonzisztencia ellenorzo / normalizalo
Kell:
- hierarchia-ellenorzes
- duplikaciogyanus elemek jelzese
- reference/id hibak tisztitasa

### 8. Import run naplozas
Kell:
- mi futott
- min futott
- meddig jutott
- hol akadt el
- folytathato legyen

## P2 - Mar mehet kesobb is

### 9. Obsidian importer
### 10. Webes chat
### 11. Erosebb retrieval
### 12. Workflow analytics

## Javasolt megvalositasi sorrend

1. backup
2. event log
3. undo alap
4. obsidian export alap
5. privat RAG-DB-re koltozes
6. kis mennyisegu valodi adat proba
7. csak ezutan nagyobb feltoltes

## Dontes az undo-rol

Elso korben:
- linearis undo

De a history-t ugy kell epiteni, hogy kesobb lehessen:
- rekordonkent verziot visszaallitani
- akar egy konkret esemenyt is visszavonni

Tehat:
- most egyszeru undo
- kesobb finomabb, celzott restore
