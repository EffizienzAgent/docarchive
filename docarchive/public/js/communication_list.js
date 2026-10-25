(() => {
	const settings = (frappe.listview_settings["Communication"] ||= {});
	const onload = settings.onload;
	settings.onload = function (listview) {
		onload && onload.apply(this, arguments);
		if (!frappe.model.can_create("Business Document")) return;
		listview.page.add_actions_menu_item(__("Archive"), () => {
			const names = listview.get_checked_items(true);
			if (names.length) docarchive.archive_communications(names, () => listview.refresh());
		});
	};
})();
