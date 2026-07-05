"""initial

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-06 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

database_name_ai_clients = "ai_clients"
database_name_image_settings = "image_settings"
database_name_import_batches = "import_batches"
database_name_documents = "documents"
database_name_invoice_extractions = "invoice_extractions"
database_name_order_positions = "order_positions"
database_name_vendor = "vendor"
database_name_customer = "customer"
database_name_vendor_bank_accounts = "vendor_bank_accounts"
database_name_system_prompts = "system_prompts"
database_name_workflow_tasks = "workflow_tasks"
database_name_documents_token_counts = "documents_token_counts"
database_name_export_config= "export_config"

def upgrade() -> None:
    # AI Clients Table
    op.create_table(
        database_name_ai_clients,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False), #Name des KI-Rechners
        sa.Column("api_key", sa.String(200), nullable=True), #API-Key 
        sa.Column("model_name", sa.String(200), nullable=False), #Name des KI-Modells, z.B. qwen/qwen3.6
        sa.Column("primary_type", sa.Integer(), nullable=False, server_default="0"), #0=Dokumententyp bestimmen, 1= Eingangsrechnungsanalyse, 2=....
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="32000"), #Maximal Output Token, die zurück kommen
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.1"), # Niedrigere Werte führen zu deterministischeren Antworten, höhere Werte zu kreativeren Antworten. Je nach Anwendungsfall kann es sinnvoll sein, diesen Wert anzupassen.
        sa.Column("chat_response", sa.Boolean(), nullable=False, server_default="0"), # Um Chats fortzusetzen, anstatt jedes Mal eine neue Anfrage zu stellen 
        sa.Column("active", sa.Boolean(), nullable=False, server_default="0"), # Ob die KI-Konfiguration aktiv ist und für neue Importvorgänge verwendet werden soll
        sa.Column("reasoning", sa.String(20), nullable=False, server_default="off"), # Reasoning-Modus: "off" | "low" | "medium" | "high" | "on". Dieser Wert wird als reasoning_effort an OpenAI-kompatible APIs übergeben (sofern != "off") und steuert, wie viel Aufwand die KI in die Beantwortung der Anfrage steckt. Je nach Anwendungsfall kann es sinnvoll sein, diesen Wert anzupassen.
        sa.Column("ip_address", sa.String(20), nullable=False, server_default=""), # IP-Addresse oder Endpunkt-Typ der KI-API, z.B. "openai" für OpenAI-kompatible APIs, "lmstudio" für LM Studio, etc. Je nach Anwendungsfall kann es sinnvoll sein, diesen Wert anzupassen, um verschiedene KI-APIs zu unterstützen
        sa.Column("endpoint_type", sa.String(20), nullable=False, server_default="openai"), # API-Endpunkt-Typ: "openai" = POST /chat/completions, "lmstudio" = POST /api/v1/chat. Dieser Wert steuert, wie die Anfragen an die KI-API formuliert werden, um die Kompatibilität mit verschiedenen KI-APIs zu gewährleisten. Je nach Anwendungsfall kann es sinnvoll sein, diesen Wert anzupassen.
        sa.Column("port", sa.String(5), nullable=False, server_default="1234"), # Port der KI-API, z.B. "1234" für eine lokale API, oder leer lassen für cloudbasierte APIs, bei denen der Port in der IP-Adresse enthalten ist. Je nach Anwendungsfall kann es sinnvoll sein, diesen Wert anzupassen.
        sa.Column("parallel_request", sa.Integer(), nullable=False, server_default="1"), # Anzahl der parallelen Anfragen, die an diese KI-Konfiguration gesendet werden können, um die Verarbeitungsgeschwindigkeit zu erhöhen. Je nach Anwendungsfall und Leistungsfähigkeit der KI-API kann es sinnvoll sein, diesen Wert anzupassen.
        sa.Column("timeout_at", sa.DateTime(timezone=True), nullable=True), #Zeitpunkt, an dem der KI-Rechner nicht mehr antwortet
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f(f"ix_{database_name_ai_clients}_id"), database_name_ai_clients, ["id"], unique=False)

    # Image Settings Table
    op.create_table(
        database_name_image_settings,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dpi", sa.Integer(), nullable=False, server_default="150"), # DPI-Wert für die Bildkonvertierung, um die Qualität der Bilder zu steuern. Ein höherer DPI-Wert führt zu einer besseren Qualität, aber auch zu größeren Dateien und längeren Verarbeitungszeiten. Je nach Anwendungsfall kann es sinnvoll sein, diesen Wert anzupassen.
        sa.Column("image_format", sa.String(10), nullable=False, server_default="PNG"), # Bildformat für die Konvertierung, um die Kompatibilität mit der KI-API zu gewährleisten. PNG bietet eine gute Qualität und verlustfreie Komprimierung, während JPEG kleinere Dateien ermöglicht, aber mit Qualitätsverlust einhergeht. Je nach Anwendungsfall kann es sinnvoll sein, dieses Format anzupassen.
        sa.Column("jpeg_quality", sa.Integer(), nullable=False, server_default="85"), # JPEG-Qualitätsstufe, um die Qualität der JPEG-Bilder zu steuern. Ein höherer Wert führt zu einer besseren Qualität, aber auch zu größeren Dateien. Je nach Anwendungsfall kann es sinnvoll sein, diesen Wert anzupassen.
        sa.Column("grayscale", sa.Boolean(), nullable=False, server_default="0"), # Ob die Bilder in Graustufen konvertiert werden sollen, um die Dateigröße zu reduzieren und die Verarbeitung durch die KI zu erleichtern. Je nach Anwendungsfall kann es sinnvoll sein, diesen Wert anzupassen.
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),

        sa.CheckConstraint("id = 1", name=f"ck_{database_name_image_settings}_singleton"), # Sicherstellen, dass nur ein Eintrag existiert
        sa.CheckConstraint("dpi BETWEEN 72 AND 600", name=f"ck_{database_name_image_settings}_dpi"),# DPI-Wert sollte in einem sinnvollen Bereich liegen
        sa.CheckConstraint("image_format IN ('PNG', 'JPEG')", name=f"ck_{database_name_image_settings}_format"),# Nur bestimmte Bildformate erlauben
        sa.CheckConstraint("jpeg_quality BETWEEN 1 AND 100", name=f"ck_{database_name_image_settings}_jpeg_quality"),# JPEG-Qualität sollte zwischen 1 und 100 liegen
    )
    op.execute(f"""INSERT INTO {database_name_image_settings} (id) VALUES (1)""") # Initialen Eintrag für die Singleton-Tabelle erstellen

    # Import Batches Table
    op.create_table(
        database_name_import_batches,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("import_folder_path", sa.String(1000), nullable=False), # Ursprünglicher Pfad, von dem die Dokumente importiert wurden, z.B. "/mnt/invoices_to_process"
        sa.Column("storage_folder_path", sa.String(1000), nullable=False), # Pfad, unter dem die Dokumente im System gespeichert werden, z.B. "/data/invoices/2024/04/06/batch_12345"
        sa.Column("company_name", sa.String(255), nullable=False), # Name der Firma, zu der die Dokumente gehören, z.B. "Muster GmbH"
        sa.Column("year", sa.Integer(), nullable=False), # Jahr, zu dem die Dokumente gehören, z.B. 2024
        sa.Column("comment", sa.Text(), nullable=True), # Freitextfeld für zusätzliche Informationen zum Importvorgang
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"), # Status des Importvorgangs, z.B. "pending", "processing", "completed", "failed"
        sa.Column("folder_sync", sa.Boolean(), nullable=True, server_default="0"), # Ob der Ordner mit den Dokumenten automatisch synchronisiert werden soll, um neue Dokumente zu erkennen
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True), # Zeitpunkt, zu dem der Importvorgang gestartet wurde
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True), # Zeitpunkt, zu dem der Importvorgang abgeschlossen wurde
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f(f"ix_{database_name_import_batches}_id"), database_name_import_batches, ["id"], unique=False)

    # Documents Table
    op.create_table(
        database_name_documents,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False), # Fremdschlüssel zum Import Batch, um die Dokumente einem bestimmten Importvorgang zuordnen zu können
        sa.Column("original_filename", sa.String(500), nullable=False), # Ursprünglicher Dateiname des Dokuments, z.B. "invoice_12345.pdf"
        sa.Column("stored_filename", sa.String(500), nullable=True), # Tatsächlicher Dateiname, unter dem das Dokument im System gespeichert wird, z.B. "{id}.pdf"
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False, server_default="0"), # Dateigröße in Bytes, um Informationen über die Größe des Dokuments zu haben
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"), # Anzahl der Seiten im Dokument, um Informationen über die Länge des Dokuments zu haben
        sa.Column("document_type", sa.Integer(), nullable=False, server_default="0"), # Dokumententyp, z.B. 0=unbekannt, 1=Eingangsrechnung, 2=Bestellung, 3=Vertrag, etc. Je nach Anwendungsfall können hier weitere Typen definiert werden
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"), # Status der Dokumentenverarbeitung, z.B. "pending", "processing", "completed", "failed"
        sa.Column("raw_response", sa.Text(), nullable=True, server_default="{}"), # Rohdaten der KI-Antwort, um die ursprüngliche Antwort der KI zu speichern und bei Bedarf darauf zugreifen zu können}"),
        sa.Column("soft_deleted", sa.Boolean(), nullable=False, server_default="0"), # Ob das Dokument als gelöscht markiert ist, um eine einfache Möglichkeit zu haben, Dokumente zu "löschen", ohne sie tatsächlich aus der Datenbank zu entfernen, um die Datenintegrität zu gewährleisten und versehentliche Datenverluste zu vermeiden
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], [f"{database_name_import_batches}.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f(f"{database_name_documents}_id"), database_name_documents, ["id"], unique=False)

    # Invoice Extractions Table
    op.create_table(
        database_name_invoice_extractions,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False), # Fremdschlüssel zum Dokument, um die extrahierten Daten einem bestimmten Dokument zuordnen zu können. Da es sich um eine 1:1 Beziehung handelt
        sa.Column("vendor_id", sa.String(255), nullable=True), # Name des Lieferanten, z.B. "Muster Lieferant GmbH". Dieses Feld dient als Redundanz, um den Namen des Lieferanten direkt in der Invoice Extractions Tabelle zu haben, ohne immer auf die Vendor Tabelle joinen zu müssen. Es kann bei der Extraktion der Daten aus dem Dokument gefüllt werden und sollte idealerweise mit dem Namen in der Vendor Tabelle übereinstimmen, um Konsistenz zu gewährleisten.
        sa.Column("invoice_number", sa.String(100), nullable=True), # Rechnungsnummer, z.B. "INV-12345"
        sa.Column("invoice_date", sa.Date(), nullable=True), # Rechnungsdatum, z.B. "2024-04-01"
        sa.Column("due_date", sa.Date(), nullable=True), # Fälligkeitsdatum, z.B. "2024-04-30"
        sa.Column("total_amount_netto", sa.Numeric(12, 2), nullable=True), # Gesamtbetrag netto, z.B. 199.99
        sa.Column("total_amount_brutto", sa.Numeric(12, 2), nullable=True),# Gesamtbetrag brutto, z.B. 237.78
        sa.Column("total_tax_value", sa.Numeric(12, 2), nullable=True), # Gesamtsteuerbetrag, z.B. 37.79
        sa.Column("total_tax", sa.Numeric(12, 2), nullable=True), # Gesamtsteuerbetrag, z.B. 37.79
        sa.Column("discount_amount", sa.Numeric(12, 2), nullable=True), # Gesamtbetrag der Rabatte, z.B. 20.00
        sa.Column("cash_discount_amount", sa.Numeric(12, 2), nullable=True), # Skontobetrag, z.B. 10.00
        sa.Column("payment_terms", sa.Text(), nullable=True), # Zahlungsbedingungen, z.B. "Zahlbar innerhalb von 30 Tagen ohne Abzug", "2% Skonto bei Zahlung innerhalb von 10 Tagen", etc.
        sa.Column("raw_response", sa.Text(), nullable=True), # Rohdaten der KI-Antwort, um die ursprüngliche Antwort der KI zu speichern und bei Bedarf darauf zugreifen zu können
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], [f"{database_name_documents}.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id"),
    )
    op.create_index(op.f(f"ix_{database_name_invoice_extractions}_id"), database_name_invoice_extractions, ["id"], unique=False)

    # Vendor Table
    op.create_table(
        database_name_vendor,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.String(255), nullable=True), # Fremdschlüssel zum Dokument, um den Lieferanten einem bestimmten Dokument zuordnen zu können. Da es sich um eine 1:1 Beziehung handelt
        sa.Column("name", sa.String(255), nullable=False), # Name des Lieferanten, z.B. "Muster Lieferant GmbH"
        sa.Column("street", sa.String(255), nullable=True), # Straße des Lieferanten, z.B. "Musterstraße 1"
        sa.Column("postal_code", sa.String(20), nullable=True), # Postleitzahl des Lieferanten, z.B. "12345"
        sa.Column("city", sa.String(100), nullable=True), # Stadt des Lieferanten, z.B. "Musterstadt"
        sa.Column("country", sa.String(100), nullable=True), # Land des Lieferanten, z.B. "Deutschland"
        sa.Column("hrb_number", sa.String(100), nullable=True), # Handelsregisternummer des Lieferanten, z.B. "HRB 12345"
        sa.Column("tax_number", sa.String(100), nullable=True), # Steuernummer des Lieferanten, z.B. "123/456/78901"
        sa.Column("vat_id", sa.String(100), nullable=True),

        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f(f"ix_{database_name_vendor}_id"), database_name_vendor, ["id"], unique=False)

    # Customer Table
    op.create_table(
        database_name_customer,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.String(255), nullable=True), # Fremdschlüssel zum Dokument, um den Kunden einem bestimmten Dokument zuordnen zu können. Da es sich um eine 1:1 Beziehung handelt
        sa.Column("name", sa.String(255), nullable=False), # Name des Kunden, z.B. "Muster Kunde GmbH"
        sa.Column("street", sa.String(255), nullable=True), # Straße des Kunden, z.B. "Musterstraße 1"
        sa.Column("postal_code", sa.String(20), nullable=True), # Postleitzahl des Kunden, z.B. "12345"
        sa.Column("city", sa.String(100), nullable=True), # Stadt des Kunden, z.B. "Musterstadt"
        sa.Column("country", sa.String(100), nullable=True),# Land des Kunden, z.B. "Deutschland"

        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f(f"ix_{database_name_customer}_id"), database_name_customer, ["id"], unique=False)

    # Vendor Bank Accounts Table
    op.create_table(
        database_name_vendor_bank_accounts,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vendor_id", sa.Integer(), nullable=False), # Fremdschlüssel zum Lieferanten, um die Bankverbindung einem bestimmten Lieferanten zuordnen zu können. Da es sich um eine 1:n Beziehung handelt, da ein Lieferant mehrere Bankverbindungen haben kann
        sa.Column("bank_name", sa.String(255), nullable=True), # Name der Bank, z.B. "Muster Bank"
        sa.Column("iban", sa.String(50), nullable=True), # IBAN der Bankverbindung, z.B. "DE89370400440532013000"
        sa.Column("bic", sa.String(20), nullable=True), # BIC der Bankverbindung, z.B. "MUSTDEFFXXX"
        sa.ForeignKeyConstraint(["vendor_id"], [f"{database_name_vendor}.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f(f"ix_{database_name_vendor_bank_accounts}_id"), database_name_vendor_bank_accounts, ["id"], unique=False)

    # Order Positions Table
    op.create_table(
        database_name_order_positions,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False), # Fremdschlüssel zum Dokument, um die Position einem bestimmten Dokument zuordnen zu können. Da es sich um eine 1:n Beziehung handelt, da ein Dokument mehrere Positionen haben kann
        sa.Column("position_index", sa.Integer(), nullable=False, server_default="0"), # Index der Position in der Reihenfolge, um die ursprüngliche Reihenfolge der Positionen im Dokument zu erhalten
        sa.Column("product_name", sa.String(255), nullable=True), # Name des Produkts oder der Dienstleistung, z.B. "Muster Produkt"
        sa.Column("product_description", sa.Text(), nullable=True), # Beschreibung des Produkts oder der Dienstleistung, z.B. "Dies ist ein Muster Produkt, das für Demonstrationszwecke verwendet wird."
        sa.Column("article_number", sa.String(100), nullable=True), # Artikelnummer oder SKU, z.B. "MP-12345"
        sa.Column("unit_price_netto", sa.Numeric(12, 4), nullable=True), # Nettopreis pro Einheit, z.B. 19.9900
        sa.Column("unit_price_brutto", sa.Numeric(12, 4), nullable=True), # Bruttopreis pro Einheit, z.B. 23.7881
        sa.Column("tax", sa.Numeric(12, 2), nullable=True), # Steuersatz in Prozent, z.B. 19.00
        sa.Column("quantity", sa.Numeric(12, 4), nullable=True), # Menge der Position, z.B. 2.0000
        sa.Column("unit", sa.String(50), nullable=True), # Einheit der Position, z.B. "Stück", "kg", "m", etc.
        sa.Column("discount", sa.String(100), nullable=True), # Rabatt auf die Position, z.B. "10% Rabatt", "5 Euro Rabatt", etc.
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f(f"ix_{database_name_order_positions}_id"), database_name_order_positions, ["id"], unique=False)

    # System Prompts Table
    op.create_table(
        database_name_system_prompts,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False), # Name des System Prompts, z.B. "Standard Prompt für Eingangsrechnungen", "Prompt für Vertragsanalyse", etc.
        sa.Column("content", sa.Text(), nullable=False), # Inhalt des System Prompts, z.B. "Du bist ein KI-Modell, das dabei hilft, Informationen aus Dokumenten zu extrahieren. Bitte extrahiere die folgenden Informationen: ...", etc.
        sa.Column("type", sa.Integer(), nullable=False, server_default="0"), # Typ des Prompts, z.B. 0=Dokumentenzuordnung, 1=Eingangsrechnung, etc. Je nach Anwendungsfall können hier weitere Typen definiert werden
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f(f"ix_{database_name_system_prompts}_id"), database_name_system_prompts, ["id"], unique=False)

    # Workflow Tasks Table
    op.create_table(
        database_name_workflow_tasks,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.String(36), nullable=False),  # UUID des Gesamt-Workflows
        sa.Column("payload", postgresql.JSONB(), nullable=False),  # Eingabedaten
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"), # Status: pending, in_progress, completed, failed
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"), # Anzahl der bisherigen Versuche, um die Aufgabe erfolgreich abzuschließen, um Informationen über die Stabilität der Aufgabe und die Notwendigkeit von Anpassungen zu haben
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"), # Maximale Anzahl der Versuche, um die Aufgabe erfolgreich abzuschließen, bevor sie als fehlgeschlagen markiert wird, um Informationen über die Stabilität der Aufgabe und die Notwendigkeit von Anpassungen zu haben
        sa.Column("result", postgresql.JSONB(), nullable=True), # Ergebnis der Aufgabe, z.B. die extrahierten Daten oder die Antwort der KI, um die Ergebnisse der Aufgabe zu speichern und bei Bedarf darauf zugreifen zu können
        sa.Column("error", sa.Text(), nullable=True), # Fehlermeldung, falls die Aufgabe fehlgeschlagen ist, um Informationen über Fehler zu haben und bei Bedarf darauf zugreifen zu können
        sa.Column("worker_id", sa.String(100), nullable=True),  # ID des Arbeiters, der die Aufgabe bearbeitet, um Informationen über die Bearbeitung der Aufgabe zu haben und bei Bedarf darauf zugreifen zu können
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True), # Zeitpunkt, zu dem die Aufgabe gesperrt wurde, um Informationen über die Bearbeitung der Aufgabe zu haben und bei Bedarf darauf zugreifen zu können
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(f"ix_{database_name_workflow_tasks}_status", database_name_workflow_tasks, ["status"])
    op.create_index(f"ix_{database_name_workflow_tasks}_workflow_id", database_name_workflow_tasks, ["workflow_id"])

    op.create_table(
        database_name_documents_token_counts,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("output_token_count", sa.Integer(), nullable=False, server_default="0"), # Anzahl der Tokens, die die KI als Antwort zurückgegeben hat, um Informationen über die Größe der KI-Antwort zu haben
        sa.Column("input_token_count", sa.Integer(), nullable=False, server_default="0"), # Anzahl der Tokens, die an die KI gesendet wurden, um Informationen über die Größe der KI-Anfrage zu haben
        sa.Column("reasoning_count", sa.Integer(), nullable=False, server_default="0"), # Anzahl der Schritte oder "Reasoning Chains", die die KI bei der Verarbeitung des Dokuments durchlaufen hat, um Informationen über die Komplexität der Verarbeitung zu haben
        sa.Column("time_spent_seconds", sa.Float(), nullable=False, server_default="0"), # Zeit in Sekunden, die die KI für die Verarbeitung des Dokuments benötigt hat, um Informationen über die Dauer der Verarbeitung zu haben
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], [f"{database_name_documents}.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f(f"ix_{database_name_documents_token_counts}_id"), database_name_documents_token_counts, ["id"], unique=False)

    op.create_table(
        database_name_export_config,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_fields", postgresql.JSONB(), nullable=False, server_default="[]"), # Liste der Felder, die im Rechnungen-Sheet des Excel-Exports enthalten sein sollen, um die Flexibilität des Exports zu erhöhen und es den Benutzern zu ermöglichen, nur die für sie relevanten Informationen zu exportieren
        sa.Column("position_fields", postgresql.JSONB(), nullable=False, server_default="[] "), # Liste der Felder, die im Positionen-Sheet des Excel-Exports enthalten sein sollen, um die Flexibilität des Exports zu erhöhen und es den Benutzern zu ermöglichen, nur die für sie relevanten Informationen zu exportieren
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name=f"ck_{database_name_export_config}_singleton"), # Sicherstellen, dass nur ein Eintrag existiert
    )

def downgrade() -> None:
    op.drop_index(op.f(f"ix_{database_name_ai_clients}_id"), table_name=database_name_ai_clients)
    op.drop_table(database_name_ai_clients)

    op.drop_index(op.f(f"ix_{database_name_image_settings}_id"), table_name=database_name_image_settings)
    op.drop_table(database_name_image_settings)

    op.drop_index(op.f(f"ix_{database_name_import_batches}_id"), table_name=database_name_import_batches)
    op.drop_table(database_name_import_batches)

    op.drop_index(op.f(f"ix_{database_name_documents}_id"), table_name=database_name_documents)
    op.drop_table(database_name_documents)

    op.drop_index(op.f(f"ix_{database_name_invoice_extractions}_id"), table_name=database_name_invoice_extractions)
    op.drop_table(database_name_invoice_extractions)
    
    op.drop_index(op.f(f"ix_{database_name_order_positions}_id"), table_name=database_name_order_positions)
    op.drop_table(database_name_order_positions)

    op.drop_index(op.f(f"ix_{database_name_vendor}_id"), table_name=database_name_vendor)
    op.drop_table(database_name_vendor)

    op.drop_index(op.f(f"ix_{database_name_customer}_id"), table_name=database_name_customer)
    op.drop_table(database_name_customer)
    
    op.drop_index(op.f(f"ix_{database_name_vendor_bank_accounts}_id"), table_name=database_name_vendor_bank_accounts)
    op.drop_table(database_name_vendor_bank_accounts)

    op.drop_index(op.f(f"ix_{database_name_system_prompts}_id"), table_name=database_name_system_prompts)
    op.drop_table(database_name_system_prompts)

    op.drop_index(f"ix_{database_name_workflow_tasks}_workflow_id", table_name=database_name_workflow_tasks)
    op.drop_index(f"ix_{database_name_workflow_tasks}_status", table_name=database_name_workflow_tasks)
    op.drop_table(database_name_workflow_tasks)

    op.drop_index(op.f(f"ix_{database_name_documents_token_counts}_id"), table_name=database_name_documents_token_counts)
    op.drop_table(database_name_documents_token_counts)

    op.drop_index(op.f(f"ix_{database_name_export_config}_id"), table_name=database_name_export_config)
    op.drop_table(database_name_export_config)