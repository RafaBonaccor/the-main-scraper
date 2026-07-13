# AGENTS.md

Questo file descrive come un orchestratore AI dovrebbe usare il progetto.

## Superficie consigliata

Usa la CLI, non la GUI.

Entry point:

```powershell
python main.py
```

Comandi orchestrator-friendly:

- `python main.py status --stdout-json`
- `python main.py run google_maps ... --stdout-json --format none`
- `python main.py run vinted ... --stdout-json --format none`
- `python main.py run vinted_descriptions ... --stdout-json --format none`
- `python main.py run subito ... --stdout-json --format none`
- `python main.py contact subito ... --stdout-json`
- `python main.py browser ... --stdout-json`

## Flag utili per orchestratori

### `--stdout-json`

Stampa un payload JSON stabile su stdout invece del riepilogo umano.

### `--quiet`

Sopprime il riepilogo testuale.

### `--format none`

Disabilita export `json/csv/xlsx` quando vuoi solo il payload su stdout.

## Schema di risposta

### `status`

Campi principali:

- `ok`
- `command`
- `generated_at`
- `project_root`
- `python_executable`
- `supported_sources`
- `supported_contact_sources`
- `features`
- `paths`

### `run`

Campi principali:

- `ok`
- `schema_version`
- `command`
- `source`
- `generated_at`
- `row_count`
- `meta`
- `rows`
- `files`
- `normalized`

### `contact`

Campi principali:

- `ok`
- `schema_version`
- `command`
- `source`
- `generated_at`
- `result`
- `normalized`

### `browser`

Campi principali:

- `ok`
- `schema_version`
- `command`
- `generated_at`
- `result`
- `normalized`

### errore runtime

Campi principali:

- `ok: false`
- `schema_version`
- `command`
- `source`
- `generated_at`
- `error.type`
- `error.message`

## Sezione `normalized`

La sezione `normalized` e la superficie consigliata per gli orchestratori.

### `run` + `source=vinted`

Ogni riga normalizzata contiene almeno:

- `id`
- `title`
- `link`
- `search_term`
- `secondary_badge_text`
- `has_ricercato_badge`
- `price_text`
- `price_value`
- `shipping_text`
- `shipping_value`
- `total_text`
- `total_value`
- `description`

### `run` + `source=subito`

Ogni riga normalizzata contiene almeno:

- `title`
- `company`
- `location`
- `link`
- `published_at`
- `distance_km`
- `geo_decision`
- `screening_decision`
- `screening_score`
- `contact_status`
- `description`

### `run` + `source=google_maps`

Ogni riga normalizzata contiene almeno:

- `name`
- `category`
- `city`
- `address`
- `link`
- `website`
- `phone`
- `email`
- `rating`
- `reviews_count`
- `opportunity_score`
- `lead_priority`

### `contact` + `source=subito`

La sezione `normalized.results` contiene almeno:

- `link`
- `ok`
- `prepared`
- `submitted`
- `attachment_uploaded`
- `message_filled`
- `login_required`
- `current_url`

## Modalita browser consigliata

Valore raccomandato:

- `sessione_persistente`

Usala quando:

- il sito richiede login
- vuoi mantenere cookie
- devi riusare una sessione reale tra piu run

## Sorgenti supportate

- `google_maps`
- `vinted`
- `vinted_descriptions`
- `subito`
- `custom_site`

Contatto supportato:

- `subito`

## Flussi pratici

### Vinted

1. `run vinted` per raccogliere card e link
2. seleziona link interessanti o costruisci un `links-file`
3. `run vinted_descriptions` per entrare nel dettaglio
4. leggi `description`, `price`, `shipping_price`, `total_price`

Nota importante:

- il prezzo del dettaglio Vinted usa il valore con `Protezione acquisti`

### Subito

1. `run subito`
2. opzionalmente usa screening OpenAI
3. prepara `links-file`
4. `contact subito`

### Google Maps

1. `run google_maps`
2. leggi `rows`
3. usa `meta` e i campi lead per ordinamento o follow-up

## File importanti

- `main.py`
- `scraper_app/runner.py`
- `scraper_app/ui.py`
- `scraper_app/browser_runtime.py`
- `scraper_app/sources/vinted.py`
- `scraper_app/sources/subito.py`
- `scraper_app/sources/subito_contact.py`
- `scraper_app/sources/google_maps.py`
- `scraper_app/vinted_database.py`

## Regole operative

- non assumere che il browser sia loggato: controlla `browser-mode`
- se ti serve integrazione machine-to-machine usa sempre `--stdout-json`
- se non ti servono file usa `--format none`
- quando modifichi selettori DOM, testa live
- quando modifichi GUI, la CLI resta la superficie primaria per orchestratori
