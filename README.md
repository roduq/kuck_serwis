### Kuck Serwis

Moduł Frappe do obsługi serwisu zegarków w wielomarkowym warsztacie. Projektowany pod
wygodę recepcji — szybkie przyjęcie, czytelny status, jak najmniej klikania przy kliencie.

#### Co zawiera

- **Naprawa** — główne zlecenie. Klient (jako referencja, z podglądem telefonu/e-maila),
  identyfikacja zegarka (marka / model / nr seryjny), lista usterek (słownik, multiselect),
  opis, **stan przy przyjęciu + zdjęcia** (ochrona przy reklamacjach), sposób dostarczenia
  i odbioru, orientacyjna wycena i termin, akceptacja klienta, finalna kwota i data wydania.
- **Klient**, **Marka Zegarka**, **Usterka** — słowniki z szybkim dodawaniem (Quick Entry).
  Karta klienta pokazuje powiązane naprawy.
- **Workflow „Serwis Naprawa"**: Przyjęto → Diagnoza → Oczekuje na akceptację →
  W naprawie ↔ Oczekiwanie na część → Gotowe do odbioru → Wydano (+ Anulowano).
  Wejście w „W naprawie" wymaga zaznaczonej akceptacji klienta (może być z góry przy przyjęciu).
- **Powiadomienia** klienta (E-mail + SMS) przy „wycenie do akceptacji" i „gotowe do odbioru",
  z możliwością wyłączenia per naprawa (checkbox „Nie powiadamiaj klienta").
- Rola **Serwis** dla zespołu warsztatu.

#### Konfiguracja po instalacji

`bench install-app kuck_serwis` automatycznie tworzy rolę Serwis, walutę PLN, workflow
i powiadomienia. Aby powiadomienia faktycznie wychodziły, skonfiguruj w Frappe:

- **E-mail**: Email Account z włączonym ruchem wychodzącym.
- **SMS**: SMS Settings (bramka SMS) — stockowy mechanizm Frappe.

Brak skonfigurowanej bramki **nie blokuje** zmian statusu — nieudana wysyłka trafia tylko do Error Log.

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch version-16
bench install-app kuck_serwis
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/kuck_serwis
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit
