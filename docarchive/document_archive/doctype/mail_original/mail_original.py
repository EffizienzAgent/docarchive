from frappe.model.document import Document


class MailOriginal(Document):
	def onload(self):
		self.raw_fallback = None
