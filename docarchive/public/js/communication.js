frappe.ui.form.on("Communication", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.communication_medium !== "Email") return;
		frappe
			.xcall("docarchive.mail.archiving.status", { communication: frm.doc.name })
			.then((status) => docarchive_communication(frm, status));
	},
});

function docarchive_communication(frm, status) {
	if (status.archived) {
		if (!status.business_document) return frm.dashboard.set_headline(__("This e-mail is archived."));
		const href = frappe.utils.get_form_link("Business Document", status.business_document);
		const link = `<a href="${frappe.utils.escape_html(href)}">${frappe.utils.escape_html(status.business_document)}</a>`;
		frm.dashboard.set_headline(__("Archived as {0}", [link]));
		return;
	}
	if (!frappe.model.can_create("Business Document")) return;
	const $button = frm.add_custom_button(__("Archive"), () =>
		docarchive.archive_communications([frm.doc.name], () => frm.reload_doc()),
	);
	if (status.reason) $button.prop("disabled", true).attr("title", status.reason);
}
