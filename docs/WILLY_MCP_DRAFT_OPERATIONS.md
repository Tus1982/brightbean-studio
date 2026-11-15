# Scritture editoriali Willy — 15 novembre 2026

La Social Room usa `POST /api/v1/posts/{id}/draft-operation` con il Bearer già esistente e il permesso `create_posts`. Nessuna nuova credenziale o migrazione. La rotta non chiama publisher, scheduler o provider.

Operazioni: `archive`, `delete`, `move`, `edit`. Campi comuni: `richiesto_da`; `motivo` obbligatorio per archivio/eliminazione; `dry_run` disponibile per queste due operazioni. Le destinazioni consentite a `move` sono IDEAS, IN_PRODUCTION, TO_APPROVE, ANALYSIS. ARCHIVE conserva il post, DELETE elimina il solo post mai pubblicato.

Il controllo definitivo avviene dentro una transazione, dopo i lock del post e di tutti i figli. Si rifiutano post programmati, approvati, in revisione, in invio, anche parzialmente pubblicati, con identificativi remoti, date di pubblicazione o tentativi nel PublishLog. Si ricontrollano workspace e allowlist degli account. L'audit durevole usa ApiKeyAuditLog nella stessa transazione: se non si salva, l'operazione viene annullata. Autore e istante sono nel registro e nelle note private della bozza.

La posizione editoriale resta separata dallo stato del publisher: TO_APPROVE è una bozza, non un'approvazione. Un marcatore nelle note private mantiene il vincolo di consenso umano anche quando il bot sostituisce le note. La modifica dei testi riusa l'editor esistente e azzera gli override dei soli testi forniti. Non sono ammessi `scheduled_at`, approvazioni o transizioni a scheduled/published.

Esempio di anteprima, su una bozza di prova e con la chiave già configurata:

```json
{"operation":"archive","richiesto_da":"jacopo","motivo":"Prova duplicata","dry_run":true}
```

Risposta: id, esito, colonna finale proposta, dry_run, richiesto_da, quando. L'anteprima non modifica il post e non crea una riga di audit operativo.

Verifica: 69 test passati (39 nuovi e 30 dell'editor esistente), lint Ruff e diff check. I test Django sono stati eseguiti su SQLite in memoria con i modelli reali; simulano anche il cambio di stato fra lettura e lock e il rollback dell'audit. Il comportamento dei lock concorrenti PostgreSQL richiede il normale ambiente CI PostgreSQL. Nessun provider, dato operativo o servizio Railway è stato modificato.

Questa modifica deve essere disponibile nel BrightBean usato da Willy prima di utilizzare gli strumenti editoriali del nuovo connettore. Il connettore rifiuta esplicitamente la scrittura se la rotta manca e non ripiega sul PATCH generico, che accetta anche post programmati.
