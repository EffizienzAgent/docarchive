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
		docarchive.set_archive_headline(frm, status);
		return docarchive_thread_pending(frm);
	}
	if (!frappe.model.can_create("Business Document")) return;
	const $button = frm.add_custom_button(__("Archive"), () =>
		docarchive.archive_communications([frm.doc.name], () => frm.reload_doc()),
	);
	if (status.reason) $button.prop("disabled", true).attr("title", status.reason);
}

function docarchive_thread_pending(frm) {
	if (!frappe.model.can_create("Business Document")) return;
	frappe
		.xcall("docarchive.mail.thread_pending", { communication: frm.doc.name })
		.then(({ count, items }) => {
			if (!count) return;
			frm.add_custom_button(__("Archive {0} earlier e-mails of this thread", [count]), () =>
				docarchive.archive_communications(
					items.map((item) => item.communication),
					() => frm.reload_doc(),
				),
			);
		});
}
