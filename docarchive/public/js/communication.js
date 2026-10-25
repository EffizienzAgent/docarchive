frappe.ui.form.on("Communication", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.communication_medium !== "Email") return;
		frappe
			.xcall("docarchive.mail.archiving.status", { communication: frm.doc.name })
			.then((status) => docarchive_communication(frm, status));
	},
});

function docarchive_communication(frm, status) {
	if (status.archived) return docarchive.set_archive_headline(frm, status);
	if (!frappe.model.can_create("Business Document")) return;
	const $button = frm.add_custom_button(__("Archive"), () =>
		docarchive.archive_communications([frm.doc.name], () => frm.reload_doc()),
	);
	if (status.reason) $button.prop("disabled", true).attr("title", status.reason);
}
