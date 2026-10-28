const COMMUNICATION_THREAD_NAMESPACE = "click.docarchive-thread";

frappe.ui.form.on("Communication", {
	refresh(frm) {
		const token = (frm.docarchive_bar_token = (frm.docarchive_bar_token || 0) + 1);
		const name = frm.doc.name;
		const current = () => frm.docarchive_bar_token === token && frm.doc.name === name;
		frm.dashboard.clear_headline();
		if (frm.is_new() || frm.doc.communication_medium !== "Email") return;
		Promise.all([docarchive.archive_states([name]), docarchive.archive_rule()])
			.then(([states, rule]) => current() && docarchive_communication(frm, states[name], rule, current))
			.catch(() => {});
	},
});

function docarchive_communication(frm, status, rule, current) {
	const bar = status && docarchive.mail_core.state_bar(status, docarchive_bar_helpers(rule));
	if (!bar) return;
	const quiet = bar.color === "gray";
	const show = (html) => {
		if (!current()) return;
		frm.dashboard.clear_headline();
		frm.dashboard.set_headline(html, quiet ? "white" : bar.color, true);
		frm.layout.message.find(".form-message").toggleClass("docarchive-quiet", quiet);
		docarchive.bind_state_actions(frm.layout.message, frm.doc.name, status, () => frm.reload_doc());
	};
	show(bar.html);
	if (["filed", "archived"].includes(status.state)) docarchive_thread_pending(frm, bar, show, current);
}

const user_date = (value) => {
	const text = String(value);
	const date = frappe.datetime.str_to_user(text.slice(0, 10));
	return text.length > 10 ? `${date} ${text.slice(11, 16)}` : date;
};

function docarchive_bar_helpers(rule) {
	const esc = frappe.utils.escape_html;
	return {
		t: __,
		rule,
		date: user_date,
		link: (name) => `<a href="${esc(frappe.utils.get_form_link("Business Document", name))}">${esc(name)}</a>`,
	};
}

function docarchive_thread_pending(frm, bar, show, current) {
	if (!frappe.model.can_create("Business Document")) return;
	frappe
		.xcall("docarchive.mail.thread_pending", { communication: frm.doc.name })
		.then(({ count, items }) => {
			if (!count || !current()) return;
			const label = frappe.utils.escape_html(__("Archive {0} more e-mails of this thread", [count]));
			show(`${bar.html} <button class="btn btn-xs btn-default" data-docarchive-action="thread">${label}</button>`);
			frm.layout.message
				.off(COMMUNICATION_THREAD_NAMESPACE)
				.on(COMMUNICATION_THREAD_NAMESPACE, '[data-docarchive-action="thread"]', (event) => {
					event.preventDefault();
					docarchive.archive_communications(
						items.map((item) => item.communication),
						() => frm.reload_doc(),
					);
				});
		})
		.catch(() => {});
}
