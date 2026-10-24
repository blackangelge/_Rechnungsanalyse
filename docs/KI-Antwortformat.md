# KI-Antwortformat (Rechnungsextraktion)

Die KI muss **ausschließlich ein JSON-Objekt** zurückgeben (kein Text davor/danach, kein Markdown).
Unbekannte Werte = `null`; Zahlen mit Punkt (`1234.56`); Datum `YYYY-MM-DD`; `positionen` ist immer eine Liste.
Erkannt wird das Format, sobald `lieferant`, `rechnungsdaten` oder `zahlungsinformationen` vorkommt.
Die Vorlage steht in der App unter Einstellungen → Systemprompts (Typ 3 „Antwortformat“ → „Standard-Antwortformat als Vorlage einfügen“).

| Bereich | Feld | Typ | Wird gespeichert in |
|---|---|---|---|
| lieferant | name | Text | `invoice_extractions.vendor_id` (Lieferantenname) |
| lieferant.anschrift | strasse, plz, ort, land | Text | Lieferanten-Stammdaten |
| lieferant | hrb_nummer, steuernummer, ust_id_nr | Text | Extraktion / Lieferant |
| lieferant.bankverbindung | bank_name, iban, bic | Text | Extraktion / Bankkonto |
| rechnungsdaten | rechnungsnummer, kundennummer | Text | Extraktion |
| rechnungsdaten | rechnungsdatum, faelligkeit | Datum | Extraktion |
| positionen[] | position_nr | Zahl | Reihenfolge |
| positionen[] | artikelbezeichnung, artikelnummer_lieferant | Text | `order_positions` |
| positionen[] | menge, einzelpreis, gesamtpreis | Zahl | `quantity`, `unit_price_netto`, `total_price` |
| positionen[] | mengeneinheit | Text (wie gedruckt) | `unit` |
| positionen[] | mengeneinheit_typ | `stueck`\|`verpackung`\|`gewicht`\|`volumen`\|`laenge`\|`flaeche`\|`pauschal`\|`dienstleistung`\|`sonstige` | `unit_type` |
| positionen[] | preis_je_menge | Zahl (Preis gilt je N Einheiten, z. B. 100) | `price_quantity` |
| positionen[] | verpackungsinhalt, verpackungsinhalt_einheit | Zahl, Text (VPE/VE/Pack) | `pack_size`, `pack_unit` |
| positionen[] | preiseinheit | Text (z. B. „je t“) | nur `raw_response` |
| positionen[] | steuersatz, waehrung | Zahl, Text | `raw_response` / Position |
| positionen[].preisnachlass | betrag, prozent, bezeichnung | Zahl/Text | Nachlass |
| zahlungsinformationen | gesamtbetrag_netto, gesamtbetrag_brutto | Zahl | Extraktion |
| zahlungsinformationen | umsatzsteuer_zusammenfassung[] (steuersatz, nettobetrag, steuerbetrag) | Liste | Extraktion / Prüfung |
| zahlungsinformationen | waehrung, zahlungsbedingungen | Text | Extraktion |
| zahlungsinformationen.skonto | prozent, betrag, frist_tage | Zahl | Extraktion / Excel |

Beispiel:

```json
{
  "lieferant": {
    "name": "...",
    "anschrift": { "strasse": "...", "plz": "...", "ort": "...", "land": "..." },
    "hrb_nummer": "...", "steuernummer": "...", "ust_id_nr": "...",
    "bankverbindung": { "bank_name": "...", "iban": "...", "bic": "..." }
  },
  "rechnungsdaten": { "rechnungsnummer": "...", "rechnungsdatum": "YYYY-MM-DD", "faelligkeit": "YYYY-MM-DD", "kundennummer": "..." },
  "positionen": [
    {
      "position_nr": 1, "artikelbezeichnung": "...", "artikelnummer_lieferant": "...",
      "menge": 4400, "mengeneinheit": "Stück", "einzelpreis": 90.0, "gesamtpreis": 3960.0, "waehrung": "EUR", "steuersatz": 19.0,
      "preiseinheit": null, "preis_je_menge": 100, "verpackungsinhalt": null, "verpackungsinhalt_einheit": null, "mengeneinheit_typ": "stueck",
      "preisnachlass": { "betrag": null, "prozent": null, "bezeichnung": null }
    }
  ],
  "zahlungsinformationen": {
    "gesamtbetrag_netto": 0.0,
    "umsatzsteuer_zusammenfassung": [{ "steuersatz": 19.0, "nettobetrag": 0.0, "steuerbetrag": 0.0 }],
    "gesamtbetrag_brutto": 0.0, "waehrung": "EUR",
    "skonto": { "prozent": null, "betrag": null, "frist_tage": null }, "zahlungsbedingungen": "..."
  }
}
```

Dokumententyp-Erkennung antwortet stattdessen mit `{"dokumententyp_id": 1, "dokumententyp_name": "Eingangsrechnung"}`.
Freie KI-Frage (❓): Antwort ist nur der Wert als Text, kein JSON.
