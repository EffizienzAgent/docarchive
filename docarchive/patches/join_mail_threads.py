import frappe

DOCUMENT = "Business Document"
CHUNK = 1000
FIELDS = ["name", "own_company", "thread_key", "mail_message_id", "mail_in_reply_to", "mail_references"]


def execute():
	parents = {}

	def find(node):
		root = node
		while parents.setdefault(root, root) != root:
			root = parents[root]
		while parents[node] != root:
			parents[node], node = root, parents[node]
		return root

	def union(a, b):
		a, b = find(a), find(b)
		if a != b:
			parents[max(a, b)] = min(a, b)

	docs = []
	start = 0
	while True:
		rows = frappe.get_all(
			DOCUMENT,
			filters={"thread_key": ("is", "set")},
			fields=FIELDS,
			order_by="name asc",
			offset=start,
			limit=CHUNK,
		)
		for row in rows:
			company = row.own_company or ""
			node = (company, row.thread_key)
			for message_id in [
				row.mail_message_id,
				row.mail_in_reply_to,
				*(row.mail_references or "").split(),
			]:
				if message_id:
					union(node, (company, message_id))
			docs.append((row.name, node))
		if len(rows) < CHUNK:
			break
		start += CHUNK
	by_root = {}
	for name, node in docs:
		by_root.setdefault(find(node)[1], []).append(name)
	for root, names in by_root.items():
		for index in range(0, len(names), CHUNK):
			frappe.db.set_value(
				DOCUMENT,
				{"name": ("in", names[index : index + CHUNK])},
				"thread_root",
				root,
				update_modified=False,
			)
