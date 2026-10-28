const STATES = "docarchive.mail.archiving.statuses";
const BATCH_DELAY = 30;

let pending = null;
let rule = null;

frappe.provide("docarchive");

docarchive.archive_states = function (names) {
	if (!names.length) return Promise.resolve({});
	if (!pending) {
		pending = { names: new Set(), promise: null };
		const batch = pending;
		batch.promise = new Promise((resolve, reject) => {
			setTimeout(() => {
				pending = null;
				frappe.xcall(STATES, { names: [...batch.names] }).then(resolve, reject);
			}, BATCH_DELAY);
		});
	}
	names.forEach((name) => pending.names.add(name));
	return pending.promise.then((states) =>
		Object.fromEntries(names.filter((name) => name in states).map((name) => [name, states[name]])),
	);
};

docarchive.archive_rule = function () {
	rule ||= frappe.xcall("docarchive.mail.suggestion.rule");
	return rule;
};

docarchive.archive_one = function (communication, on_done) {
	return docarchive.archive_communications([communication], on_done, { discard_link: true });
};

docarchive.ignore_one = function (mail_original, on_done) {
	return frappe
		.xcall("docarchive.mail.inbox.ignore", { names: [mail_original] })
		.then((result) => on_done && on_done(result));
};

docarchive.unignore_one = function (mail_original, on_done) {
	return frappe
		.xcall("docarchive.mail.inbox.unignore", { names: [mail_original] })
		.then((result) => on_done && on_done(result));
};

docarchive.bind_state_actions = function ($root, communication, status, on_done) {
	const actions = {
		archive: () => docarchive.archive_one(communication, on_done),
		discard: () => docarchive.discard_documents([status.business_document], on_done),
		ignore: () => docarchive.ignore_one(status.mail_original, on_done),
		unignore: () => docarchive.unignore_one(status.mail_original, on_done),
	};
	$root
		.off("click.docarchive-state")
		.on("click.docarchive-state", "[data-docarchive-action]", (event) => {
			event.preventDefault();
			actions[event.currentTarget.dataset.docarchiveAction]?.();
		});
};
