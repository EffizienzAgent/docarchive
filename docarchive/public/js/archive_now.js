frappe.provide("docarchive");

docarchive.format_retain_until = (iso) =>
	`${frappe.datetime.str_to_user(iso.slice(0, 10))} ${iso.slice(11, 16)} UTC`;

docarchive.confirm_archive = (rows, on_done) => {
	const eligible = rows.filter((row) => row.eligible);
	const refused = rows.filter((row) => !row.eligible);
	if (!eligible.length) {
		frappe.msgprint(
			refused
				.map(
					(row) =>
						`${frappe.utils.escape_html(row.title || row.name)}: ${frappe.utils.escape_html(row.reason)}`,
				)
				.join("<br>"),
		);
		return;
	}
	const dates = [
		...new Set(eligible.map((row) => docarchive.format_retain_until(row.retain_until))),
	];
	const modes = eligible[0].modes.join(", ");
	let message = __(
		"After this, the file and the archived fields of {0} document(s) can no longer be changed or deleted. They are kept unchangeable until {1} (mode {2}).",
		[eligible.length, dates.join(", "), modes],
	);
	if (refused.length) {
		message +=
			"<br><br>" +
			__("{0} selected document(s) cannot be archived yet and are skipped.", [
				refused.length,
			]);
	}
	frappe.confirm(message, () =>
		frappe
			.xcall("docarchive.archive_now.archive_now", {
				names: eligible.map((row) => row.name),
			})
			.then((result) => {
				frappe.show_alert({ message: __("Archiving started."), indicator: "blue" });
				on_done && on_done(result);
			}),
	);
};

docarchive.archive_now = (names, on_done) =>
	frappe
		.xcall("docarchive.archive_now.preview", { names })
		.then((rows) => docarchive.confirm_archive(rows, on_done));
