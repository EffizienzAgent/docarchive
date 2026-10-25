frappe.provide("docarchive");

const EXPORT = "docarchive.export";
const SCOPE_FIELDS = [
	"own_company",
	"document_type",
	"date_from",
	"date_to",
	"counterparty_type",
	"counterparty",
];

docarchive.export_access = function () {
	if (!docarchive._export_access) {
		docarchive._export_access = frappe.xcall(`${EXPORT}.access`);
	}
	return docarchive._export_access;
};

docarchive.export_archive = function (defaults = {}) {
	docarchive.export_access().then((access) => {
		if (!access.export) return;
		const dialog = new frappe.ui.Dialog({
			title: __("Export archive"),
			fields: [
				{
					fieldname: "target_name",
					fieldtype: "Select",
					label: __("Storage Target"),
					options: access.targets,
					default: access.targets[0],
					reqd: 1,
				},
				{
					fieldname: "own_company",
					fieldtype: "Link",
					label: __("Own Company"),
					options: "Own Company",
				},
				{
					fieldname: "document_type",
					fieldtype: "Link",
					label: __("Business Document Type"),
					options: "Business Document Type",
				},
				{ fieldname: "column_break_dates", fieldtype: "Column Break" },
				{ fieldname: "date_from", fieldtype: "Date", label: __("From Date") },
				{ fieldname: "date_to", fieldtype: "Date", label: __("To Date") },
				{ fieldname: "section_party", fieldtype: "Section Break" },
				{
					fieldname: "counterparty_type",
					fieldtype: "Link",
					label: __("Counterparty Type"),
					options: "DocType",
				},
				{
					fieldname: "counterparty",
					fieldtype: "Dynamic Link",
					label: __("Counterparty"),
					options: "counterparty_type",
					depends_on: "counterparty_type",
				},
			],
			primary_action_label: __("Start export"),
			primary_action(values) {
				frappe
					.xcall(`${EXPORT}.start_export`, { scope: values })
					.then((name) => {
						dialog.hide();
						frappe.show_alert({
							message: __("Export started in the background"),
							indicator: "orange",
						});
						frappe.set_route("Form", "Archive Export", name);
					});
			},
		});
		dialog.set_values(defaults);
		dialog.show();
	});
};

docarchive.list_export_defaults = function (listview) {
	const defaults = {};
	for (const [, field, operator, value] of listview.filter_area.get()) {
		if (operator === "=" && SCOPE_FIELDS.includes(field)) defaults[field] = value;
	}
	return defaults;
};

docarchive.add_export_button = function (listview) {
	docarchive.export_access().then((access) => {
		if (!access.export) return;
		listview.page.add_inner_button(__("Export archive"), () =>
			docarchive.export_archive(docarchive.list_export_defaults(listview)),
		);
	});
};
