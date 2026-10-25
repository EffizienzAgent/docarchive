frappe.provide("docarchive");

const METHOD = "docarchive.extension";
const EVENT = "docarchive_extension";
const esc = (value) => frappe.utils.escape_html(value ?? "");
const until_text = (iso) => (iso ? docarchive.format_retain_until(iso) : "–");
const NO_SHORTENING = () => __("The retention cannot be shortened afterwards.");

function how_field(options, value) {
	return {
		fieldname: "how",
		fieldtype: "Select",
		label: __("Extend"),
		options: options.map(([value, label]) => ({ value, label })),
		default: value,
		reqd: 1,
	};
}

const years_field = (show_for, value) => ({
	fieldname: "years",
	fieldtype: "Int",
	label: __("Years"),
	default: value,
	depends_on: `eval:doc.how=="${show_for}"`,
	mandatory_depends_on: `eval:doc.how=="${show_for}"`,
});

const date_fields = [
	{
		fieldname: "until",
		fieldtype: "Date",
		label: __("New date"),
		depends_on: 'eval:doc.how=="to_date"',
		mandatory_depends_on: 'eval:doc.how=="to_date"',
	},
	{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason") },
];

function copies_table(copies) {
	const rows = copies
		.map(
			(copy) =>
				`<tr><td>${esc(copy.target)}</td><td>${esc(copy.mode)}</td><td>${esc(until_text(copy.retain_until))}</td></tr>`,
		)
		.join("");
	return `<table class="table table-condensed">
		<thead><tr><th>${esc(__("Storage"))}</th><th>${esc(__("Lock Mode"))}</th><th>${esc(__("Retain Until (UTC)"))}</th></tr></thead>
		<tbody>${rows}</tbody>
	</table>`;
}

docarchive.extension_options = (name) => frappe.xcall(`${METHOD}.options`, { name });

docarchive.extend_retention = function (name, on_done) {
	docarchive.extension_options(name).then((options) => {
		const dialog = new frappe.ui.Dialog({
			title: __("Extend retention"),
			fields: [
				{ fieldname: "copies", fieldtype: "HTML", options: copies_table(options.copies) },
				how_field(
					[
						["add_years", __("Add years")],
						["to_date", __("Until a date")],
					],
					"add_years",
				),
				years_field("add_years", 2),
				...date_fields,
			],
			primary_action_label: __("Extend"),
			primary_action(values) {
				frappe.confirm(NO_SHORTENING(), () =>
					frappe
						.call({ method: `${METHOD}.extend`, args: { name, ...values }, freeze: true })
						.then(({ message }) => {
							dialog.hide();
							frappe.show_alert({
								message: message.done
									? __("Kept until {0}.", [until_text(message.retain_until)])
									: __("The extension is not finished on every target yet. The nightly run retries it."),
								indicator: message.done ? "green" : "orange",
							});
							on_done && on_done(message);
						}),
				);
			},
		});
		dialog.show();
	});
};

function preview_table(rows) {
	const body = rows
		.map(
			(row) => `<tr class="${row.skipped ? "text-muted" : ""}">
				<td>${esc(row.title || row.name)}</td>
				<td>${esc(until_text(row.current))}</td>
				<td>${row.skipped ? esc(row.skipped) : esc(until_text(row.new))}</td>
			</tr>`,
		)
		.join("");
	return `<div class="docarchive-extension-preview"><table class="table table-condensed">
		<thead><tr><th>${esc(__("Title"))}</th><th>${esc(__("Current"))}</th><th>${esc(__("New"))}</th></tr></thead>
		<tbody>${body}</tbody>
	</table></div>`;
}

function follow_job(total, on_done) {
	const handler = (data) => {
		frappe.show_progress(__("Extend retention"), data.done, data.total || total);
		if (!data.result) return;
		frappe.realtime.off(EVENT, handler);
		frappe.hide_progress();
		const { extended, pending, skipped, failed } = data.result;
		frappe.msgprint({
			title: __("Extend retention"),
			message: [
				__("Extended: {0}", [extended.length]),
				__("Still running on some targets: {0}", [pending.length]),
				__("Skipped: {0}", [Object.keys(skipped).length]),
				__("Failed: {0}", [Object.keys(failed).length]),
			]
				.map(esc)
				.join("<br>"),
		});
		on_done && on_done(data.result);
	};
	frappe.realtime.on(EVENT, handler);
}

docarchive.extend_many = function (scope, on_done) {
	const dialog = new frappe.ui.Dialog({
		title: __("Extend retention"),
		fields: [
			how_field(
				[
					["at_least", __("At least N years from the retention start")],
					["to_date", __("Until a date")],
				],
				"at_least",
			),
			years_field("at_least", 10),
			...date_fields,
		],
		primary_action_label: __("Preview"),
		primary_action(values) {
			frappe.xcall(`${METHOD}.preview`, { ...scope, ...values }).then((preview) => {
				dialog.hide();
				confirm_many(scope, values, preview, on_done);
			});
		},
	});
	dialog.show();
};

function confirm_many(scope, values, preview, on_done) {
	const skipped = preview.rows.length - preview.count;
	const dialog = new frappe.ui.Dialog({
		title: __("Extend retention"),
		size: "large",
		fields: [
			{
				fieldname: "summary",
				fieldtype: "HTML",
				options: `<p>${esc(__("{0} documents will be extended, {1} are skipped.", [preview.count, skipped]))}</p>${preview_table(preview.rows)}`,
			},
		],
		primary_action_label: __("Extend {0} documents", [preview.count]),
		primary_action() {
			if (!preview.count) return dialog.hide();
			frappe.confirm(NO_SHORTENING(), () =>
				frappe.xcall(`${METHOD}.extend_many`, { ...scope, ...values }).then((job) => {
					dialog.hide();
					follow_job(job.total, on_done);
					frappe.show_alert({ message: __("Extension started."), indicator: "blue" });
				}),
			);
		},
	});
	dialog.show();
}
