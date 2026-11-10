# Vinted image embeddings: ricerca e roadmap MVP

Aggiornato: 29 ottobre 2026

## Obiettivo

Usare le immagini già raccolte per trovare prodotti visivamente simili e capire se
alcuni motivi, forme o stili sono associati a vendite più rapide. Il primo risultato
deve essere un’analisi consultabile, non una decisione automatica di acquisto.

## Ricerca: come funzionano gli embedding

Un encoder visivo pre-addestrato trasforma un’immagine in un vettore numerico, detto
embedding. Immagini vicine nello spazio dei vettori tendono ad avere caratteristiche
visive simili. Per confrontarle si può usare la similarità coseno, cioè il prodotto
scalare tra vettori normalizzati.

CLIP apprende uno spazio condiviso per immagini e testo tramite supervisione
immagine-testo; consente quindi sia confronti immagine-immagine sia ricerche come
“charm a forma di cuore”. DINOv2 impara rappresentazioni visive senza etichette di
testo ed è progettato come feature extractor riutilizzabile per classificazione e
ricerca per vicini. Entrambi sono pre-addestrati: per generare i primi vettori non
serve allenarli sui nostri dati né avere una GPU.

Non c’è una garanzia che uno dei due capisca meglio i dettagli dei gioielli o predica
le vendite. Le foto Vinted includono anche sfondo, inquadratura, luce e confezione;
questi fattori possono influenzare la somiglianza quanto il prodotto stesso. La scelta
va fatta con un piccolo confronto manuale sui nostri esempi.

## Dati disponibili nel progetto

Verifica locale del database e dell’archivio immagini al 29 ottobre 2026:

| Stato nel tracking | Annunci | Con immagine archiviata |
| --- | ---: | ---: |
| Venduti | 1.411 | 972 |
| Attivi | 904 | 709 |
| Non disponibili | 174 | 121 |

La cartella `data/deal_archive` contiene circa 4.858 file e occupa circa 6,4 GB.
Questi valori descrivono lo snapshot locale e vanno ricalcolati all’avvio del batch.

Fra i venduti con immagine archiviata, la durata osservata nel DB (`sold_at` meno
`first_seen_at`) è distribuita così:

| Tempo osservato dalla prima rilevazione alla vendita | Venduti con immagine archiviata |
| --- | ---: |
| 0–6 ore | 85 |
| 6–12 ore | 143 |
| 12–24 ore | 199 |
| Oltre 24 ore | 545 |

Questa durata è il tempo osservato dal sistema, non necessariamente il tempo reale
dalla pubblicazione. Quando esiste un’età di pubblicazione affidabile va conservata
come misura distinta, senza mescolare le due definizioni.

## Roadmap

### 1. Preparare una coorte affidabile

- Usare gli `archive_image_path` esistenti; non riscaricare le foto dagli URL se il file
  locale è valido.
- Collegare ogni immagine a `item_id`, URL annuncio, stato, prezzo, categoria, like,
  venditore e date osservate.
- Verificare file mancanti/corrotti e calcolare SHA-256 per individuare duplicati.
- Non trattare gli annunci ancora attivi come “mai venduti”: sono osservazioni censurate.
  Per un confronto entro 24 ore, usare solo annunci osservati per almeno 24 ore.
- Escludere `unavailable` dall’etichetta venduto/non-venduto: significa che la pagina
  non è verificabile, non che la vendita sia confermata.

### 2. Fare un benchmark visivo piccolo

Prendere circa 100–200 immagini rappresentative e confrontare due baseline:

- CLIP ViT-B/32: embedding immagine e testo nello stesso spazio; utile anche per
  interrogazioni testuali e categorie formulate in linguaggio naturale.
- DINOv2 ViT-B/14: embedding visivo, da valutare per similarità di forma e dettagli
  senza dipendere da descrizioni testuali.

