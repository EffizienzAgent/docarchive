frappe.provide("docarchive");

const HOLD = "docarchive.legal_hold";
const EXPIRY = "docarchive.expiry";
const BULK_WORD = "LÖSCHEN";
const escape = (value) => frappe.utils.escape_html(value || "");

docarchive.hold_access = function () {
	if (!docarchive._hold_access) {
		docarchive._hold_access = frappe.xcall(`${HOLD}.access`);
	}
	return docarchive._hold_access;
};

docarchive.admin_access = () => docarchive.hold_access().then((access) => access.admin);

function show_results(results, done) {
	const lines = Object.entries(results)
		.filter(([, result]) => result.message)
		.map(([name, result]) => `<p><b>${escape(name)}</b>: ${escape(result.message)}</p>`);
	if (lines.length) frappe.msgprint(lines.join(""), __("Legal Hold"));
	else frappe.show_alert({ message: __("Done"), indicator: "green" });
	done && done();
}

docarchive.set_legal_hold = function (names, done) {
	frappe.prompt(
		[{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 }],
		({ reason }) =>
			frappe
				.call({ method: `${HOLD}.set_legal_hold`, args: { names, reason }, freeze: true })
				.then((r) => show_results(r.message, done)),
		__("Set Legal Hold"),
		__("Set Legal Hold"),
	);
};

docarchive.release_legal_hold = function (names, done) {
	const dialog = new frappe.ui.Dialog({
		title: __("Release Legal Hold"),
		fields: [
			{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 },
			{
				fieldname: "confirm",
				fieldtype: "Check",
				label: __("I confirm that the legal hold may be released."),
			},
		],
		primary_action_label: __("Release Legal Hold"),
		primary_action({ reason, confirm }) {
			if (!confirm) return frappe.msgprint(__("Please confirm the release."));
			frappe
				.call({
					method: `${HOLD}.release_legal_hold`,
					args: { names, reason, confirm: 1 },
					freeze: true,
				})
				.then((r) => {
					dialog.hide();
					show_results(r.message, done);
				});
		},
	});
	dialog.show();
};

function preview_table(rows) {
	const body = rows
		.map(
			(row) => `<tr>
				<td>${escape(row.name)}</td>
				<td>${escape(row.title)}</td>
				<td>${escape(row.retain_until_date)}</td>
				<td>${row.eligible ? "" : escape(row.reason)}</td>
			</tr>`,
		)
		.join("");
	const headers = ["Business Document", "Title", "Retain Until", "Problem"];
	return `<table class="table table-bordered table-condensed">
		<thead><tr>${headers.map((h) => `<th>${escape(__(h))}</th>`).join("")}</tr></thead>
		<tbody>${body}</tbody>
	</table>`;
}

function queued(done) {
	frappe.show_alert({ message: __("Deletion started in the background"), indicator: "orange" });
	done && done();
}

docarchive.delete_expired = function (names, done) {
	frappe.xcall(`${EXPIRY}.preview`, { names }).then((rows) => {
		const ready = rows.filter((row) => row.eligible).map((row) => row.name);
		const dialog = new frappe.ui.Dialog({
			title: __("Delete Expired Documents"),
			size: "large",
			fields: [
				{ fieldname: "preview", fieldtype: "HTML" },
				{
					fieldname: "confirm",
					fieldtype: "Check",
					label: __(
						"I confirm that these documents are deleted on every storage target. This cannot be undone.",
					),
				},
			],
			primary_action_label: __("Delete"),
			primary_action({ confirm }) {
				if (!ready.length) return dialog.hide();
				if (!confirm) return frappe.msgprint(__("Please confirm the deletion."));
				frappe
					.call({
						method: `${EXPIRY}.delete_documents`,
						args: { names: ready, confirm: 1 },
						freeze: true,
					})
					.then(() => {
						dialog.hide();
						queued(done);
					});
			},
		});
		dialog.fields_dict.preview.$wrapper.html(preview_table(rows));
		dialog.show();
	});
};

