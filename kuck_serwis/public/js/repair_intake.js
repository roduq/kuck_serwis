(() => {
	"use strict";

	const form = document.getElementById("repair-intake-form");
	if (!form) return;
	const shipping = document.getElementById("shipping-details");
	const valueInput = document.getElementById("declared-value");
	const errors = document.getElementById("repair-intake-errors");
	const success = document.getElementById("repair-intake-success");
	const submit = form.querySelector('button[type="submit"]');
	const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
	let idempotencyKey = "";

	function createKey() {
		if (globalThis.crypto?.randomUUID)
			return `repair_${globalThis.crypto.randomUUID().replaceAll("-", "")}`;
		if (!globalThis.crypto?.getRandomValues)
			throw new Error("Secure random number generation is unavailable");
		const values = new Uint8Array(24);
		globalThis.crypto.getRandomValues(values);
		return `repair_${Array.from(values, (value) => value.toString(16).padStart(2, "0")).join(
			""
		)}`;
	}

	function isShipping() {
		return ["delivery_method", "return_method"].some(
			(name) => form.querySelector(`input[name="${name}"]:checked`)?.value === "COURIER"
		);
	}

	function updateShipping() {
		const visible = isShipping();
		shipping.hidden = !visible;
		valueInput.required = visible;
		if (!visible) valueInput.value = "";
	}

	function field(name) {
		return form.elements.namedItem(name)?.value?.trim() ?? "";
	}

	function payload() {
		return {
			full_name: field("full_name"),
			email: field("email"),
			phone: field("phone"),
			brand: field("brand"),
			model: field("model"),
			serial_number: field("serial_number"),
			purchase_date: field("purchase_date"),
			issue_description: field("issue_description"),
			condition_description: field("condition_description"),
			warranty: field("warranty"),
			delivery_method: field("delivery_method"),
			return_method: field("return_method"),
			declared_value: field("declared_value"),
			privacy_accepted: form.elements.namedItem("privacy_accepted")?.checked ?? false,
			website: field("website"),
		};
	}

	function showError(message) {
		errors.textContent = message;
		errors.hidden = false;
		errors.focus();
	}

	function clearError() {
		errors.hidden = true;
		errors.textContent = "";
	}

	try {
		idempotencyKey = createKey();
	} catch (_error) {
		showError(
			"Ta przeglądarka nie może bezpiecznie wysłać formularza. Skontaktuj się z serwisem lub użyj aktualnej wersji przeglądarki."
		);
		submit.disabled = true;
		return;
	}
	if (!csrf) {
		showError(
			"Nie udało się przygotować bezpiecznego formularza. Odśwież stronę i spróbuj ponownie."
		);
		return;
	}

	form.addEventListener("change", (event) => {
		if (["delivery_method", "return_method"].includes(event.target.name)) updateShipping();
	});

	form.addEventListener("submit", async (event) => {
		event.preventDefault();
		clearError();
		if (!form.reportValidity()) return;
		submit.disabled = true;
		submit.textContent = "Wysyłanie…";
		try {
			const response = await fetch(
				"/api/method/kuck_serwis.repair_intake.submit_repair_intake",
				{
					method: "POST",
					credentials: "same-origin",
					headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": csrf },
					body: JSON.stringify({ payload: payload(), idempotency_key: idempotencyKey }),
				}
			);
			if (!response.ok) throw new Error("SUBMIT_FAILED");
			const data = await response.json();
			if (data.message?.accepted !== true) throw new Error("SUBMIT_FAILED");
			form.hidden = true;
			success.hidden = false;
			success.focus();
		} catch (_error) {
			showError(
				"Nie udało się wysłać zgłoszenia. Sprawdź dane i połączenie, a następnie spróbuj ponownie."
			);
			submit.disabled = false;
			submit.textContent = "Wyślij zgłoszenie";
		}
	});

	updateShipping();
	submit.disabled = false;
})();
