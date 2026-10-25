import frappe

COMMUNICATION = "Communication"
CHUNK = 500


def readable(communications) -> set[str]:
	communications = list(dict.fromkeys(name for name in communications if name))
	found = set()
	for start in range(0, len(communications), CHUNK):
		chunk = communications[start : start + CHUNK]
		found.update(
			frappe.get_list(COMMUNICATION, filters={"name": ("in", chunk)}, pluck="name", limit=len(chunk))
		)
	return found
