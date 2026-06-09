// Copyright (c) 2026, Kuck and contributors
// For license information, please see license.txt
//
// Strona Desk „Karta klienta": recepcja wybiera klienta i widzi komplet jego napraw
// (z historią statusów/dat) oraz skrótowe podsumowanie. Klik w naprawę otwiera ją.

frappe.pages["karta-klienta"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Karta klienta"),
		single_column: true,
	});
	wrapper.karta_klienta = new KartaKlienta(page);
};

frappe.pages["karta-klienta"].on_page_show = function (wrapper) {
	const kk = wrapper.karta_klienta;
	// Klient może przyjść z innego ekranu (frappe.route_options, np. z formularza naprawy)
	// albo z adresu URL (?klient=…) — dzięki temu wybór przeżywa odświeżenie strony (F5)
	// i można podlinkować konkretnego klienta.
	const klient = (frappe.route_options && frappe.route_options.klient) || frappe.urllib.get_arg("klient");
	if (frappe.route_options) {
		frappe.route_options = null;
	}
	if (klient && klient !== kk.klient_field.get_value()) {
		// set_value wyzwoli change → wczyta dane
		kk.ustaw_klienta(klient);
	} else if (kk.klient) {
		// Ten sam klient (lub powrót na stronę): i tak odświeżamy dane przy każdym wejściu,
		// bo status/kwoty naprawy mogły się zmienić w międzyczasie.
		kk.wczytaj();
	}
};

// Kolor wskaźnika statusu — spójnie z workflow naprawy.
const KOLORY_STATUSOW = {
	"Przyjęto": "gray",
	"Diagnoza": "blue",
	"Oczekuje na akceptację": "orange",
	"W naprawie": "blue",
	"Oczekiwanie na część": "yellow",
	"Gotowe do odbioru": "green",
	"Wydano": "darkgrey",
	"Anulowano": "red",
};

class KartaKlienta {
	constructor(page) {
		this.page = page;
		this.klient = null;
		// Pole wyboru klienta musi powstać przed renderem treści — `add_field` dokłada je do
		// `page_form` (prepend w page.main); gdybyśmy nadpisali main przez .html(), zniknęłoby.
		this.dodaj_pole_klienta();
		this.render_szkielet();
	}

	render_szkielet() {
		this.$body = $(`
			<div class="karta-klienta" style="margin-top: 15px;">
				<div class="kk-podsumowanie row" style="margin: 0 0 15px;"></div>
				<div class="kk-naprawy"></div>
			</div>
		`).appendTo(this.page.main);
		this.$podsumowanie = this.$body.find(".kk-podsumowanie");
		this.$naprawy = this.$body.find(".kk-naprawy");
		this.pokaz_pusto(__("Wybierz klienta, aby zobaczyć jego naprawy."));
	}

	dodaj_pole_klienta() {
		this.klient_field = this.page.add_field({
			fieldname: "klient",
			label: __("Klient"),
			fieldtype: "Link",
			options: "Customer",
			change: () => {
				const wartosc = this.klient_field.get_value();
				if (wartosc !== this.klient) {
					this.klient = wartosc;
					this.zapisz_klienta_w_url(wartosc);
					this.wczytaj();
				}
			},
		});
	}

	zapisz_klienta_w_url(klient) {
		// Trzymamy wybór w ?klient=… bez przeładowania trasy (replaceState), żeby przetrwał
		// odświeżenie strony i dało się skopiować link do konkretnego klienta.
		const url = new URL(window.location.href);
		if (klient) {
			url.searchParams.set("klient", klient);
		} else {
			url.searchParams.delete("klient");
		}
		window.history.replaceState(null, "", url);
	}

	ustaw_klienta(klient) {
		this.klient_field.set_value(klient);
	}

	wczytaj() {
		if (!this.klient) {
			this.$podsumowanie.empty();
			this.pokaz_pusto(__("Wybierz klienta, aby zobaczyć jego naprawy."));
			return;
		}
		frappe.dom.freeze(__("Wczytywanie…"));
		frappe
			.call("kuck_serwis.api.dane_klienta", { klient: this.klient })
			.then((r) => this.render(r.message))
			.always(() => frappe.dom.unfreeze());
	}

