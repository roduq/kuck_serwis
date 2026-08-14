# ADR 0001 — publiczny kontrakt odczytu napraw v1

Status: proposed; wdrożenie wymaga schematu, backfillu i testów IDOR

Data: 2026-08-14
Audytowana rewizja: `1820cc644ce1eb146b6e19f074c6ca4768827b33`

## Kontekst

`kuck_serwis` pozostaje właścicielem `Naprawa` i reguł jej ujawniania. Sklep może
wyświetlać dane napraw wyłącznie przez mały, wersjonowany moduł
`kuck_serwis.public_contract.v1`. Ten ADR jest counterpartem ADR 0005 w
`kuck_shop`; nie czyni publicznymi DocType, SQL, kontrolerów dokumentów ani
dotychczasowego API dla Desk.

Stan bieżącej aplikacji uzasadnia osobną granicę:

- `Naprawa.klient` jest wymaganym linkiem do `Customer`, a nazwa dokumentu ma
  przewidywalny format `NAP-.YYYY.-.#####`
  (`kuck_serwis/kuck_serwis/doctype/naprawa/naprawa.json:4,54-61`);
- jedyny obecny modułowy endpoint `dane_klienta(klient)` przyjmuje nazwę
  Customer od wywołującego, sprawdza ogólne DocPerm i zwraca szeroki model Desk
  (`kuck_serwis/api.py:32-65`); nie jest to autoryzacja portalu;
- aplikacja nie rejestruje `permission_query_conditions` ani `has_permission`
  dla `Naprawa` (`kuck_serwis/hooks.py:139-149`), więc ogólnego Resource API nie
  wolno wystawić użytkownikowi Website;
- `Naprawa` zawiera wewnętrzne opisy, numer seryjny, dane kontaktowe, wycenę i
  zdjęcia (`kuck_serwis/kuck_serwis/doctype/naprawa/naprawa.json:63-185,227-285`),
  a child table zdjęć przechowuje zwykłe `Attach Image` bez modelu widoczności
  (`kuck_serwis/kuck_serwis/doctype/naprawa_zdjecie/naprawa_zdjecie.json:8-25`);
- zapis `Naprawa` może zmieniać `Contact` z `ignore_permissions=True` oraz pola
  podsumowujące Customer (`kuck_serwis/kuck_serwis/doctype/naprawa/naprawa.py:56-101`).
  Dlatego v1 nie zawiera żadnej mutacji;
- obecne testy API potwierdzają tylko filtr dla jednego jawnie podanego Customer
  i happy path pracownika (`kuck_serwis/kuck_serwis/doctype/naprawa/test_naprawa.py:205-248`),
  nie macierz użytkownik A/użytkownik B/Guest.

## Decyzja i zakres v1

Pierwszy wydawany kontrakt jest wyłącznie kontowym odczytem:

```text
kuck_serwis.public_contract.v1.get_capabilities()
kuck_serwis.public_contract.v1.list_repairs_for_current_user(cursor=None, page_size=20)
kuck_serwis.public_contract.v1.get_repair_for_current_user(repair_id)
```

Funkcje są publiczne dla kodu innych zainstalowanych aplikacji, ale nie otrzymują
`@frappe.whitelist()`: przeglądarka wywołuje endpoint należący do `kuck_shop`, a
ten importuje adapter w tym samym procesie. Nie powstaje drugi, łatwy do ominięcia
endpoint HTTP. Adapter nie wywołuje `dane_klienta`, nie używa `frappe.get_doc`
jako fallbacku i nie przyjmuje `customer`, `user` ani wewnętrznego
`Naprawa.name` od klienta.

Poza tym ADR i poza pierwszym rolloutem pozostają:

- `token-read`, generowanie, TTL, hash i odwoływanie grantów;
- decyzje o wycenie, upload zdjęć i wszystkie pozostałe mutacje;
- aliasy legacy oraz publiczne wyszukiwanie po starym numerze;
- tworzenie konta/Customer i wybór guest checkout. D06 pozostaje otwarta.

Te obszary wymagają osobnych ADR i nie mogą być reklamowane w `features` v1.

