"""
Importiert alle ORM-Modelle, damit Alembic Base.metadata vollständig liest.
"""

from app.models.ai_clients import AIClients  # noqa: F401
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
