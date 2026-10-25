frappe.query_reports["Archive Completeness"] = {
	filters: [
		{
			fieldname: "problem",
			label: __("Problem"),
			fieldtype: "Select",
			options: ["", "Overdue", "Failed", "Missing Copies", "Number Gap", "Mail Original"].map((value) => ({
				value,
				label: value ? __(value) : "",
			})),
		},
		{
			fieldname: "document_type",
			label: __("Business Document Type"),
			fieldtype: "Link",
			options: "Business Document Type",
		},
		{ fieldname: "own_company", label: __("Own Company"), fieldtype: "Link", options: "Own Company" },
		{ fieldname: "number_gaps", label: __("Show Number Gaps"), fieldtype: "Check" },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "problem" && data) value = __(data.problem);
		return value;
	},
};