## Negocjacja i domyślnie wyłączony rollout

`get_capabilities()` zawsze zwraca obiekt o ścisłym kształcie:

```json
{
  "contract": "kuck-serwis/v1",
  "schema_revision": 1,
  "features": []
}
```

`features` zawiera `"account-read"` tylko wtedy, gdy wszystkie warunki są
spełnione: flaga wdrożeniowa `enable_kuck_serwis_account_read` ma jawną wartość
true, backfill `public_id` jest kompletny, istnieje unikalny indeks, a check
gotowości nie wykrywa nieznanych statusów. Brak flagi, wartość niepoprawna albo
błąd checku oznacza pustą listę. Flagę ustawia się per site; jej brak jest
wartością domyślną false. Adapter nigdy nie ogłasza częściowo działającej
capability.

`kuck_shop` ma wtedy zwrócić kontrolowane `DEPENDENCY_UNAVAILABLE`, bez fallbacku
do DocType, SQL lub API Desk. `token-read` nie jest ogłaszane w tej rewizji.

## Publiczny identyfikator naprawy

Do `Naprawa` zostanie dodane systemowo zarządzane pole `public_id` typu `Data`,
`read_only`, `no_copy`, bez wartości domyślnej i docelowo z `unique: 1`.
Identyfikator ma format `rpr_` + base64url bez paddingu z 24 losowych bajtów
(192 bity entropii), generowanych przez kryptograficzny generator systemowy.
Nie zawiera czasu, numeru dokumentu, Customer ani legacy ID. Kod nie regeneruje
już zapisanej wartości i nie pozwala zmienić jej przez formularz, import danych
ani argument API.

`public_id` jest bezpiecznym, nieprzewidywalnym uchwytem do URL i audytu, ale
**nie jest uwierzytelnieniem ani autoryzacją**. Znajomość `public_id` nigdy nie
wystarcza do odczytu. Każde pobranie ponownie ogranicza zapytanie do Customer
wynikających z bieżącej sesji. Wewnętrzne `Naprawa.name` oraz format
`NAP-.YYYY.-.#####` nie opuszczają read modelu.

Kolizję rozstrzyga constraint bazy. Generator dla nowego dokumentu losuje do
pięciu kandydatów i odrzuca wartości już istniejące; constraint pozostaje
ostateczną ochroną przed wyścigiem pomiędzy transakcjami. Konflikt constraintu
przerywa zapis i emituje krytyczny alert; kod nie zastępuje wartości
przewidywalnym fallbackiem.

## Autoryzacja konta

Źródłem uprawnienia jest wyłącznie standardowa child table
`Customer.portal_users`. Dla każdego wywołania adapter:

1. odrzuca `Guest`, wyłączonego User, User innego typu niż `Website User` oraz
   sesję bez uwierzytelnionego User kodem `AUTH_REQUIRED`;
2. na serwerze pobiera zbiór `Customer.name` z `Portal User`, z warunkami
   `parenttype = "Customer"`, `parentfield = "portal_users"` i
   `user = frappe.session.user`;
3. ogranicza to samo zapytanie o `Naprawa` do `klient IN <ten zbiór>`;
4. nie dopasowuje po e-mailu, telefonie, nazwie Customer, Contact, roli `Serwis`
   ani wartości przesłanej przez przeglądarkę.

Jedno konto może jawnie należeć do kilku Customer i wtedy lista jest sumą ich
napraw. Brak powiązania daje pustą listę, nigdy globalny zakres. Samo posiadanie
roli `Customer` nie daje dostępu bez wiersza `portal_users`; szerokie DocPerm roli
`Serwis` z `kuck_serwis/install.py:46-58,132-146` nie uczestniczą w autoryzacji
portalu.

`get_repair_for_current_user(repair_id)` wykonuje jedno zapytanie z koniunkcją
`public_id = repair_id` oraz autoryzowanym zbiorem Customer. Nie wykonuje najpierw
globalnego lookupu po `public_id`, co ogranicza różnice odpowiedzi i czasów dla
rekordu obcego oraz nieistniejącego.

## Wersjonowany read model i statusy

