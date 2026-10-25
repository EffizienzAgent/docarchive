import csv
import email
import email.policy
import html
import io
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime
from frappe.utils.html_utils import sanitize_html

from docarchive import retention, run

VIEWER = Path(__file__).with_name("export_viewer.html")
DATA_MARKER = "/*DOCARCHIVE_DATA*/"
STORED = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".zip", ".gz", ".docx", ".xlsx"}
SIDECAR = re.compile(r"\.meta\.(\d+)\.json$")
MAIL_PREVIEW_LIMIT = 50 * 1024 * 1024
MAIL_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
CSV_FIELDS = (
	"id",
	"title",
	"type",
	"date",
	"number",
	"own_company",
	"counterparty_type",
	"counterparty",
	"counterparty_name",
	"lock_mode",
	"retain_until",
	"legal_hold",
	"thread_key",
	"file_role",
	"file_name",
	"key",
	"version_id",
	"sha256",
	"size",
	"verified",
)


def scope_of(export, number, count) -> dict:
	return {
		"export": export.name,
		"part": number,
		"parts": count,
		"target": export.target_name,
		"exported_at": run.utc_iso(now_datetime()),
		"exported_by": export.requested_by,
		"filters": {
			field: str(export.get(field)) if export.get(field) else None
			for field in (
				"own_company",
				"document_type",
				"date_from",
				"date_to",
				"counterparty_type",
				"counterparty",
			)
		},
	}


def archive_path(key) -> str:
	parts = [part for part in PurePosixPath(key).parts if part not in ("", ".", "..", "/")]
	return "documents/" + "/".join(parts)


def preview_path(key) -> str:
	return "previews/" + archive_path(key).removeprefix("documents/") + ".html"


def iso(value):
	return retention.from_db(value).isoformat().replace("+00:00", "Z") if value else None


class Package:
	def __init__(self, path, scope):
		self.zip = zipfile.ZipFile(path, "w", allowZip64=True)
		self.scope = scope
		self.documents = []
		self.mismatches = []
		self.pending = {}

	def add_object(self, doc, item, result):
		entry = self.pending.setdefault(doc.name, {"objects": {}, "metas": {}, "previews": {}})
		key = item["key"]
		expected = item["sha256"] or result.get("stored_sha256")
		error = result.get("error")
		actual = result.get("sha256")
		if not error and (actual != expected or result.get("stored_sha256") not in (None, actual)):
			error = _("Checksum mismatch")
		if error is not None:
			self.mismatches.append(
				{"document": doc.name, "key": key, "expected": expected, "actual": actual, "error": error}
			)
		path = None
		if result.get("path"):
			path = archive_path(key)
			suffix = PurePosixPath(key).suffix.lower()
			compress = zipfile.ZIP_STORED if suffix in STORED else zipfile.ZIP_DEFLATED
			self.zip.write(result["path"], path, compress_type=compress)
			if key == doc.meta_key or SIDECAR.search(key):
				entry["metas"][key] = json.loads(Path(result["path"]).read_bytes())
			elif suffix == ".eml" and result["size"] <= MAIL_PREVIEW_LIMIT:
				self.add_mail_preview(entry, key, result["path"])
		entry["objects"][key] = {
			"key": key,
			"path": path,
			"version_id": item["version_id"],
			"sha256": actual,
			"size": result.get("size"),
			"verified": error is None,
		}

	def add_mail_preview(self, entry, key, source):
		try:
			with open(source, "rb") as f:
				message = email.message_from_binary_file(f, policy=email.policy.default)
			body = mail_preview(message)
		except Exception:
			return
		self.zip.writestr(preview_path(key), body, compress_type=zipfile.ZIP_DEFLATED)
		entry["previews"][key] = preview_path(key)

	def finish_document(self, doc, copy):
		entry = self.pending.pop(doc.name)
		self.documents.append(document_row(doc, copy, entry))

	def close(self):
		data = {"scope": self.scope, "documents": self.documents, "mismatches": self.mismatches}
		manifest = json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True)
		self.zip.writestr("manifest.json", manifest, compress_type=zipfile.ZIP_DEFLATED)
		self.zip.writestr("manifest.csv", manifest_csv(self.documents), compress_type=zipfile.ZIP_DEFLATED)
		self.zip.writestr("index.html", viewer(data), compress_type=zipfile.ZIP_DEFLATED)
		self.zip.close()


def latest_meta(entry, doc):
	metas = entry["metas"]
	sidecars = sorted(
		(int(SIDECAR.search(key).group(1)), key)
		for key in metas
		if key != doc.meta_key and SIDECAR.search(key)
	)
	if sidecars:
		return metas[sidecars[-1][1]]
	return metas.get(doc.meta_key)


def fallback_meta(doc) -> dict:
	return json.loads(doc.archive_meta) if doc.archive_meta else {}


def document_row(doc, copy, entry) -> dict:
	meta = latest_meta(entry, doc) or fallback_meta(doc)
	document = meta.get("document") or {}
	objects = entry["objects"]
	files = []
	for described in meta.get("files") or [meta.get("file") or {"key": doc.object_key}]:
		found = objects.get(described.get("key")) or {"key": described.get("key"), "verified": False}
		files.append(
			{
				**found,
				"name": described.get("name"),
				"role": described.get("role"),
				"role_label": _(described.get("role")) if described.get("role") else None,
				"title": described.get("title"),
				"preview": entry["previews"].get(described.get("key")),
			}
		)
	described_keys = {file["key"] for file in files}
	metadata = sorted(
		(value for key, value in objects.items() if key not in described_keys),
		key=lambda value: value["key"],
	)
	history = list(meta.get("history") or [])
	if meta.get("extension"):
		history.append(meta["extension"])
	return {
		"id": doc.name,
		"title": document.get("title") or doc.title,
		"type": document.get("type") or doc.document_type,
		"type_label": _(document.get("type") or doc.document_type),
		"date": document.get("date") or str(doc.document_date),
		"number": document.get("number"),
		"contract_end_date": document.get("contract_end_date"),
		"own_company": document.get("own_company") or doc.own_company,
		"counterparty": meta.get("counterparty"),
		"source": meta.get("source"),
		"retention": meta.get("retention"),
		"history": history,
		"lock_mode": copy.lock_mode,
		"retain_until": iso(copy.retain_until),
		"legal_hold": bool(doc.get("legal_hold")),
		**thread_fields(doc, meta),
		"files": files,
		"metadata": metadata,
		"verified": all(value["verified"] for value in objects.values()) and len(objects) > 0,
	}


