frappe.provide("docarchive");

const STATUS_COLORS = { Archived: "green", Failed: "red" };
const esc = (value) => frappe.utils.escape_html(value);
const METHOD = "docarchive.documents";

function status_pill(status) {
	const color = STATUS_COLORS[status];
	return color ? `<span class="indicator-pill ${color}">${esc(__(status))}</span>` : "";
}

function document_row(doc) {
	const href = frappe.utils.get_form_link("Business Document", doc.name);
	return `<tr>
		<td><a href="${esc(href)}">${esc(doc.title || doc.name)}</a></td>
		<td>${esc(__(doc.document_type))}</td>
		<td>${esc(doc.document_number || "")}</td>
		<td>${esc(frappe.datetime.str_to_user(doc.document_date))}</td>
		<td>${status_pill(doc.archive_status)}</td>
	</tr>`;
}

function documents_table(rows) {
	if (!rows.length) return `<p class="text-muted">${esc(__("No documents yet"))}</p>`;
	const headers = [
		"Title",
		"Business Document Type",
		"Document Number",
		"Document Date",
		"Archive Status",
	];
	return `<table class="table table-bordered table-condensed">
		<thead><tr>${headers.map((h) => `<th>${esc(__(h))}</th>`).join("")}</tr></thead>
		<tbody>${rows.map(document_row).join("")}</tbody>
	</table>`;
}

function ask_type_and_date(party, file_urls, on_saved) {
	let saved = false;
	frappe.xcall(`${METHOD}.upload_defaults`).then((defaults) => {
		const dialog = new frappe.ui.Dialog({
			title: __("Upload documents"),
			fields: [
				{
					fieldname: "document_type",
					fieldtype: "Link",
					options: "Business Document Type",
					label: __("Business Document Type"),
					reqd: 1,
					default: defaults.document_type,
				},
				{
					fieldname: "document_date",
					fieldtype: "Date",
					label: __("Document Date"),
					reqd: 1,
					default: defaults.document_date,
				},
				{
					fieldname: "combine",
					fieldtype: "Check",
					label: __("One document with attachments"),
					description: __("The first file is the main file, the others become its attachments."),
					hidden: file_urls.length < 2,
				},
			],
			primary_action_label: __("Save"),
			primary_action(values) {
				frappe
					.call({
						method: `${METHOD}.create_documents`,
						args: { ...party, file_urls, ...values },
						freeze: true,
					})
					.then(() => {
						saved = true;
						dialog.hide();
						on_saved && on_saved();
					});
			},
		});
		dialog.onhide = () => saved || frappe.xcall(`${METHOD}.discard_uploads`, { file_urls });
		dialog.show();
	});
}

docarchive.upload_documents = function ({ counterparty_type, counterparty, on_saved }) {
	const file_urls = [];
	const uploader = new frappe.ui.FileUploader({
		allow_multiple: true,
		disable_file_browser: true,
		make_attachments_public: false,
		allow_toggle_private: false,
		on_success: (file) => file_urls.push(file.file_url),
	});
	uploader.dialog.onhide = () => {
		if (file_urls.length) {
			ask_type_and_date({ counterparty_type, counterparty }, file_urls, on_saved);
		}
	};
};

docarchive.render_documents = function ($wrapper, { counterparty_type, counterparty }) {
	$wrapper.empty();
	if (!frappe.model.can_read("Business Document")) return Promise.resolve([]);
	const party = { counterparty_type, counterparty };
	return frappe.xcall(`${METHOD}.counterparty_documents`, party).then((rows) => {
		$wrapper.html(documents_table(rows));
		$(`<a href="#">${esc(__("Show all documents"))}</a>`)
			.appendTo($wrapper)
			.on("click", (event) => {
				event.preventDefault();
				frappe.set_route("List", "Business Document", party);
			});
		if (frappe.model.can_create("Business Document")) {
			$(
				`<button class="btn btn-default btn-xs" style="margin-left: 12px;">${esc(__("Upload"))}</button>`,
			)
				.appendTo($wrapper)
				.on("click", () =>
					docarchive.upload_documents({
						...party,
						on_saved: () => docarchive.render_documents($wrapper, party),
					}),
				);
		}
		return rows;
	});
};

docarchive.documents_panel = function (frm, fieldname, section) {
	const can_read = frappe.model.can_read("Business Document") && !frm.is_new();
	if (section) frm.toggle_display(section, can_read);
	if (!can_read) return;
	return docarchive.render_documents(frm.get_field(fieldname).$wrapper, {
		counterparty_type: frm.doctype,
		counterparty: frm.doc.name,
	});
};
