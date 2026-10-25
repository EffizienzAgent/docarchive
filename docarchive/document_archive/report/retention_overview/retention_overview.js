frappe.query_reports["Retention Overview"] = {
	filters: [
		{
			fieldname: "document_type",
			label: __("Business Document Type"),
			fieldtype: "Link",
			options: "Business Document Type",
		},
		{ fieldname: "own_company", label: __("Own Company"), fieldtype: "Link", options: "Own Company" },
		{ fieldname: "counterparty_type", label: __("Counterparty Type"), fieldtype: "Link", options: "DocType" },
		{
			fieldname: "counterparty",
			label: __("Counterparty"),
			fieldtype: "Dynamic Link",
			options: "counterparty_type",
		},
		{
			fieldname: "lock_mode",
			label: __("Lock Mode"),
			fieldtype: "Select",
			options: ["", "GOVERNANCE", "COMPLIANCE"],
		},
		{ fieldname: "ending_within_months", label: __("Ends within (months)"), fieldtype: "Int" },
		{ fieldname: "failed_copies", label: __("Only failed copies"), fieldtype: "Check" },
	],
};