Per 30–50 immagini, controllare manualmente i 5–10 vicini più simili. Annotare se la
somiglianza riguarda davvero il prodotto oppure soltanto sfondo, colore o confezione.
Se il confronto favorisce sistematicamente lo sfondo, valutare in una fase successiva
un ritaglio del prodotto; non introdurre subito segmentazione automatica.

### 3. Calcolare e salvare gli embedding in batch

È implementato il primo batch isolato in `scraper_app/vinted_image_embeddings.py`:

- apre `data/scraper.db` in sola lettura e usa solo immagini archiviate;
- scrive nel distinto `data/vinted_image_embeddings.db`, senza migrare o modificare
  il DB dello scraper;
- deduplica gli asset con SHA-256 ma conserva le osservazioni e i cambi di stato degli
  annunci;
- tiene separati asset, percorsi dell’asset, osservazioni, vettori, tentativi e run;
- è idempotente e riprendibile, crea un backup SQLite online del DB embeddings prima
  delle run successive e registra gli errori per singola immagine;
- supporta `--index-only` (solo hash/metadati, senza librerie ML) e `--limit N`.

Smoke test non distruttivo, limitato alle prime 10 immagini uniche:

```bash
cd projects/main-scraper
.venv/bin/python -m scraper_app.vinted_image_embeddings --index-only --limit 10 \
  --store-db /tmp/vinted-embeddings-smoke/embeddings.db \
  --backup-dir /tmp/vinted-embeddings-smoke/backups
```

Per calcolare davvero gli embedding CLIP nella virtualenv dello scraper, installare
le dipendenze opzionali (PyTorch può occupare spazio e Hugging Face scarica i pesi
al primo avvio):

```bash
cd projects/main-scraper
.venv/bin/python -m pip install -r requirements-vinted-embeddings.txt
.venv/bin/python -m scraper_app.vinted_image_embeddings --limit 10
```

Solo dopo aver verificato questo campione, eseguire senza `--limit` per l’intero
archivio. Per poter recuperare i dati dopo un guasto del Mac, indicare con
`--backup-dir` un volume esterno/sincronizzato: il backup locale predefinito protegge
da errori logici, ma non dalla perdita del disco. Le immagini originali non vengono
mai eliminate né alterate.

Dopo aver creato i vettori, generare il report HTML interattivo (selezione per titolo
o ID, similarità coseno e filtri per tempo di vendita):

```bash
.venv/bin/python -m scraper_app.vinted_image_embedding_report \
  --store-db data/vinted_image_embeddings.db
```

Il report viene scritto in `output/vinted_image_embeddings/index.html`, con miniature
locali nella cartella `thumbnails` e preview più nitide nella cartella `previews`
(fino a 1200 px, usate nel gioco di validazione). Per una diversa versione del
modello si possono passare `--model` e `--revision`.

Il report include anche il mini-gioco di validazione: una coppia per schermata,
pulsanti **Simili / Diversi / Non so**, scorciatoie `A`/`D`/`S`, avanzamento e annulla.
Il campione è di 25, 50 o 100 coppie. Le risposte e la posizione corrente si salvano
nel local storage del browser; **Esporta valutazioni JSON** crea una copia trasferibile
e il controllo di import la ripristina. Esporta un backup al termine delle sessioni:
le valutazioni non sono ancora scritte nel DB SQLite.

Quando il monitoraggio conferma per la prima volta che un affare è venduto, salva
anche la variante immagine più grande presente nel DOM dell'annuncio (preferendo
`srcset`), in `data/deal_archive/sold_dom/`. Il percorso e l'URL vengono conservati
nel payload dello storico; le immagini precedenti non vengono sovrascritte. Il
prossimo batch embeddings indicizza il file DOM archiviato come nuova risorsa, mentre
la vecchia immagine e il suo embedding rimangono disponibili per confronti e audit.
Per includerla nei confronti CLIP basta rilanciare il batch già esistente: è
incrementale e non ricalcola gli asset già presenti.

