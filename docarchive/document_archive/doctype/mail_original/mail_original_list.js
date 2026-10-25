frappe.listview_settings["Mail Original"] = {
	onload(listview) {
		if (!frappe.model.can_create("Business Document")) return;
		listview.page.add_actions_menu_item(__("Archive"), () => {
			const names = listview.get_checked_items(true);
			if (names.length) docarchive.archive_originals(names, () => listview.refresh());
		});
	},
};
