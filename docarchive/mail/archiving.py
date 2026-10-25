import frappe
from frappe import _
from frappe.utils import get_datetime, getdate

from docarchive import audit, content, counterparty, settings
from docarchive.document_types import CORRESPONDENCE, INTERNAL_CORRESPONDENCE
from docarchive.mail import access, accounts, events, headers
from docarchive.mail import thread as threads

DOCUMENT = "Business Document"
ORIGINAL = "Mail Original"
COMMUNICATION = "Communication"
MANUAL, AUTOMATIC, THREAD = "Manual", "Automatic", "Thread"
FOLLOW_SAVEPOINT = "docarchive_mail_follow"
FOLLOW_JOB = "docarchive.mail.archiving.follow_up_job"
DIRECTIONS = ("Incoming", "Outgoing")
SAVEPOINT = "docarchive_mail_archive"
SEND_NOTE = "Copies to other recipients differ only in tracking and unsubscribe links."
TITLE_LENGTH = 140
EMAIL_LENGTH = 140


def archive(
	original_name: str, own_company: str | None = None, check_permission: bool = False, raw=None
) -> str:
	return archive_with_outcome(original_name, own_company, check_permission, raw)[0]


def archive_with_outcome(original_name, own_company=None, check_permission=False, raw=None, reason=MANUAL):
	original = frappe.get_doc(ORIGINAL, original_name)
	if original.business_document:
		return original.business_document, "skipped"
	refused = refusal(original_name)
	if refused:
		frappe.throw(refused)
	row = accounts.account_row(original.email_account) or frappe._dict()
	own_company = settings.own_company_for(original.mail_at, own_company or row.own_company)
	name, outcome = _existing(original, own_company), "linked"
	if not name:
		name, outcome = _create(original, own_company, check_permission, raw, reason), "created"
	original.db_set("business_document", name, update_modified=False)
	return name, outcome


def refusal(original_name: str | None) -> str | None:
	if not original_name:
		return _("No original was captured for this e-mail.")
	status, attempts = frappe.db.get_value(ORIGINAL, original_name, ["status", "attempts"])
	if status == "Pending" and attempts:
		return _("Storing the original of this e-mail failed. It will be retried automatically.")
	if status == "Pending":
		return _("The original of this e-mail is still being stored. Please try again in a moment.")
	if status == "Failed":
		return _("Storing the original of this e-mail failed. See the Error Log.")
	return None


def auto_archive(original_name: str, raw=None) -> str | None:
	original = frappe.db.get_value(
		ORIGINAL, original_name, ["email_account", "automated", "internal", "communication"], as_dict=True
	)
	row = accounts.account_settings(original.email_account)
	if not row:
		return None
	if row.mode == "Automatic" and not original.automated and (row.archive_internal or not original.internal):
		reason = AUTOMATIC
	elif threads.follows(original_name, row):
		reason = THREAD
	else:
		return None
	return _archive_unattended(original_name, reason, original.communication, raw)


def _archive_unattended(original_name, reason, communication, raw=None):
	name, outcome = archive_with_outcome(original_name, raw=raw, reason=reason)
	if outcome != "skipped":
		pending = f"{reason.lower()}:{outcome}"
		frappe.db.set_value(ORIGINAL, original_name, "audit_pending", pending, update_modified=False)
		events.changed("archived", [original_name], [communication])
	return name


def schedule_follow_up(documents) -> None:
	for document in dict.fromkeys(documents):
		frappe.enqueue(
			FOLLOW_JOB,
			queue="default",
			job_id=f"docarchive-follow-thread-{document}",
			deduplicate=True,
			enqueue_after_commit=True,
			document=document,
		)


def follow_up_job(document: str) -> None:
	follow_up([document])
	frappe.db.commit()


def follow_up(documents) -> int:
	done = 0
	for document in dict.fromkeys(documents):
		messages = len(frappe.local.message_log)
		try:
			frappe.db.savepoint(FOLLOW_SAVEPOINT)
			for original, _row in threads.later_originals(document):
				if done >= threads.MAX_MAILS:
					return done
				communication = frappe.db.get_value(ORIGINAL, original, "communication")
				_archive_unattended(original, THREAD, communication)
				done += 1
		except Exception:
			frappe.db.rollback(save_point=FOLLOW_SAVEPOINT)
			del frappe.local.message_log[messages:]
			frappe.log_error(title="docarchive follow thread")
	return done


