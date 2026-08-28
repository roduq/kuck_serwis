# ADR 0005 — publiczne zgłoszenie naprawy v1

Status: **ACCEPTED**

Data: 2026-08-28

## Kontekst

Panel klienta umożliwia odczyt istniejących napraw, ale nie może przyjmować
nowej naprawy bez utworzenia `Customer`. Gość nie ma zweryfikowanej relacji z
kartoteką klienta, a rekord `Naprawa` ze statusem `Przyjęto` oznacza fizyczne
przyjęcie zegarka. Publiczny formularz nie może więc bezpośrednio tworzyć
`Naprawa` ani przyznawać prawa odczytu na podstawie adresu e-mail.

## Decyzja

1. `kuck_serwis` udostępnia trasę `/serwis/zglos-naprawe` dla gościa i
   zalogowanego użytkownika oraz prywatny DocType `Kuck Repair Intake`.
2. Formularz zbiera dane kontaktowe, identyfikację i stan zegarka, opis usterki,
   deklarację gwarancji, sposób dostarczenia/odbioru oraz — dla wysyłki — wartość
   od 0,01 do 10 000 PLN. Zdjęcia, płatność i etykieta kurierska nie należą do v1.
3. Publiczny zapis jest metodą POST, używa CSRF powiązanego z sesją/cookie,
   ścisłego originu, limitu rozmiaru i częstotliwości, honeypotu oraz klucza
   idempotencji związanego z aktorem. Odpowiedź sukcesu jest neutralna i nie
   zawiera wewnętrznego identyfikatora.
4. Gość nigdy nie jest wiązany z `Customer` po e-mailu. Zalogowany aktywny
   `Website User` jest wiązany tylko wtedy, gdy istnieje dokładnie jedna relacja
   `Customer.portal_users`; brak lub wieloznaczność pozostawia intake bez
   klienta.
5. Intake nie ma uprawnień Guest/Website User ani publicznego endpointu odczytu.
   Dostęp Desk mają role `Serwis` i `System Manager`.
6. Operator przypisuje lub potwierdza `Customer`, rodzaj naprawy i dopiero po
   zaznaczeniu fizycznego przyjęcia tworzy jedną `Naprawa`. Przejście jest
   transakcyjne, blokowane wierszem, idempotentne i chronione rewizją `modified`.
7. Snapshot zgłoszenia i dowody bezpieczeństwa są po zapisie niemutowalne.
   Odrzucenie wymaga powodu i jest kontrolowanym przejściem.
8. Rewizja informacji o prywatności v1 to `2026-08-25-v1`; rekord przechowuje
   rewizję i domenowo związany SHA-256 dowodu, a nie treść zgody.

## Granice i konsekwencje

- Intake jest prośbą o rozpoczęcie obsługi, nie potwierdzeniem przyjęcia zegarka,
  wyceną, terminem, ubezpieczeniem ani zleceniem przewozu.
- Konto ułatwia bezpieczne powiązanie, ale nowe zgłoszenie nie staje się widoczną
  naprawą do chwili kontrolowanej konwersji.
- Upload zdjęć oraz guest-token read wymagają osobnych threat modeli i ADR.
- Automatyczne czyszczenie intake nie wchodzi do pierwszego pionu. Rekomendacja
  operacyjna: przegląd po 180 dniach dla rekordów odrzuconych i nieprzyjętych;
  purge dopiero po zatwierdzeniu polityki, legal hold i zgodności backupów.

## Rollout i rollback

Rollout wymaga migracji addytywnej, testów contract/API/Frappe, próby Guest i
Website User, kontroli mobilnej oraz backupu produkcyjnego. Rollback aplikacji
nie usuwa tabeli ani zgłoszeń. W razie incydentu usuwa się link do formularza i
blokuje metodę na reverse proxy/aplikacji; danych nie kasuje się ad hoc.

