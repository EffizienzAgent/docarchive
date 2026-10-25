import frappe
from frappe.model.document import Document


class MailOriginal(Document):
	def on_trash(self):
		from docarchive.mail.originals import remove_spool

		name = self.name
		frappe.db.after_commit.add(lambda: remove_spool(name))
