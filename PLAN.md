# Better Proposals → Pipedrive Products Integration

## Überblick
Produkte aus BP-Angeboten automatisch als Deal-Produkte in Pipedrive anlegen.
Zapier sendet Events (sent/updated/signed) an `/webhooks/betterproposals`.

---

## Phase 1 — Jetzt umsetzbar (vor Zapier-Test)

### 1. BP API Key in .env eintragen
- `BP_API_KEY=<token>` in `.env` hinzufügen

### 2. Test-Endpoint für BP API bauen
- Temporärer `GET /test/bp/{proposal_id}` Endpoint in `app.py`
- Ruft `bp_get_proposal()` auf und gibt die rohe Antwort zurück
- Damit sehen wir die **exakte Struktur der Line Items**
- Wird nach Entwicklung wieder entfernt

### 3. Pipedrive Product-Funktionen in `pipedrive.py`
Neue Funktionen (folgen bestehendem `pd_get/pd_post/pd_put` Pattern):
- `pd_search_product(name)` — `GET /products/search?term=...`
- `pd_create_product(name, price, currency)` — `POST /products`
- `pd_find_or_create_product(name, price, currency)` — Search + Create-Fallback
- `pd_get_deal_products(deal_id)` — `GET /deals/{id}/products`
- `pd_delete_deal_product(deal_id, deal_product_id)` — `DELETE /deals/{id}/products/{id}`
- `pd_add_product_to_deal(deal_id, product_id, item_price, quantity, discount, tax)` — `POST /deals/{id}/products`
- `pd_replace_deal_products(deal_id, products)` — Alle löschen + neu anlegen

### 4. BP Webhook-Logging verbessern
- Vollständigen Zapier-Payload strukturiert loggen
- Event-Type extrahieren (sent/updated/signed) falls im Payload vorhanden
- 200 OK zurückgeben damit Zapier nicht retried

---

## Phase 2 — Nach Montag (Zapier-Payload bekannt)

### 5. Zapier-Payload parsen
- `proposal_id` und `deal_id` aus dem Payload extrahieren
- Event-Type (sent/updated/signed) erkennen
- Anpassen basierend auf tatsächlicher Payload-Struktur

### 6. Sync-Logik in `betterproposals.py`
- `bp_parse_line_items(proposal_data)` — Line Items aus BP-Antwort extrahieren → `[{name, price, quantity, tax, discount}]`
- `bp_sync_products_to_deal(proposal_id, deal_id, event_type)` — Hauptfunktion:
  1. BP API aufrufen → Proposal-Daten holen
  2. Line Items parsen
  3. Für jedes Produkt: `pd_find_or_create_product()`
  4. `pd_replace_deal_products()` aufrufen (alte löschen, neue einfügen)
  5. Note zum Deal hinzufügen mit Event-Info + Produktliste

### 7. Webhook-Handler fertigstellen in `app.py`
- Payload parsen → `bp_sync_products_to_deal()` aufrufen
- Fehlerbehandlung + Logging
- Deduplication (optional, je nach Zapier-Verhalten)

### 8. Deal-Note mit Historie
Format pro Event:
```
📋 Better Proposals — Angebot [gesendet/aktualisiert/signiert]
Produkte:
• Produkt A — 1x €1.500,00
• Produkt B — 2x €750,00
Gesamt: €3.000,00
```

---

## Dateien die geändert werden

| Datei | Änderungen |
|-------|-----------|
| `.env` | `BP_API_KEY` hinzufügen |
| `pipedrive.py` | 7 neue Funktionen (Product CRUD + Deal-Products) |
| `betterproposals.py` | `bp_parse_line_items()`, `bp_sync_products_to_deal()` |
| `app.py` | Webhook-Handler fertigstellen, temp. Test-Endpoint |
| `config.py` | Keine Änderungen nötig (BP config existiert bereits) |
