frappe.ui.form.on("Mail Original", {
	refresh(frm) {
		if (frm.is_new()) return;
		frappe
			.xcall("docarchive.mail.inbox.original_status", { name: frm.doc.name })
			.then((status) => mail_original_actions(frm, status));
	},
});

function mail_original_actions(frm, status) {
	const actions = docarchive.mail_core.original_actions(
		status,
		frappe.model.can_create("Business Document"),
	);
	const reload = () => frm.reload_doc();
	if (actions.headline) docarchive.set_archive_headline(frm, status);
	if (actions.show) {
		frm.add_custom_button(__("Show e-mail"), () =>
			docarchive.show_mail(frm.doc.name, { doctype: "Mail Original" }),
		);
	}
	if (actions.archive) {
		const $button = frm.add_custom_button(__("Archive"), () =>
			docarchive.archive_originals([frm.doc.name], reload),
		);
		if (actions.archive_reason) $button.prop("disabled", true).attr("title", actions.archive_reason);
	}
	const toggle = (method) =>
		frappe.xcall(`docarchive.mail.inbox.${method}`, { names: [frm.doc.name] }).then(reload);
	if (actions.ignore) frm.add_custom_button(__("Ignore"), () => toggle("ignore"));
	if (actions.unignore) frm.add_custom_button(__("Unignore"), () => toggle("unignore"));
}
