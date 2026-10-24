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
	},
	counterparty_type(frm) {
		frm.set_value("counterparty", null);
	},
});