Stato del primo popolamento completo (29 ottobre 2026): 1.668 immagini uniche,
vettori CLIP da 512 dimensioni, nessun errore e `integrity_check=ok`. La revisione
`3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268` è quella fissata e usata dal report; la
prima elaborazione, inizialmente registrata come `main`, è stata conservata come
versione separata. Il database è circa 27 MB; il report rigenerato con preview ad alta
risoluzione occupa circa 67 MB insieme a miniature e preview locali.
Sono stati creati backup SQLite online locali prima e dopo la seconda run. Per
proteggere i dati dal guasto/perdita del Mac occorre ancora configurare una destinazione
esterna o sincronizzata: nessun volume esterno è montato in questo ambiente.

Il batch seleziona i file locali, applica il preprocessing CLIP, calcola il vettore in
modalità inference, normalizza L2 e lo salva in BLOB float32 little-endian. La chiave
comprende hash, modello, revisione effettiva dei pesi e versione preprocessing: una
nuova versione aggiunge embedding senza sostituire silenziosamente i precedenti. Le
righe dello scraper restano inalterate.

### 4. Valutare due usi distinti

**Ricerca per similarità.** Dato un annuncio, mostrare i 10 vicini più simili con
stato e tempo osservato di vendita. Misurare la precisione manuale dei risultati:
quanti dei primi 10 sono davvero comparabili?

**Associazione con la velocità di vendita.** Raggruppare gli esempi per vicinanza
visiva e confrontare mediana e distribuzione del tempo di vendita. Confrontare un
baseline con prezzo, categoria, like e venditore contro lo stesso baseline arricchito
dagli embedding. Usare una divisione temporale train/test e separare duplicati o
annunci dello stesso prodotto per evitare leakage.

Con circa un migliaio di immagini vendute archiviate, il primo obiettivo è una prova
esplorativa e un benchmark, non un classificatore di produzione. Una regressione
logistica regolarizzata o un nearest-neighbor sono sufficienti come prima misura;
modelli più complessi e fine-tuning si considerano solo se il test temporale dimostra
un miglioramento stabile.

### 5. Report e criterio per proseguire

Il primo report dovrebbe mostrare:

- immagini di esempio e vicini visivi;
- numero di esempi per gruppo;
- mediana del tempo osservato alla vendita e percentuale venduta entro 6/12/24 ore;
- prezzo e like tipici, per distinguere l’effetto visuale da quello commerciale;
- avviso se il campione è troppo piccolo o se i dati sono censurati.

Si passa all’integrazione nella ricerca solo se il benchmark manuale produce vicini
utili e il test temporale aggiunge valore rispetto alle variabili già disponibili.
Anche allora il punteggio va presentato come segnale con campione e incertezza, non
come certezza che un prodotto venderà.

## Proposta tecnica iniziale

Un primo MVP locale può stare in uno script batch separato e in una nuova tabella
SQLite. Genera gli embedding per le immagini già archiviate, crea un report HTML
statico con i vicini e i tempi di vendita, e non cambia il comportamento del
Procacciatore o del monitoraggio Vinted. Il calcolo parte su CPU; si valuta una GPU
solo se tempi misurati o volume futuro lo giustificano.

## Scalabilità e conservazione: regole da subito

Il principio è: **non cancellare l’unica copia di immagini, osservazioni o embedding**.
Un embedding si può ricalcolare soltanto se è ancora disponibile la stessa immagine
originale e sono noti modello e preprocessing usati.

### Identità e provenienza

- Assegnare alle immagini una chiave basata su SHA-256 del contenuto, non solo sul
  nome del file o sull’URL remoto. Lo stesso file incontrato più volte va deduplicato,
  ma gli annunci che lo usano restano collegati all’asset.
- Conservare `item_id`, URL, path/URI dell’asset, hash, dimensioni, stato di vendita,
  data e fonte della durata, prezzo, like e venditore. Non sovrascrivere la storia
  quando un annuncio cambia stato.