docarchive.delete_early = function (names, done) {
	frappe.xcall(`${EXPIRY}.preview`, { names, early: 1 }).then((rows) => {
		const refused = rows.filter((row) => !row.eligible);
		if (refused.length) {
			return frappe.msgprint(
				preview_table(refused),
				__("These documents cannot be deleted early"),
			);
		}
		const word = names.length === 1 ? names[0] : BULK_WORD;
		const warnings = rows
			.filter((row) => row.locked)
			.map(
				(row) =>
					`<p class="text-danger">${escape(row.name)}: ${escape(
						__("Retention period still runs until {0}", [row.retain_until_date]),
					)}</p>`,
			)
			.join("");
		const dialog = new frappe.ui.Dialog({
			title: __("Delete Early (GOVERNANCE only)"),
			fields: [
				{ fieldname: "warning", fieldtype: "HTML" },
				{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 },
				{
					fieldname: "typed",
					fieldtype: "Data",
					label: __("Type {0} to confirm", [word]),
					reqd: 1,
				},
			],
			primary_action_label: __("Delete Early"),
			primary_action({ reason, typed }) {
				if ((typed || "").trim() !== word) {
					return frappe.msgprint(__("Please type {0} to confirm.", [word]));
				}
				frappe
					.call({
						method: `${EXPIRY}.delete_documents`,
						args: { names, confirm: 1, early: 1, reason, typed },
						freeze: true,
					})
					.then(() => {
						dialog.hide();
						queued(done);
					});
			},
		});
		dialog.fields_dict.warning.$wrapper.html(warnings);
		dialog.show();
	});
};

docarchive.legal_hold_list_actions = function (listview) {
	const checked = () => listview.get_checked_items(true);
	const run = (action) => {
		const names = checked();
		if (names.length) action(names, () => listview.refresh());
	};
	docarchive.hold_access().then((access) => {
		if (access.set) {
			listview.page.add_actions_menu_item(__("Set Legal Hold"), () =>
				run(docarchive.set_legal_hold),
			);
		}
		if (!access.admin) return;
		listview.page.add_actions_menu_item(__("Release Legal Hold"), () =>
			run(docarchive.release_legal_hold),
		);
		listview.page.add_actions_menu_item(__("Delete Early (GOVERNANCE only)"), () =>
			run(docarchive.delete_early),
		);
	});
};

function add_admin_buttons(frm) {
	const name = frm.doc.name;
	const reload = () => frm.reload_doc();
	const group = __("Legal Hold");
	if (frm.doc.legal_hold) {
		frm.add_custom_button(
			__("Release Legal Hold"),
			() => docarchive.release_legal_hold([name], reload),
			group,
		);
		return;
	}
	frappe.xcall(`${EXPIRY}.preview`, { names: [name] }).then(([row]) => {
		if (row.eligible) {
			frm.add_custom_button(__("Delete Expired Document"), () =>
				docarchive.delete_expired([name], reload),
			);
			return;
		}
		frappe.xcall(`${EXPIRY}.preview`, { names: [name], early: 1 }).then(([early]) => {
			if (early.eligible) {
				frm.add_custom_button(__("Delete Early (GOVERNANCE only)"), () =>
					docarchive.delete_early([name], reload),
				);
			}
		});
	});
}

frappe.ui.form.on("Business Document", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.archive_status === "Deleted") return;
		const copies = frm.doc.archive_copies || [];
		if (frm.doc.legal_hold) frm.dashboard.add_indicator(__("Legal Hold"), "red");
		if (copies.some((copy) => copy.legal_hold === "Failed")) {
			frm.dashboard.set_headline(
				__("The legal hold is not confirmed on every copy."),
				"orange",
			);
		}
		const archived = copies.some((copy) => copy.status === "Archived");
		if (!archived) return;
		docarchive.hold_access().then((access) => {
			if (access.set) {
				frm.add_custom_button(
					__("Set Legal Hold"),
					() => docarchive.set_legal_hold([frm.doc.name], () => frm.reload_doc()),
					__("Legal Hold"),
				);
			}
			if (access.admin) add_admin_buttons(frm);
		});
		if (!frm.docarchive_deletion_listening) {
			frm.docarchive_deletion_listening = true;
			frappe.realtime.on("docarchive_deletion", (data) => {
				if (data.name !== frm.doc.name) return;
				frappe.show_alert({ message: __("Deletion status: {0}", [__(data.status)]) });
				frm.reload_doc();
			});
		}
	},
});
