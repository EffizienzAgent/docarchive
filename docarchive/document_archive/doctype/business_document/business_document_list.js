frappe.listview_settings["Business Document"] = {
	add_fields: ["archive_status"],
	get_indicator(doc) {
		const colors = { Pending: "orange", Archived: "green", Failed: "red" };
		const status = doc.archive_status || "Pending";
		return [__(status), colors[status], `archive_status,=,${status}`];
	},
};
