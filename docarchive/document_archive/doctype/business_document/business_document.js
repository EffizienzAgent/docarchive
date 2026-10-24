const ARCHIVE_COLORS = { Pending: "orange", Archived: "green", Failed: "red" };

frappe.ui.form.on("Business Document", {
	setup(frm) {
		frappe.xcall("docarchive.counterparty.doctypes").then((doctypes) => {
			frm.set_query("counterparty_type", () => ({
				filters: doctypes.length
					? { name: ["in", doctypes] }
					: { istable: 0, issingle: 0 },
			}));
		});
	},
	refresh(frm) {
		if (frm.is_new()) return;
		const status = frm.doc.archive_status || "Pending";
		frm.page.set_indicator(__(status), ARCHIVE_COLORS[status]);
		if (!frm.doc.archive_started_at && frm.perm[0].write && !frm.is_dirty()) {
			frappe
				.xcall("docarchive.archive_now.preview", { names: [frm.doc.name] })
				.then(([row]) => {
					if (!row.eligible || frm.doc.archive_started_at) return;
					frm.add_custom_button(__("Archive now"), () =>
						docarchive.confirm_archive([row], () => frm.reload_doc()),
					);
				});
		}
		if (!frm.docarchive_listening) {
			frm.docarchive_listening = true;
			frappe.realtime.on("docarchive_archive_now", (data) => {
				if (data.name !== frm.doc.name) return;
				frappe.show_alert({
					message: __("Archive status: {0}", [__(data.status)]),
					indicator: ARCHIVE_COLORS[data.status],
				});
				frm.reload_doc();
			});
		}
	},
	counterparty_type(frm) {
		frm.set_value("counterparty", null);
	},
});
