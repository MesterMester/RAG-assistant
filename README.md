# RAG-asszisztens

Lokalis projekt a szemelyes ugyek, projektek es dokumentumok kezelesere epulo RAG rendszerhez.

## Alapelv

A `RAG-asszisztens` repo csak a kodot es a lokalis eszkozoket tartalmazza.
A tenyleges szemelyes tudasbazis, vagyis a `RAG-DB`, kulso, privat konyvtar marad.
A privat forrasadatokat nem taroljuk ebben a Git repoban.
A generalt index sem a repoban van, hanem a `RAG-DB` alatt jon letre.

## Mit tud most

Ez a repo mar tartalmaz egy kezdeti RAG-alapot es az elso manualis ugy-/tudasbevitel UI-t:

- dokumentumok beolvasasa egy megadott forraskonyvtarbol
- kezileg felvitt rekordok tarolasa a privat `RAG-DB` alatt
- egyszeru chunkolas szoveges fajlokra es manualis rekordokra
- lokalis keyword index
- Chroma upsert manualis rekordokra
- Streamlit UI az elso nezetekkel: tablazat, kanban, timeline, mindmap

## Tarolas

A forras es a kapcsolodo indexek is a privat `RAG-DB` oldalhoz kotodnek:

- forras: `RAG-DB/`
- manual rekordok: `RAG-DB/.rag_assistant/manual_records.json`
- keyword index: `RAG-DB/.rag_assistant/index.json`
- Chroma tar: `RAG-DB/.rag_assistant/chroma/`

## Fejlesztoi Mod

Fejlesztes kozben hasznalhatsz egy ideiglenes, repo alatti mintatarat:

- `RAG-asszisztens/dev-rag-db/`

Ez csak fejlesztoi kiserleti tar. A Git ignore alatt van, ezert nem szinkronizalodik.
Ha eljutunk egy stabil allapotig, a teljes mappa atmasolhato egy kulso, privat helyre, es onnantol a `.env`-ben megadott vegleges `RAG_SOURCE_DIR` alatt fog mukodni minden.

## Beallitas

1. Hozz letre virtualis kornyezetet es telepitsd a projektet:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```

2. Hozz letre egy sajat `.env` fajlt az `.env.example` alapjan.

3. A privat `RAG-DB` utvonalat csak a helyi `.env` fajlban add meg:

   ```dotenv
   RAG_SOURCE_DIR=/teljes/utvonal/a/RAG-DB-hez
   OLLAMA_EMBED_MODEL=nomic-embed-text
   ```

## CLI

Biztonsagos forras-ellenorzes:

```bash
rag inspect
```

Keyword index ujraepitese a fajlokbol es manual rekordokbol:

```bash
rag ingest
```

Manual rekordok Chroma upsertje:

```bash
rag reindex-manual
```

Kereses a keyword indexben:

```bash
rag search "projekt hatarido"
```

## UI

A kezdeti kezi bevitelhez es nezetekhez:

```bash
rag-ui
```

A Streamlit felulet jelenleg ezeket adja:

- manualis rekord bevitel
- tablazat nezet
- kanban nezet statusz szerint
- timeline nezet datum szerint
- mindmap nezet kapcsolatokkal
- egyszeru keresesi nezet

## Kovetkezo Lepes

A belso adatmodell es a fejlesztoi RAG tar szerkezete a `docs/data-model.md` fajlban van leirva.
