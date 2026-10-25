frappe.listview_settings["Business Document"] = {
	add_fields: ["archive_status", "legal_hold", "counterparty"],
	formatters: {
		counterparty_name(value, _df, doc) {
			return frappe.utils.escape_html(docarchive.documents_core.party_label(value, doc.counterparty));
		},
	},
	onload(listview) {
		docarchive.legal_hold_list_actions(listview);
		docarchive.add_export_button(listview);
		if (!frappe.model.can_write("Business Document")) return;
		const refresh = () => listview.refresh();
		listview.page.add_actions_menu_item(__("Archive now"), () => {
			const names = listview.get_checked_items(true);
			if (names.length) docarchive.archive_now(names, refresh);
		});
		listview.page.add_actions_menu_item(__("Assign"), () =>
			docarchive.triage(listview.get_checked_items(true), { on_close: refresh }),
		);
		listview.page.add_actions_menu_item(__("Discard"), () => {
			const names = listview.get_checked_items(true);
			if (names.length) docarchive.discard_documents(names, refresh);
		});
		listview.page.add_actions_menu_item(__("Extend retention"), () => {
			const names = listview.get_checked_items(true);
			if (names.length) docarchive.extend_many({ names }, refresh);
		});
		listview.page.add_inner_button(__("Assign all"), () =>
			docarchive.triage_all({ on_close: refresh }),
		);
	},
	get_indicator(doc) {
		const colors = { Pending: "orange", Archived: "green", Failed: "red", Deleted: "gray" };
		const status = doc.archive_status || "Pending";
		return [__(status), colors[status], `archive_status,=,${status}`];
	},
};
