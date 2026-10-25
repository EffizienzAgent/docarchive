# E-invoices

docarchive recognizes e-invoices among a document's files (main file first, then the attachments):

- XRechnung / EN 16931 XML in **CII** (UN/CEFACT Cross Industry Invoice) or **UBL** (Invoice and CreditNote) syntax,
- **ZUGFeRD / Factur-X** PDFs: the embedded XML (`factur-x.xml`, `zugferd-invoice.xml`, `xrechnung.xml` or another embedded `.xml`) is read from the PDF's embedded files. The PDF is not changed.

**What is archived**: the files as they are. A ZUGFeRD PDF is archived whole, with its embedded XML, exactly as before. With **Archive Settings → Store E-Invoice XML as Attachment** (off by default), the embedded XML is also stored as an attachment with the role *Structured Invoice (XML)*, so it is archived as its own object next to the PDF.

**What is extracted** (metadata only, no line items): invoice number, issue date, due date, type code and whether it is an invoice or a credit note; seller and buyer name, VAT IDs, tax numbers and address; currency; net, tax and gross totals and the amount due; the buyer reference (Leitweg-ID); the syntax and profile. The data is stored in `einvoice_data`, shown in the **E-Invoice** section of the form and written to the `.meta.json` under `einvoice`. It is frozen once archiving starts. The `.meta.json` is a WORM object, so the extracted data (including the parties' names, addresses and IDs) becomes permanent there for the whole retention period.

**How it is used**, only while archiving has not started and only when the files change:

- Empty fields are prefilled: document date, number, title, own company and type. Add your VAT IDs to **Own Company → VAT IDs**: if the seller's VAT ID matches, the document is an **Outgoing Invoice** of that company, if the buyer's matches, an **Incoming Invoice** (by the seeded type slugs `outgoing-invoice` and `incoming-invoice`). Tax numbers are not used for this, and when own VAT IDs appear on both sides nothing is filled.
- Own company and type taken from the XML are only suggestions, since anyone can send a crafted XML. The document gets **E-Invoice Needs Confirmation** and lists "Confirmation" as missing, so it is never archived automatically. A person confirms by saving it in the form or the triage with both fields set. Type and own company sent by the upload API are trusted as before.
- Values a user or the API set are never overwritten. When they differ from the XML, the form shows it, e.g. "Document Date in the XML differs: 10.03.2026". The upload API only needs `document_type` and `document_date` when the e-invoice does not provide them.
- The counterparty hint goes to your `docarchive_assign` hook: call `docarchive.einvoice.counterparty_hint(doc)`. It returns the other party (the buyer of an outgoing, the seller of an incoming invoice) as `{"vat_id", "tax_number", "name", "address"}`. Match by VAT ID first, then by tax number, then by name. Documents with e-invoice data but no counterparty are run through the hooks again before each nightly run.

**Existing documents**: `bench --site <site> docarchive-extract [--dry-run]` reads the e-invoice data of documents whose archiving has not started and that have none yet. It fills only empty fields (and the title while it is still the automatic one) and records conflicts. A document without an e-invoice is marked as checked without changing its `modified` date, so it is not read again. `--dry-run` lists what it would fill and writes nothing. It exits with status 1 if a document fails.

**Your own parser**: register callables under `docarchive_einvoice_extractors` in `hooks.py`. Each gets `(data: bytes, file_name: str)` and returns a dict in the format above (see `docarchive/einvoice_parser.py`) or `None`. The result is validated: dates must be ISO dates, texts are cut to 140 characters, unknown keys are dropped, and an optional `document_type` must be `outgoing-invoice` or `incoming-invoice`. The last hook is tried first, the built-in parser last. An ERPNext site can plug in a dedicated e-invoice app this way. To store the XML of a PDF as an attachment, return it as `xml` together with `embedded_file`.

**Safety**: XML is parsed with `defusedxml` (no DTDs, no entities, no network) with explicit namespaces. XML larger than 10 MB, also inside a PDF, is skipped, and at most 5 embedded XML files of a PDF are tried, known file names first. Files that cannot be read are treated as plain documents; saving never fails because of them.

**GoBD note**: for a hybrid invoice such as ZUGFeRD, the structured XML is the authoritative original, not the PDF view. Archive the file you received unchanged; docarchive does that. The extracted metadata is a convenience for filing and search. docarchive does not validate e-invoices against EN 16931 or XRechnung, does not check that the PDF view matches the XML, and makes no compliance promise (see [No compliance promise](../README.md#no-compliance-promise)).
