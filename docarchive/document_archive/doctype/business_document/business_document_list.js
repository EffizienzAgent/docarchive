frappe.listview_settings["Business Document"] = {
	add_fields: ["archive_status"],
	onload(listview) {
		if (!frappe.model.can_write("Business Document")) return;
		listview.page.add_actions_menu_item(__("Archive now"), () => {
			const names = listview.get_checked_items(true);
			if (names.length) docarchive.archive_now(names, () => listview.refresh());
		});
	},
	get_indicator(doc) {
		const colors = { Pending: "orange", Archived: "green", Failed: "red" };
		const status = doc.archive_status || "Pending";
		return [__(status), colors[status], `archive_status,=,${status}`];
	},
};