- Gli embedding sono versionati per `model_name`, revisione/pesi e
  `preprocessing_version`. Nuovo modello significa nuova riga/versione, non
  sostituzione silenziosa del vettore precedente.
- Registrare per ogni batch ora di avvio/fine, conteggi, errori e checkpoint. Il job
  deve essere idempotente e riprendibile: se si interrompe, riparte dall’ultima
  immagine completata senza duplicare o azzerare i dati.

### Backup e restore

- Fare backup coerenti del DB con l’Online Backup API di SQLite, non affidarsi alla
  copia del solo file mentre è in uso.
- Fare una copia separata dell’archivio immagini e di un manifest con hash, item ID e
  percorso. Un backup del DB senza le immagini, o viceversa, non è sufficiente.
- Mantenere almeno una copia su un supporto o storage distinto dal Mac/progetto; la
  cartella `data/deal_archive` da sola non è una strategia di backup.
- Non eliminare immagini, snapshot, etichette o versioni di embedding nell’MVP.
  Se in futuro serve ridurre spazio, introdurre una retention esplicita e verificare
  prima che resti una copia recuperabile.
- Programmare restore di prova e verificare hash e conteggi dopo il ripristino. Un
  backup non è considerato affidabile finché non è stato ripristinato almeno una
  volta.

SQLite fornisce un Online Backup API che produce uno snapshot consistente mentre il
DB è in uso. Quando occorrono più processi/scrittori concorrenti o ricerca vettoriale
indicizzata, si può migrare il catalogo e gli embedding in PostgreSQL con pgvector:
supporta ricerca esatta e approssimata, metriche coseno e vettori a precisione ridotta.
La ricerca approssimata può scambiare recall con velocità, quindi va confrontata con
la ricerca esatta prima di abilitarla. Non è necessario introdurre un vector database
separato nell’MVP.

### Soglie e percorso di crescita

| Fase | Dove stanno i dati | Ricerca | Quando passare alla fase successiva |
| --- | --- | --- | --- |
| MVP locale | SQLite + archivio immagini esistente | Confronto esatto in batch/in memoria | La prima analisi usa poche migliaia di vettori; niente indice ANN necessario |
| Multi-job | DB con coda/checkpoint e archivio immagini separato, versionato | Backend di ricerca isolato dietro un’interfaccia | Più processi scrivono insieme, i backup diventano lenti o la query misurata non rispetta la latenza obiettivo |
| Crescita | PostgreSQL + pgvector; immagini in object storage compatibile S3 | Prima esatta, poi HNSW/IVFFlat dopo benchmark | Milioni di vettori, ricerca concorrente o esigenze di servizio distribuite |

Queste soglie sono indicatori, non migrazioni automatiche. Per spostare i vettori,
esportare anche hash, dimensione, modello, preprocessing e ID sorgente; confrontare
conteggi e hash prima/dopo e mantenere il DB originale intatto fino alla verifica.

Con una dimensione d’esempio di 512 float32, 1.665 vettori grezzi occupano circa
3,3 MiB (senza indici e metadati); un milione circa 1,9 GiB. Per 768 dimensioni i
valori sono circa 4,9 MiB e 2,9 GiB. Lo spazio delle immagini, non i vettori MVP,
resta il costo dominante.

## Fonti primarie

- Radford et al., [Learning Transferable Visual Models From Natural Language
  Supervision (CLIP)](https://arxiv.org/abs/2103.00020).
- Oquab et al., [DINOv2: Learning Robust Visual Features without
  Supervision](https://arxiv.org/abs/2304.07193), e [repository ufficiale Meta](https://github.com/facebookresearch/dinov2).
- Scikit-learn, [cosine similarity](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.pairwise.cosine_similarity.html).
- Hugging Face Transformers, [API CLIP e `get_image_features`](https://huggingface.co/docs/transformers/model_doc/clip).
- SQLite, [Online Backup API](https://www.sqlite.org/backup.html).
- pgvector, [repository e documentazione ufficiale](https://github.com/pgvector/pgvector).
