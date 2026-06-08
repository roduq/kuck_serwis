### Kuck Serwis

Moduł Frappe do obsługi serwisu zegarków w wielomarkowym warsztacie. Projektowany pod
wygodę recepcji — szybkie przyjęcie, czytelny status, jak najmniej klikania przy kliencie.

#### Co zawiera

- **Naprawa** — główne zlecenie. Klient (ERPNext **Customer**, z edytowalnym telefonem/e-mailem
  zapisywanym z powrotem przy kliencie), identyfikacja zegarka (marka / model / nr seryjny),
  lista usterek (słownik, multiselect), opis, **stan przy przyjęciu + zdjęcia** (ochrona przy
  reklamacjach), sposób dostarczenia i odbioru, orientacyjna wycena i termin, akceptacja klienta,
  finalna kwota i data wydania.
- Klient to **ERPNext Customer** (moduł wymaga ERPNext) — wspólna kartoteka z resztą firmy.
- **Marka Zegarka**, **Usterka**, **Kategoria Napraw** — słowniki z szybkim dodawaniem (Quick Entry).
  Każda **Usterka** (rodzaj naprawy) należy do jednej **Kategorii Napraw** (wymagane pole) — grupowanie
  do celów statystycznych. Słowniki są **wstępnie wypełniane przy instalacji** danymi z arkuszy
  klienta (5 kategorii, ~50 usterek z przypisaną kategorią, ~110 marek) — patrz `kuck_serwis/seed.py`.
- **Workflow „Serwis Naprawa"**: Przyjęto → Diagnoza → Oczekuje na akceptację →
  W naprawie ↔ Oczekiwanie na część → Gotowe do odbioru → Wydano (+ Anulowano).
  Wejście w „W naprawie" wymaga zaznaczonej akceptacji klienta (może być z góry przy przyjęciu).
- **Powiadomienia** klienta (E-mail + SMS) przy „wycenie do akceptacji" i „gotowe do odbioru",
  z możliwością wyłączenia per naprawa (checkbox „Nie powiadamiaj klienta").
- **Karta klienta** — strona Desk z pulpitem jednego klienta: po wybraniu klienta widać jego
  dane kontaktowe, skrót (liczba napraw, w toku, gotowe do odbioru, łączny obrót) oraz pełną
  listę napraw z historią (status, rodzaj, zegarek, kwota, daty), klikalną do naprawy.
  Dostępna ze skrótu w obszarze roboczym oraz przyciskiem „Karta klienta" na formularzu naprawy
  i klienta. Na formularzu **Customer** dochodzi też grupa powiązań **Serwis → Naprawa**.
- Rola **Serwis** dla zespołu warsztatu.

#### Konfiguracja po instalacji

`bench install-app kuck_serwis` automatycznie tworzy rolę Serwis, walutę PLN, workflow
i powiadomienia. Aby powiadomienia faktycznie wychodziły, skonfiguruj w Frappe:

- **E-mail**: Email Account z włączonym ruchem wychodzącym.
- **SMS**: SMS Settings (bramka SMS) — stockowy mechanizm Frappe.

Brak skonfigurowanej bramki **nie blokuje** zmian statusu — nieudana wysyłka trafia tylko do Error Log.

#### Uprawnienia — dostęp dla zespołu serwisu

Cały dostęp niesie jedna rola: **Serwis**. Żeby dać komuś dostęp do modułu, wystarczy przypisać
mu tę rolę (User → Roles → „Serwis"). Rola obejmuje komplet potrzebnych uprawnień:

- pełny dostęp (odczyt/zapis/tworzenie/usuwanie, druk, raport) do DocType modułu:
  **Naprawa**, **Marka Zegarka**, **Usterka**, **Kategoria Napraw** oraz raportu wydań;
- dostęp do powiązanych danych **ERPNext**, których ERPNext domyślnie nie udostępnia:
  **Customer**, **Contact**, **Address** (odczyt/zapis/tworzenie, bez usuwania kartotek) oraz
  odczyt słowników **Customer Group**, **Territory**, **Country** (do wyboru w polach Link).

Uprawnienia nadawane są jako Custom DocPerm i **odtwarzają się idempotentnie** przy każdej
migracji (`after_migrate` → `install.setup_all`), więc przetrwają aktualizacje i resync.

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