def thread_fields(doc, meta) -> dict:
	correspondence = meta.get("correspondence") or {}
	mail = correspondence.get("mail") or {}
	at = doc.get("correspondence_at")
	return {
		"thread_key": correspondence.get("thread_key") or doc.get("thread_key"),
		"message_id": mail.get("message_id") or doc.get("mail_message_id"),
		"in_reply_to": mail.get("in_reply_to") or doc.get("mail_in_reply_to"),
		"references": mail.get("references") or (doc.get("mail_references") or "").split(),
		"correspondence_at": correspondence.get("at") or (run.utc_iso(get_datetime(at)) if at else None),
	}


def manifest_csv(documents) -> str:
	out = io.StringIO()
	writer = csv.DictWriter(out, fieldnames=CSV_FIELDS)
	writer.writeheader()
	for row in documents:
		party = row["counterparty"] or {}
		for file in row["files"]:
			writer.writerow(
				{
					**{field: row.get(field) for field in CSV_FIELDS if field in row},
					"counterparty_type": party.get("doctype"),
					"counterparty": party.get("id"),
					"counterparty_name": party.get("name"),
					"file_role": file.get("role"),
					"file_name": file.get("name"),
					"key": file.get("key"),
					"version_id": file.get("version_id"),
					"sha256": file.get("sha256"),
					"size": file.get("size"),
					"verified": file.get("verified"),
				}
			)
	return out.getvalue()


def labels() -> dict:
	return {
		"title": _("Archive Export"),
		"search": _("Search title, number or counterparty"),
		"all_types": _("All types"),
		"all_counterparties": _("All counterparties"),
		"all_companies": _("All own companies"),
		"all_years": _("All years"),
		"legal_hold": _("Legal Hold"),
		"only_held": _("Only legal hold"),
		"only_problems": _("Only problems"),
		"documents": _("Documents"),
		"of": _("of"),
		"more": _("Show more"),
		"none": _("No documents match the filters."),
		"previous": _("Previous"),
		"next": _("Next"),
		"open_original": _("Open original"),
		"download_original": _("Download original"),
		"rendered_preview": _("Rendered preview for reading only. The archived original is the file above."),
		"no_preview": _("No preview for this file type. Open or download the original."),
		"date": _("Document Date"),
		"number": _("Document Number"),
		"type": _("Business Document Type"),
		"counterparty": _("Counterparty"),
		"own_company": _("Own Company"),
		"retain_until": _("Retain Until (UTC)"),
		"lock_mode": _("Lock Mode"),
		"retention_trail": _("Retention extensions"),
		"metadata": _("Metadata files"),
		"files": _("Files"),
		"thread": _("Conversation"),
		"verified": _("Checksum verified"),
		"problem": _("Checksum mismatch or missing object"),
		"mismatches": _("{0} objects could not be verified. See manifest.json."),
		"part": _("Part {0} of {1}"),
		"exported_at": _("Exported at"),
		"target": _("Storage Target"),
		"document_type": _("Business Document Type"),
		"date_from": _("From Date"),
		"date_to": _("To Date"),
		"counterparty_type": _("Counterparty Type"),
	}


def viewer(data) -> str:
	payload = json.dumps({**data, "labels": labels()}, ensure_ascii=False).replace("</", "<\\/")
	return VIEWER.read_text(encoding="utf-8").replace(DATA_MARKER, payload)


def mail_preview(message) -> str:
	rows = "".join(
		f"<tr><th>{html.escape(label)}</th><td>{html.escape(str(message.get(header) or ''))}</td></tr>"
		for header, label in (
			("From", _("From")),
			("To", _("To")),
			("Cc", _("Cc")),
			("Date", _("Date")),
			("Subject", _("Subject")),
		)
		if message.get(header)
	)
	part = message.get_body(preferencelist=("html", "plain"))
	if part is None:
		body = ""
	elif part.get_content_type() == "text/html":
		body = sanitize_html(
			part.get_content(), always_sanitize=True, disallowed_tags={"html", "head", "meta", "link", "body"}
		)
	else:
		body = f"<pre>{html.escape(part.get_content())}</pre>"
	names = [p.get_filename() for p in message.iter_attachments() if p.get_filename()]
	label = html.escape(_("Attachments"))
	attachments = f"<p class='attachments'>{label}: {html.escape(', '.join(names))}</p>" if names else ""
	return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{MAIL_CSP}">
<style>
body{{font:14px/1.5 system-ui,sans-serif;margin:16px;color:#1f2328;background:#fff}}
table{{border-collapse:collapse;margin-bottom:12px}}
th{{text-align:left;padding:2px 12px 2px 0;color:#59636e;font-weight:500;vertical-align:top}}
pre{{white-space:pre-wrap;font:inherit}}
.attachments{{color:#59636e}}
hr{{border:0;border-top:1px solid #d1d9e0}}
</style></head><body>
<table>{rows}</table>{attachments}<hr>
{body}
</body></html>"""
