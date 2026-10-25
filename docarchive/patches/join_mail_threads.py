import frappe

from docarchive.mail.thread import MAX_ROOTS

DOCUMENT = "Business Document"
CHUNK = 1000
FIELDS = [
	"name",
	"own_company",
	"thread_key",
	"mail_message_id",
	"mail_in_reply_to",
	"mail_references",
	"correspondence_at",
	"creation",
]


def execute():
	docs = _documents()
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

	by_message = {}
	for doc in docs:
		if doc.mail_message_id:
			by_message.setdefault((doc.own_company, doc.mail_message_id), doc.name)
	for doc in docs:
		node = ("doc", doc.name)
		union(node, ("key", doc.own_company, doc.thread_key))
		referenced = [*(doc.mail_references or "").split(), doc.mail_in_reply_to]
		targets = [by_message.get((doc.own_company, ref)) for ref in dict.fromkeys(referenced) if ref]
		sets = []
		for target in filter(None, targets):
			if find(("doc", target)) not in sets:
				sets.append(find(("doc", target)))
		for found in sets[:MAX_ROOTS]:
			union(node, found)
	groups = {}
	for doc in docs:
		groups.setdefault(find(("doc", doc.name)), []).append(doc)
	for members in groups.values():
		first = min(members, key=lambda doc: (doc.correspondence_at or doc.creation, doc.creation))
		names = [doc.name for doc in members]
		for index in range(0, len(names), CHUNK):
			frappe.db.set_value(
				DOCUMENT,
				{"name": ("in", names[index : index + CHUNK])},
				"thread_root",
				first.thread_key,
				update_modified=False,
			)


def _documents():
	docs, start = [], 0
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
			row.own_company = row.own_company or ""
		docs.extend(rows)
		if len(rows) < CHUNK:
			return docs
		start += CHUNK