	render(dane) {
		if (!dane || !dane.klient) {
			this.$podsumowanie.empty();
			this.pokaz_pusto(__("Brak danych klienta."));
			return;
		}
		this.render_podsumowanie(dane);
		this.render_naprawy(dane.naprawy || []);
	}

	render_podsumowanie(dane) {
		const k = dane.klient;
		const s = dane.statystyki || {};
		const kontakt = [k.mobile_no, k.email_id].filter(Boolean).join(" · ") || __("brak danych kontaktowych");
		const kafelek = (etykieta, wartosc) => `
			<div class="col-sm-3">
				<div class="kk-kafelek" style="border:1px solid var(--border-color);border-radius:var(--border-radius-md);padding:10px 14px;">
					<div class="text-muted" style="font-size:var(--text-sm);">${etykieta}</div>
					<div style="font-size:var(--text-2xl);font-weight:600;">${wartosc}</div>
				</div>
			</div>`;
		this.$podsumowanie.html(`
			<div class="col-sm-12" style="margin-bottom:12px;">
				<div style="font-size:var(--text-xl);font-weight:600;">${frappe.utils.escape_html(k.customer_name || k.name)}</div>
				<div class="text-muted">${frappe.utils.escape_html(kontakt)}</div>
			</div>
			${kafelek(__("Napraw łącznie"), s.liczba || 0)}
			${kafelek(__("W toku"), s.w_toku || 0)}
			${kafelek(__("Gotowe do odbioru"), s.gotowe || 0)}
			${kafelek(__("Obrót (wydane)"), format_currency(s.obrot || 0, "PLN"))}
		`);
	}

	render_naprawy(naprawy) {
		if (!naprawy.length) {
			this.pokaz_pusto(__("Ten klient nie ma jeszcze żadnej naprawy."));
			return;
		}
		const wiersze = naprawy.map((n) => this.wiersz_naprawy(n)).join("");
		this.$naprawy.html(`
			<table class="table table-hover" style="margin:0;">
				<thead>
					<tr>
						<th>${__("Naprawa")}</th>
						<th>${__("Status")}</th>
						<th>${__("Rodzaj")}</th>
						<th>${__("Zegarek")}</th>
						<th class="text-right">${__("Kwota")}</th>
						<th>${__("Przyjęto")}</th>
						<th>${__("Wydano")}</th>
					</tr>
				</thead>
				<tbody>${wiersze}</tbody>
			</table>
		`);
		this.$naprawy.find("a.kk-link").on("click", (e) => {
			e.preventDefault();
			frappe.set_route("Form", "Naprawa", $(e.currentTarget).attr("data-name"));
		});
	}

	wiersz_naprawy(n) {
		const kolor = KOLORY_STATUSOW[n.status] || "gray";
		const status = `<span class="indicator-pill ${kolor}">${frappe.utils.escape_html(n.status || "")}</span>`;
		const zegarek = [n.marka, n.model_zegarka].filter(Boolean).join(" ");
		const seryjny = n.numer_seryjny ? ` <span class="text-muted">(${frappe.utils.escape_html(n.numer_seryjny)})</span>` : "";
		const kwota = n.kwota_odbioru || n.orientacyjna_wycena || 0;
		return `
			<tr>
				<td><a href="#" class="kk-link" data-name="${frappe.utils.escape_html(n.name)}">${frappe.utils.escape_html(n.name)}</a></td>
				<td>${status}</td>
				<td>${frappe.utils.escape_html(n.rodzaj_naprawy || "")}</td>
				<td>${frappe.utils.escape_html(zegarek)}${seryjny}</td>
				<td class="text-right">${kwota ? format_currency(kwota, "PLN") : ""}</td>
				<td>${n.creation ? frappe.datetime.str_to_user(n.creation).split(" ")[0] : ""}</td>
				<td>${n.data_wydania ? frappe.datetime.str_to_user(n.data_wydania) : ""}</td>
			</tr>`;
	}

	pokaz_pusto(tekst) {
		this.$naprawy.html(`<div class="text-muted" style="padding:40px 0;text-align:center;">${frappe.utils.escape_html(tekst)}</div>`);
	}
}