def log_automatic() -> int:
	rows = frappe.get_all(
		ORIGINAL,
		filters={"audit_pending": ("is", "set")},
		fields=["name", "business_document", "audit_pending"],
		order_by="creation asc",
	)
	by_how = {}
	for row in rows:
		how, _sep, outcome = row.audit_pending.rpartition(":")
		by_how.setdefault(how or "automatic", []).append((DOCUMENT, row.business_document, outcome))
	for how, documents in by_how.items():
		audit_log(audit.MAIL_ARCHIVED, documents, _counted({"how": how}, documents, {}))
	if rows:
		frappe.db.set_value(
			ORIGINAL,
			{"name": ("in", [row.name for row in rows])},
			"audit_pending",
			None,
			update_modified=False,
		)
	return len(rows)


def remove_document(name: str) -> None:
	if frappe.db.get_value(DOCUMENT, name, "archive_started_at"):
		frappe.throw(_("Archived documents cannot be discarded."))
	for original in frappe.get_all(ORIGINAL, filters={"business_document": name}, pluck="name"):
		frappe.delete_doc(ORIGINAL, original, ignore_permissions=True)
	frappe.delete_doc(DOCUMENT, name, ignore_permissions=True)


def mail_meta(doc) -> dict | None:
	original = _meta_original(doc)
	if not original:
		return None
	meta = {
		"message_id": original.message_id,
		"in_reply_to": original.in_reply_to,
		"references": (original.mail_references or "").split(),
		"from": original.sender,
		"to": original.to,
		"cc": original.cc,
		"subject": original.subject,
		"date_header": original.date_header,
		"account": original.email_account,
		"origin": original.origin,
	}
	if original.origin == "frappe-send":
		meta["note"] = SEND_NOTE
	if original.rebuilt_at:
		meta["rebuilt_at"] = get_datetime(original.rebuilt_at).isoformat() + "Z"
	return meta


def _meta_original(doc):
	originals = frappe.get_all(
		ORIGINAL,
		filters={"business_document": doc.name},
		fields=[
			"message_id",
			"in_reply_to",
			"mail_references",
			"sender",
			"to",
			"cc",
			"subject",
			"date_header",
			"email_account",
			"origin",
			"sha256",
			"rebuilt_at",
		],
		order_by="creation asc, name asc",
	)
	match = next((row for row in originals if row.sha256 == doc.get("content_sha256")), None)
	return match or (originals[0] if originals else None)


def assign(doc):
	if doc.get("source_doctype") != COMMUNICATION or not doc.get("source_name") or doc.get("counterparty"):
		return
	candidates = _candidates(doc)
	if len(candidates) == 1:
		doc.counterparty_type, doc.counterparty = candidates[0]


@frappe.whitelist()
def status(communication: str) -> dict:
	if communication not in access.readable([communication]):
		return {
			"original": False,
			"archived": False,
			"business_document": None,
			"automated": False,
			"internal": False,
			"archive_status": None,
			"retain_until": None,
			"reason": _("You cannot read this e-mail."),
		}
	original = (
		frappe.db.get_value(
			ORIGINAL,
			{"communication": communication},
			["name", "business_document", "automated", "internal"],
			as_dict=True,
		)
		or frappe._dict()
	)
	document = original.business_document
	return {
		"original": bool(original.name),
		"archived": bool(document),
		**document_state(document),
		"automated": bool(original.automated),
		"internal": bool(original.internal),
		"reason": refusal(original.name),
	}


def document_state(document) -> dict:
	if not (document and frappe.has_permission(DOCUMENT, "read", document)):
		return {"business_document": None, "archive_status": None, "retain_until": None}
	archive_status, retain_until = frappe.db.get_value(DOCUMENT, document, ["archive_status", "retain_until"])
	return {"business_document": document, "archive_status": archive_status, "retain_until": retain_until}