Każdy element jest budowany przez jawną funkcję projekcji, nie przez
`doc.as_dict()`, `SELECT *` ani automatyczną serializację DocType. Dozwolony
kształt `repair-portal/v1` to:

```json
{
  "schema": "repair-portal/v1",
  "repair_id": "rpr_...",
  "public_status": "in_repair",
  "status_label": "W naprawie",
  "watch": {"brand": "...", "model": "..."},
  "received_on": "YYYY-MM-DD",
  "estimated_completion_on": "YYYY-MM-DD",
  "quote": {"amount": "0.00", "currency": "PLN"},
  "actions": []
}
```

Wartości puste są `null`; klucze pozostają stabilne. Kwota jest tekstem
dziesiętnym o dwóch miejscach i zawsze ma walutę. `actions` w read-only v1 jest
zawsze pustą listą. Marka i model są zwykłym opisem przedmiotu, nie linkami do
DocType. `received_on` pochodzi z daty utworzenia dokumentu, bez czasu.

Allowlista mapowania bieżących statusów:

| Status wewnętrzny | `public_status` | Publiczna etykieta PL |
|---|---|---|
| `Przyjęto` | `received` | `Przyjęto` |
| `Diagnoza` | `diagnosis` | `Diagnoza` |
| `Oczekuje na akceptację` | `awaiting_customer` | `Oczekuje na akceptację` |
| `W naprawie` | `in_repair` | `W naprawie` |
| `Oczekiwanie na część` | `awaiting_part` | `Oczekiwanie na część` |
| `Gotowe do odbioru` | `ready_for_collection` | `Gotowe do odbioru` |
| `Wydano` | `completed` | `Zakończono` |
| `Anulowano` | `cancelled` | `Anulowano` |

Lista odpowiada obecnemu polu Select
(`kuck_serwis/kuck_serwis/doctype/naprawa/naprawa.json:94-101`) i workflow
(`kuck_serwis/install.py:16-42`). Nieznany status nie jest zwracany jako surowy
tekst: pojedynczy odczyt kończy się `DEPENDENCY_UNAVAILABLE`, capability readiness
nie pozwala włączyć ruchu, a metryka wskazuje brak mapowania. Dodanie statusu
wewnętrznego wymaga świadomego rozszerzenia mapy i testu kontraktowego.

Model nigdy nie zawiera `Naprawa.name`, `Customer.name`, telefonu, e-maila,
pełnego numeru seryjnego, opisów/uwag warsztatu, przełączników powiadomień,
`akceptacja_uwagi`, child rows zdjęć ani ścieżek `File`. Dodanie nowego pola do
DocType nie rozszerza automatycznie odpowiedzi.

## Paginacja

`list_repairs_for_current_user` stosuje keyset pagination w stałym porządku
`creation DESC, public_id DESC`:

- `page_size` musi być liczbą całkowitą 1–50; domyślnie 20;
- odpowiedź ma kształt `{items, next_cursor}`, a `next_cursor` jest `null`, gdy
  nie ma następnej strony;
- nieprzezroczysty, podpisany cursor zawiera rewizję schematu, ostatnią parę
  `(creation, public_id)`, skrót zakresu autoryzowanych Customer i czas ważności;
- podpis używa klucza serwerowego, cursor ma krótki TTL i nie jest logowany;
- zmiana użytkownika, zbioru `portal_users`, wersji schematu, podpisu albo TTL
  daje `INVALID_CURSOR`, bez ujawniania zawartości;
- cursor nie jest grantem. Każda strona ponownie wyprowadza Customer z sesji i
  dokłada warunek autoryzacyjny do zapytania.

Nie używamy offsetu, `limit_page_length=0` ani arbitralnego `order_by` od klienta.

## Błędy i nierozróżnialne NOT_FOUND

Publiczne kody tego przyrostu to `AUTH_REQUIRED`, `NOT_FOUND`, `INVALID_CURSOR`,
`VALIDATION_FAILED` i `DEPENDENCY_UNAVAILABLE`. Odpowiedź nie zawiera tracebacka,
nazwy tabeli, wewnętrznego ID ani surowego komunikatu Frappe.

