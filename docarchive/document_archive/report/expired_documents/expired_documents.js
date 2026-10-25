frappe.query_reports["Expired Documents"] = {
	get_datatable_options(options) {
		return { ...options, checkboxColumn: true };
	},
	onload(report) {
		docarchive.admin_access().then((allowed) => {
			if (!allowed) return;
			report.page.add_inner_button(__("Delete Selected"), () => {
				const rows = report.datatable.rowmanager.getCheckedRows();
				const names = rows.map((index) => report.data[index].name);
				if (!names.length)
					return frappe.msgprint(__("Please select at least one document."));
				docarchive.delete_expired(names, () => report.refresh());
			});
		});
	},
};