@frappe.whitelist()
def warnings(names) -> dict:
	allowed = access.readable(_names(names))
	names = [name for name in _names(names) if name in allowed]
	rows = frappe.get_all(
		ORIGINAL,
		filters={"communication": ("in", names or [""]), "business_document": ("is", "not set")},
		fields=["communication", "automated", "internal"],
	)
	order = {name: index for index, name in enumerate(names)}
	rows.sort(key=lambda row: order[row.communication])
	return {
		"automated": [row.communication for row in rows if row.automated],
		"internal": [row.communication for row in rows if row.internal and not row.automated],
	}


@frappe.whitelist(methods=["POST"])
def archive_communications(names) -> dict:
	names = _names(names)
	frappe.has_permission(DOCUMENT, "create", throw=True)
	allowed = access.readable(names)
	originals = dict(
		frappe.get_all(
			ORIGINAL,
			filters={"communication": ("in", list(allowed) or [""])},
			fields=["communication", "name"],
			as_list=True,
		)
	)
	refused = {name: _("You cannot read this e-mail.") for name in names if name not in allowed}
	return archive_each([(name, originals.get(name), name) for name in names if name in allowed], refused)


def archive_each(items, refused=None) -> dict:
	result = {"archived": {}, "refused": dict(refused or {})}
	archived, documents = [], []
	for key, original, communication in items:
		reason = refusal(original)
		if reason:
			result["refused"][key] = reason
			continue
		messages = len(frappe.local.message_log)
		try:
			frappe.db.savepoint(SAVEPOINT)
			name, outcome = archive_with_outcome(original, check_permission=True)
			result["archived"][key] = name
			documents.append((DOCUMENT, name, outcome))
			if outcome != "skipped":
				archived.append((original, communication))
		except (frappe.ValidationError, frappe.PermissionError) as error:
			frappe.db.rollback(save_point=SAVEPOINT)
			del frappe.local.message_log[messages:]
			result["refused"][key] = str(error)
	audit_log(audit.MAIL_ARCHIVED, documents, _counted({"how": "manual"}, documents, result["refused"]))
	events.changed("archived", [o for o, _c in archived], [c for _o, c in archived])
	schedule_follow_up([name for _doctype, name, outcome in documents if outcome != "skipped"])
	return result


def _counted(details, documents, refused):
	counts = {}
	for _doctype, _name, outcome in documents:
		counts[outcome] = counts.get(outcome, 0) + 1
	return {
		**details,
		"counts": dict(sorted(counts.items())),
		**({"refused": len(refused)} if refused else {}),
	}


def audit_log(action, documents, details=None):
	if audit.changes_anything(documents):
		audit.log(action, details=details, documents=documents)


@frappe.whitelist(methods=["POST"])
def archive_upload(
	file_url: str,
	direction: str,
	gap_message_id: str | None = None,
	own_company: str | None = None,
	counterparty_type: str | None = None,
	counterparty: str | None = None,
	source_doctype: str | None = None,
	source_name: str | None = None,
) -> str:
	from docarchive.documents import check_source
	from docarchive.mail import originals

	frappe.has_permission(DOCUMENT, "create", throw=True)
	if counterparty_type and counterparty:
		frappe.has_permission(counterparty_type, "read", counterparty, throw=True)
	check_source(source_doctype, source_name)
	if direction not in DIRECTIONS:
		frappe.throw(_("The direction must be Incoming or Outgoing."))
	file_name = _upload_name(file_url)
	raw = content.read_bytes(file_url)
	if raw is None:
		frappe.throw(_("File {0} does not exist.").format(frappe.utils.escape_html(file_url)))
	parsed = headers.parse(raw)
	if not (parsed.sender or parsed.message_id or parsed.date_header):
		frappe.throw(_("This file is not an e-mail."))
	if gap_message_id and parsed.message_id != gap_message_id:
		frappe.throw(
			_("This e-mail is not the missing one: its Message-ID is {0}.").format(
				frappe.utils.escape_html(parsed.message_id or "-")
			)
		)
	thread = _gap_thread(gap_message_id) if gap_message_id and not own_company else frappe._dict()
	company = settings.own_company_for(originals.mail_time(parsed), own_company or thread.own_company)
	name = _existing_document(parsed.message_id, content.sha256(raw), company)
	if not name:
		original = originals.create_upload(raw, parsed, direction)
		name = archive(original, company, check_permission=True)
		party = frappe._dict(counterparty_type=counterparty_type, counterparty=counterparty)
		_complete(name, party if counterparty_type and counterparty else thread, source_doctype, source_name)
		audit_log(
			audit.MAIL_UPLOADED,
			[(DOCUMENT, name, "created")],
			{"direction": direction, "gap": bool(gap_message_id)},
		)
	else:
		audit_log(
			audit.MAIL_UPLOADED,
			[(DOCUMENT, name, "linked")],
			{"direction": direction, "gap": bool(gap_message_id)},
		)
	frappe.delete_doc("File", file_name, ignore_permissions=True)
	return name


