import re

import frappe
from frappe import _

SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def validate_slug(value, label):
	if not SLUG.match(value or ""):
		frappe.throw(_("{0} may only contain lowercase letters, digits and single hyphens.").format(_(label)))