Obcy `public_id`, poprawnie sformatowany lecz nieistniejący `public_id` oraz
wewnętrzne `Naprawa.name` podane w miejscu `repair_id` dają identyczny status HTTP,
kod `NOT_FOUND` i publiczny komunikat. Walidacja formatu może zostać wykonana,
ale jej publiczny rezultat nadal jest `NOT_FOUND`. Zaufany audyt może rozróżnić
`foreign`, `missing` i `malformed` przez drugie, stałokształtne zapytanie
wykonywane dla każdego chybienia (dla błędnego formatu: lookup wartości
zastępczej). Każde chybienie wykonuje tę samą liczbę zapytań i przechodzi ten sam
budżet odpowiedzi. Rozróżnienia nie wolno przekazać do odpowiedzi ani analityki
przeglądarkowej.

## Audyt i monitoring

Każde wywołanie zapisuje strukturalne zdarzenie zawierające: wersję kontraktu,
correlation ID, operację, klasę aktora, pseudonimizowany identyfikator User,
`public_id` albo jego jednokierunkowy skrót dla odmowy, liczbę wyników, czas,
wynik i zaufany kod przyczyny. Nie zapisuje e-maila User, nazw Customer,
wewnętrznego `Naprawa.name`, pełnego cursoru, pól read modelu ani przyszłych
tokenów. Logi są dostępne wyłącznie operatorom i mają ustaloną przed produkcją
retencję; decyzja retencyjna nie rozszerza API.

Metryki bez etykiet PII obejmują co najmniej:

- liczbę wywołań i opóźnienie według operacji/wyniku;
- `AUTH_REQUIRED`, `NOT_FOUND`, `INVALID_CURSOR` i błędy zależności;
- liczbę odrzuceń IDOR widoczną tylko po stronie zaufanej;
- brakujące mapowanie statusu, brak/duplikat `public_id` i awarie audytu;
- liczbę stron i rozmiar wyniku, bez identyfikatorów rekordów.

Alert krytyczny uruchamia duplikat/brak `public_id`, nieznany status albo wzrost
błędów autoryzacji. Correlation ID wraca w nagłówku/obudowie błędu, aby support
mógł odnaleźć zdarzenie bez ujawniania szczegółów klientowi.

## Migracja, backfill i rollback

Zmiana jest wdrażana w dwóch kompatybilnych wydaniach, zawsze po backupie i
próbie na kopii site:

1. **Expand:** dodać nullable `public_id`, generator dla nowych `Naprawa` i patch
   backfillu; capability pozostaje wyłączona. Patch przetwarza rekordy partiami
   po stabilnym `name`, nadaje losową wartość tylko tam, gdzie jej brak, zapisuje
   checkpoint i raport liczników bez danych klienta. Ponowienie zachowuje już
   nadane ID.
2. **Verify:** sprawdzić `count(missing) = 0`, `count(distinct public_id) = count(*)`,
   format wszystkich wartości oraz próbkę odczytu. Kolizja zatrzymuje patch;
   nigdy nie nadpisuje istniejącego ID.
3. **Constrain:** w kolejnym migrate ustawić `unique: 1`, `read_only` i `no_copy`,
   sprawdzić obecność indeksu oraz uruchomić pełne testy. Dopiero potem readiness
   może dopuścić `account-read`.
4. **Dark/staff:** włączyć flagę na staging, następnie dla syntetycznych lub
   wewnętrznych kont na produkcji; porównać wyłącznie liczniki i kody wyników.
5. **Rollout:** stopniowo włączyć ruch, obserwując IDOR, błędy, latency i brakujące
   statusy. Nie włączać `token-read` ani mutacji w ramach tego rollout.

Rollback wyłącza flagę i usuwa `account-read` z `features`; Desk i workflow
pozostają bez zmian. Rollback kodu **nie usuwa** pola, indeksu ani nadanych
`public_id` i nie uruchamia downgrade patcha danych. Ponowne wdrożenie wykorzysta
te same identyfikatory. Dopiero osobna, zatwierdzona migracja po okresie retencji
mogłaby usunąć nieużywany schemat.

## Wymagane testy akceptacyjne

