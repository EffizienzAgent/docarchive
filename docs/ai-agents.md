# AI agents (MCP)

docarchive ships tools for the [mcpserver](https://github.com/EffizienzAgent/mcpserver) app, so an AI agent such as Claude can work through the documents that are not complete yet. docarchive does not depend on mcpserver: the tools are plain definitions in the `mcp_tools` hook (`docarchive/mcp_tools.py`). Install mcpserver on the same site and they appear in its MCP endpoint.

Every call runs as the signed-in user with that user's permissions. The tools are only offered to users who may read Business Documents.

| Tool | Purpose |
| --- | --- |
| `list_open_documents(limit?)` | Documents that cannot be archived yet and the fields they miss, plus failed ones; the same list as [triage](host-apps.md#triage). |
| `get_document(name, include_text?)` | Fields, missing fields, file names, source and sender, e-invoice data and the text of the main file (PDF text layer, e-mail body or plain text). |
| `document_options()` | Document types with their rules, own companies and counterparty doctypes. |
| `assign_document(name, …, modified?)` | Sets or corrects title, type, date, number, own company, counterparty, contract end, channel and direction. Only passed fields change; `""` clears a field. |

## No archiving by agents

The tools never archive. A document that `assign_document` completes is archived by the [nightly run](archiving.md#archive-run) once it was unchanged for the minimum age from the Archive Settings; the result of `assign_document` says when (`archives_after`). Until then a person can check and correct it in the desk. [Archive now](archiving.md#archive-now) stays a desk action, because archiving cannot be undone.

Saving through `assign_document` confirms e-invoice suggestions once own company and type are set, like saving the form in the desk. Fields are locked once archiving has started, so documents in progress or archived cannot be changed.

Scans without a text layer have no text; `text_note` explains why. There is no OCR.
