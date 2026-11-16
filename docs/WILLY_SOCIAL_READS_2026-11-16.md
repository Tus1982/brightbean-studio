# Letture social e archivi conservativi

La raccolta account Google e Pinterest chiamava il metodo base non implementato. TikTok restituisce intenzionalmente metriche account vuote: la lettura dei video con `video.list` resta separata. Il builder esponeva comunque `analytics_available=true` quando non esistevano snapshot.

Google `get_profile` prendeva la prima location dell'account, ignorando quella selezionata. Il controllo salute riscriveva quindi i nomi interni dei negozi con lo stesso profilo. Ora il GET usa la location scelta e include indirizzo/città nel nome interno. Nessuna scrittura su Google.

- Google account: Business Profile Performance API, cinque metriche giornaliere (quattro impressioni Maps/Search e clic al sito). Risposte incomplete o non finalizzate sono errori, non zero. Gli zeri espliciti vengono conservati negli snapshot.
- Pinterest account: `/v5/user_account/analytics`, intervallo effettivo e `summary_metrics`. Nessuna storia sintetica.
- Account senza snapshot nella finestra: `analytics_available=false`, motivo e nessuna metrica inventata. Conservati i metadati di periodo nativi YouTube.
- `GET /api/v1/accounts/{uuid}/google/reviews?limite=20`: rating complessivo e una pagina di recensioni, massimo API 50, `troncato` esplicito. Richiede `view_analytics` e account nell'allowlist; errori HTTP espliciti. Nessuna risposta alle recensioni o rinomina pubblica.
- `draft-operation` permette di sostituire i `media_asset_ids` tramite la validazione esistente. Due operazioni esplicite `archive_error`/`archive_test` conservano post, figli, stato precedente e audit nella stessa transazione. Non modificano righe scheduled/publishing/published. Nessun cambiamento a `delete`.

Un archivio di una prova `awaiting_creator` è solo editoriale: il file già caricato nelle bozze TikTok rimane nell'app. Non viene cancellato, ritirato o pubblicato da questo endpoint.

## Verifiche

286 test su PostgreSQL locale usa e getta, Python 3.12 e dipendenze del progetto: API e allowlist, archivi e media, analytics e raccolta, Instagram/Facebook, YouTube, provider e relativi casi di errore. Prima delle correzioni: riprodotti profilo errato, metriche mancanti, falsa disponibilità e tool rifiutati. Ruff e mypy sui moduli interessati passati. Nessuna migrazione nuova.

Nessun job, sync, archivio, video o post reale eseguito; nessun segreto, permesso o variabile Railway cambiato. Pubblicazione/rilascio esclusi dal mandato di questa PR.

## Verifica dopo il rilascio

La prima raccolta e il normale controllo salute devono confermare metriche e nomi delle nove location. Non sono stati interrogati token o permessi di produzione in questo lavoro: se una chiamata restituisce 403, verificare nell'app esistente:

- Google: accesso Business Profile della location, API Business Profile Performance abilitata nello stesso progetto OAuth, concessione `https://www.googleapis.com/auth/business.manage`; per le recensioni accesso alla Business Profile API v4. Non creare altre chiavi.
- Pinterest: account Business, accesso dell'app alle analytics, concessioni esistenti `user_accounts:read` e `pins:read`.
- TikTok: le metriche aggregate account restano non lette per scelta del provider; non è stato richiesto `user.info.stats`. Le metriche per video non sono state disabilitate.

Fonti API: [Google Performance](https://developers.google.com/my-business/reference/performance/rest/v1/locations/fetchMultiDailyMetricsTimeSeries), [Google Reviews](https://developers.google.com/my-business/reference/rest/v4/accounts.locations.reviews/list), [Pinterest OpenAPI](https://github.com/pinterest/api-description/blob/main/v5/openapi.yaml).
