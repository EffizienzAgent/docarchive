import frappe
from frappe import _
from frappe.model.document import Document

from docarchive import audit
from docarchive.slug import validate_slug

CONTRACT_END = "Contract End Date"
AUDITED_FIELDS = (
	"slug",
	"archive",
	"retention_from",
	"default_retention_years",
	"counterparty_required",
	"number_required",
	"contract_end_required",
)


class BusinessDocumentType(Document):
	def validate(self):
		validate_slug(self.slug, "Slug")
		if self.default_retention_years is not None and self.default_retention_years < 0:
			frappe.throw(_("Retention years must be positive."))
		if self.contract_end_required and self.retention_from != CONTRACT_END:
			self.contract_end_required = 0

	def on_update(self):
		audit.log_settings(self, AUDITED_FIELDS)
