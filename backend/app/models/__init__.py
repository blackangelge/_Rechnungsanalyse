"""
Importiert alle ORM-Modelle, damit Alembic Base.metadata vollständig liest.
"""

from app.models.ai_clients import AIClients  # noqa: F401
from app.models.automation_settings import AutomationSettings  # noqa: F401
from app.models.image_settings import ImageSettings  # noqa: F401
from app.models.import_batch import ImportBatch  # noqa: F401
from app.models.document import Document  # noqa: F401
from app.models.invoice_extraction import InvoiceExtraction  # noqa: F401
from app.models.order_position import OrderPosition  # noqa: F401
from app.models.vendor import Vendor  # noqa: F401
from app.models.customer import Customer  # noqa: F401
from app.models.vendor_bank_account import VendorBankAccount  # noqa: F401
from app.models.system_prompt import SystemPrompt  # noqa: F401
from app.models.workflow_task import WorkflowTask  # noqa: F401
from app.models.document_token_count import DocumentTokenCount  # noqa: F401
from app.models.analysis_history import AnalysisHistory  # noqa: F401
from app.models.document_version import DocumentVersion  # noqa: F401
from app.models.pdf_settings import PdfSettings  # noqa: F401
from app.models.workflow import Workflow  # noqa: F401
from app.models.user import User, UserSession  # noqa: F401
from app.models.data_change import DataChange, DataChangeRun  # noqa: F401
from app.models.ai_query import AIQuery, DocumentAnswer  # noqa: F401
from app.models.schema_def import SchemaVersion, SchemaEntity, SchemaField, SchemaRelation, ExtractedRecord  # noqa: F401
from app.models.validation_rule import ValidationRule  # noqa: F401
from app.models.units import UnitGroup, Unit, UnitAlias, UnitReviewItem  # noqa: F401
from app.models.export_template import ExportTemplate  # noqa: F401
from app.models.assistant_chat import AssistantChat, AssistantMessage  # noqa: F401
from app.models.document_page import DocumentPage  # noqa: F401
