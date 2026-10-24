from frappe.model.document import Document

from docarchive.slug import validate_slug


class OwnCompany(Document):
	def validate(self):
		validate_slug(self.slug, "Slug")