@frappe.whitelist(methods=["POST"])
def discard(names) -> dict:
	names = _names(names)
	for name in names:
		frappe.has_permission(DOCUMENT, "write", name, throw=True)
	result = {"discarded": [], "refused": {}}
	for name in names:
		reason = _discard_refusal(name)
		if reason:
			result["refused"][name] = reason
			continue
		remove_document(name)
		result["discarded"].append(name)
	audit_log(
		audit.MAIL_DISCARDED,
		[(DOCUMENT, name, "discarded") for name in result["discarded"]],
		{"refused": len(result["refused"])} if result["refused"] else {},
	)
	return result


def _names(names) -> list[str]:
	return list(frappe.parse_json(names) if isinstance(names, str) else names or [])


def _discard_refusal(name):
	source, system, started, hold = frappe.db.get_value(
		DOCUMENT, name, ["source_doctype", "source_system", "archive_started_at", "hold_on_archive"]
	)
	if started:
		return _("Archived documents cannot be discarded.")
	if hold:
		return _("Documents marked for legal hold cannot be discarded.")
	if source != COMMUNICATION and system != "email":
		return _("Only e-mail documents can be discarded.")
	return None


def _gap_thread(message_id):
	rows = frappe.get_list(
		DOCUMENT,
		or_filters=[["mail_references", "like", f"%{message_id}%"], ["mail_in_reply_to", "=", message_id]],
		fields=["own_company", "counterparty_type", "counterparty", "mail_references", "mail_in_reply_to"],
		order_by="correspondence_at asc",
		limit_page_length=50,
	)
	for row in rows:
		if message_id == row.mail_in_reply_to or message_id in (row.mail_references or "").split():
			return row
	return frappe._dict()


def _complete(name, party, source_doctype, source_name):
	if not (party.counterparty or source_doctype):
		return
	doc = frappe.get_doc(DOCUMENT, name)
	if party.counterparty:
		doc.counterparty_type, doc.counterparty = party.counterparty_type, party.counterparty
	if source_doctype:
		doc.source_doctype, doc.source_name = source_doctype, source_name
		doc.flags.archive_write = True
	doc.save(ignore_permissions=True)


def _upload_name(file_url):
	name = frappe.db.get_value(
		"File",
		{
			"file_url": file_url,
			"owner": frappe.session.user,
			"is_private": 1,
			"attached_to_doctype": ("is", "not set"),
		},
		"name",
	)
	if not name or not file_url.lower().endswith(".eml"):
		frappe.throw(_("Please upload the e-mail as a private .eml file."))
	return name


def _existing(original, own_company):
	return _existing_document(original.message_id, original.sha256, own_company)


def _existing_document(message_id, sha256, own_company):
	filters = {"own_company": own_company}
	if message_id:
		filters["mail_message_id"] = message_id
	else:
		filters.update(content_sha256=sha256, source_system="email")
	return frappe.db.get_value(DOCUMENT, filters)


