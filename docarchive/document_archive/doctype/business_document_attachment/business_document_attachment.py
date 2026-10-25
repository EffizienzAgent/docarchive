import frappe
from frappe import _
from frappe.model.document import Document


class BusinessDocumentAttachment(Document):
	def validate(self):
		self.refuse_if_started()

	def on_trash(self):
		self.refuse_if_started()

	def refuse_if_started(self):
		if self.parent and frappe.db.get_value(self.parenttype, self.parent, "archive_started_at"):
			frappe.throw(_("Attachments cannot be changed once archiving has started."))
