DOCUMENT_NAME = {"type": "string", "description": "Business Document ID."}


def string(description, enum=None):
	return {"type": "string", "description": description, **({"enum": list(enum)} if enum else {})}


def tool(name, title, description, properties=None, required=(), read_only=True):
	return {
		"name": name,
		"title": title,
		"description": description,
		"method": f"docarchive.mcp.{name}",
		"available": "docarchive.mcp.available",
		"read_only": read_only,
		"input_schema": {"type": "object", "properties": properties or {}, "required": list(required)},
	}


TOOLS = [
	tool(
		"list_open_documents",
		"List documents to assign",
		"Business documents that cannot be archived yet, newest document date first: the fields they "
		"miss (file, date, own company, counterparty, number, contract end, channel, direction, or "
		"'Confirmation' for e-invoice suggestions to confirm) plus documents whose archiving failed. "
		"Returns {total, documents: [{name, title, document_type, document_date, document_number, "
		"own_company, counterparty_type, counterparty, archive_status, archive_error, missing, url}]}. "
		"Read-only.",
		{"limit": {"type": "integer", "description": "Maximum number of documents (default 50)."}},
	),
	tool(
		"get_document",
		"Get business document",
		"One business document with everything needed to assign it: current fields, missing fields, "
		"file and attachment names, a date suggested by the file name, the source (system, sender name "
		"and email), correspondence data, extracted e-invoice data, archive state and, with "
		"include_text (default true), the text of the main file (PDF text layer, e-mail body or plain "
		"text; scans without a text layer have none). Read-only.",
		{
			"name": DOCUMENT_NAME,
			"include_text": {"type": "boolean", "description": "Include the file text (default true)."},
		},
		required=("name",),
	),
	tool(
		"document_options",
		"Document assignment options",
		"What assign_document accepts: the document types with their rules (archived or not, retention, "
		"which fields they require), the own companies and the doctypes allowed as counterparty. "
		"Read-only.",
	),
	tool(
		"assign_document",
		"Assign business document",
		"Fills or corrects the fields of a business document that is not archived yet. Only the arguments "
		"you pass change; an empty string clears a field. Saving also confirms e-invoice suggestions once "
		"own company and type are set, exactly like saving in the desk. Never archives: complete documents "
		"are archived (write-once, not reversible) by the nightly run once they were unchanged for the "
		"configured minimum age, so check the result before then. Returns the document like get_document "
		"without text, including what is still missing and when it will be archived.",
		{
			"name": DOCUMENT_NAME,
			"title": string("Title."),
			"document_type": string("Business Document Type name (see document_options)."),
			"document_date": string("Document date (YYYY-MM-DD)."),
			"document_number": string("Document number, e.g. the invoice number."),
			"own_company": string("Own Company name (see document_options)."),
			"counterparty_type": string("Doctype of the counterparty, e.g. 'Organization'."),
			"counterparty": string("Counterparty ID, or its exact title within counterparty_type."),
			"contract_end_date": string("Contract end date (YYYY-MM-DD), for contract types."),
			"correspondence_channel": string(
				"Channel, for correspondence types.", ("", "E-mail", "Letter", "Fax", "Other")
			),
			"correspondence_direction": string(
				"Direction, for correspondence types.", ("", "Incoming", "Outgoing")
			),
			"modified": string(
				"The `modified` value from get_document; the call fails if someone changed the document since."
			),
		},
		required=("name",),
		read_only=False,
	),
]
