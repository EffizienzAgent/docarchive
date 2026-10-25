import json

import frappe


def execute():
	for copy in frappe.get_all(
		"Archive Copy",
		filters={"parenttype": "Business Document", "sidecar_version_id": ("is", "set")},
		fields=["name", "parent", "retain_until", "sidecar_version_id", "sidecar_versions"],
	):
		if copy.sidecar_versions:
			continue
		doc = frappe.db.get_value(
			"Business Document",
			copy.parent,
			["object_key", "retention_extensions", "extension_until"],
			as_dict=True,
		)
		index = (doc.retention_extensions or 0) + 1
		if doc.extension_until and copy.retain_until and copy.retain_until >= doc.extension_until:
			index += 1
		if index < 2:
			continue
		key = f"{doc.object_key}.meta.{index}.json"
		frappe.db.set_value(
			"Archive Copy",
			copy.name,
			"sidecar_versions",
			json.dumps({key: copy.sidecar_version_id}),
			update_modified=False,
		)