def _create(original, own_company, check_permission=False, raw=None, reason=MANUAL):
	doc = frappe.get_doc(
		{
			"doctype": DOCUMENT,
			"document_type": INTERNAL_CORRESPONDENCE if original.internal else CORRESPONDENCE,
			"own_company": own_company,
			"document_date": getdate(original.mail_at),
			"title": (original.subject or _("(no subject)"))[:TITLE_LENGTH],
			"source_system": "email",
			"source_account": original.email_account,
			"source_doctype": COMMUNICATION if original.email_account else None,
			"source_name": original.communication,
			"source_party_email": _party_email(original),
			"correspondence_channel": "E-mail",
			"correspondence_direction": original.direction,
			"correspondence_at": original.mail_at,
			"thread_key": original.thread_key,
			"thread_root": original.thread_key,
			"filing_reason": reason,
			"mail_message_id": original.message_id,
			"mail_in_reply_to": original.in_reply_to,
			"mail_references": original.mail_references,
			**(_thread_party(original, own_company) if reason == THREAD else {}),
		}
	)
	if check_permission:
		frappe.has_permission(DOCUMENT, "create", doc=doc, throw=True)
	doc.flags.archive_write = True
	doc.insert(ignore_permissions=True)
	doc.file = _attach(original, doc.name, raw)
	doc.save(ignore_permissions=True)
	threads.schedule_join(doc.name)
	return doc.name


def _thread_party(original, own_company) -> dict:
	party = _party_email(original).lower()
	ids = [*(original.mail_references or "").split(), original.in_reply_to]
	documents = threads.thread_documents(own_company, original.thread_key, [i for i in ids if i])
	earliest = next(
		(doc for doc in documents if doc.counterparty and (doc.source_party_email or "").lower() == party),
		None,
	)
	if not (party and earliest):
		return {}
	return {"counterparty_type": earliest.counterparty_type, "counterparty": earliest.counterparty}


def _party_email(original):
	value = original.sender if original.direction == "Incoming" else original.to
	return (value or "").split(", ")[0][:EMAIL_LENGTH]


def _attach(original, document, raw=None):
	data = raw if raw is not None else content.read_bytes(original.file)
	if data is None:
		frappe.throw(_("The captured original of this e-mail is missing."))
	file_name = frappe.db.get_value(
		"File",
		{"file_url": original.file, "attached_to_doctype": ORIGINAL, "attached_to_name": original.name},
		"file_name",
	)
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name or f"{original.name}.eml",
			"content": data,
			"is_private": 1,
			"attached_to_doctype": DOCUMENT,
			"attached_to_name": document,
			"attached_to_field": "file",
		}
	).insert(ignore_permissions=True)
	return file_doc.file_url


def _candidates(doc):
	return parties({doc.source_name: doc.get("source_account")})[doc.source_name]


def parties(communications: dict) -> dict[str, list[tuple[str, str]]]:
	links = frappe.get_all(
		"Communication Link",
		filters={"parenttype": COMMUNICATION, "parent": ("in", list(communications) or [""])},
		fields=["parent", "link_doctype", "link_name"],
		order_by="idx",
	)
	contacts = list({link.link_name for link in links if link.link_doctype == "Contact"})
	contact_links = _by_parent(
		frappe.get_all(
			"Dynamic Link",
			filters={"parenttype": "Contact", "parentfield": "links", "parent": ("in", contacts or [""])},
			fields=["parent", "link_doctype", "link_name"],
			order_by="idx",
		)
	)
	emails = _by_parent(
		frappe.get_all(
			"Contact Email",
			filters={"parenttype": "Contact", "parent": ("in", contacts or [""])},
			fields=["parent", "email_id"],
		)
	)
	rows = {}
	found = {name: [] for name in communications}
	for link in links:
		if link.link_doctype != "Contact":
			found[link.parent].append((link.link_doctype, link.link_name))
			continue
		account = communications[link.parent]
		if account not in rows:
			row = accounts.account_row(account) or frappe._dict(email_account=account)
			rows[account] = (row, accounts.internal_domains(row))
		row, domains = rows[account]
		if _internal_contact(emails.get(link.link_name, []), domains):
			continue
		links_of_contact = contact_links.get(link.link_name)
		if links_of_contact:
			found[link.parent].extend((item.link_doctype, item.link_name) for item in links_of_contact)
		elif row.contacts_as_counterparty:
			found[link.parent].append(("Contact", link.link_name))
	allowed = counterparty.doctypes()
	return {
		name: [party for party in dict.fromkeys(items) if not allowed or party[0] in allowed]
		for name, items in found.items()
	}


def _by_parent(rows):
	grouped = {}
	for row in rows:
		grouped.setdefault(row.parent, []).append(row)
	return grouped


def _internal_contact(emails, domains):
	return bool(emails) and all(row.email_id.rpartition("@")[2].lower() in domains for row in emails)