Przed włączeniem `account-read` muszą przejść co najmniej:

1. flaga brak/false daje `features: []`; true bez gotowego backfillu lub mapy
   statusów także fail-closed; dopiero pełna gotowość ogłasza `account-read`;
2. Guest, wyłączony User i System User (również sztucznie wpisany do
   `Customer.portal_users`) nie listują ani nie pobierają napraw;
3. Website User A widzi naprawy wszystkich i tylko Customer powiązanych z jego
   wierszami `portal_users`; rola `Customer` bez wiersza nie wystarcza;
4. parametr lub cursor pochodzący od użytkownika B nie rozszerza zakresu A;
5. A otrzymuje identyczną publiczną odpowiedź `NOT_FOUND` dla naprawy B,
   nieistniejącego losowego ID, poprawnie wyglądającego ID i `Naprawa.name`;
6. zmiana `portal_users` pomiędzy stronami unieważnia cursor i natychmiast odbiera
   dostęp; usunięcie powiązania przed get także odbiera dostęp;
7. paginacja nie duplikuje ani nie pomija rekordów na stabilnym fixture, respektuje
   limit 1 i 50 oraz odrzuca 0, 51, float, cursor zmieniony/wygasły/cudzego konta;
8. read model ma dokładnie allowlistę i nie zawiera danych wrażliwych także po
   dodaniu syntetycznego pola do dokumentu;
9. każdy obecny status mapuje się jawnie; status spoza mapy zamyka odczyt,
   emituje metrykę i nie ujawnia surowej wartości;
10. nowe rekordy dostają różne poprawnie sformatowane ID, kopia nie dziedziczy ID,
    ręczna zmiana jest blokowana, a backfill jest idempotentny;
11. fixture kolizji lub brakującego ID zatrzymuje readiness i rollout; raport nie
    zawiera PII ani wewnętrznych nazw napraw;
12. pozytywne i odmowne logi nie zawierają e-maila, Customer, cursoru, payloadu
    read modelu ani tracebacka;
13. dotychczasowy pełny zestaw `kuck_serwis`, workflow i Karta klienta dla Desk
    pozostają zielone przy fladze wyłączonej i włączonej;
14. test konsumencki `kuck_shop` potwierdza ścisłe `kuck-serwis/v1`, rewizję 1,
    `account-read` oraz kontrolowane `DEPENDENCY_UNAVAILABLE` bez fallbacku.

Macierz IDOR ma używać wyłącznie syntetycznych User A/B, Customer A/B, napraw A/B
i losowych identyfikatorów. Testy bezpośrednie kontraktu powinny trafić do
`kuck_serwis.tests.test_public_contract_v1`; testy gateway pozostają po stronie
`kuck_shop`.

## Konsekwencje i nierozstrzygnięte kwestie

- `public_id` rozwiązuje ekspozycję przewidywalnej nazwy, ale bezpieczeństwo nadal
  zależy od koniunkcyjnej kontroli `Customer.portal_users` przy każdym odczycie.
- V1 nie udostępnia zdjęć, ponieważ obecny model nie ma widoczności ani
  kontrolowanego downloadu.
- Publiczne etykiety statusów są obecnie polskie. Wersjonowana lokalizacja EN
  wymaga osobnego kontraktu lub jawnego parametru locale i nie może zwracać
  wewnętrznych tłumaczeń przypadkiem.
- Retencja audytu i progi alertów wymagają uzgodnienia operacyjnego przed
  produkcją, lecz capability może pozostać bezpiecznie wyłączona.
- Token-read i mutacje wymagają osobnych decyzji bezpieczeństwa. Ten ADR nie
  wybiera mechanizmu tokenów, step-up ani idempotencji komend.
- D06 pozostaje nierozstrzygnięta; nie jest potrzebna do implementacji ciemnego
  adaptera, lecz blokuje końcową akceptację ścieżki konta i jej UX.

Status ADR może zmienić się na `accepted` dopiero po migracji dwuetapowej,
zielonej macierzy IDOR, testach kontraktowych obu aplikacji, przeglądzie logów pod
kątem PII i zatwierdzonym, kontrolowanym włączeniu `account-read`.
