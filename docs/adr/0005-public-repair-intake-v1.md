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
   od 0,01 do 10 000 PLN. Płatność i etykieta kurierska nie należą do v1.
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
8. Rewizja informacji o prywatności v1 to `2026-08-28-v2`, a źródłem jest
   opublikowana strona Kuck `https://kuck.pl/pl/content/aeu-legal-privacy`;
   rekord przechowuje rewizję i domenowo związany SHA-256 dowodu, a nie treść
   zgody.
9. Publiczna trasa wymusza własny CSP: zasoby i połączenia tylko same-origin,
   `object-src 'none'`, kontrolowane `base-uri`, `form-action` i
   `frame-ancestors`. Tymczasowe `unsafe-inline` pozostaje wyłącznie dla
   zgodności z inline bootstrappingiem Frappe v16; usunięcie go wymaga
   frameworkowego nonce albo przebudowy bazowego szablonu.

10. Formularz może zawierać od 0 do 3 zdjęć zegarka. Jeden końcowy request
    `multipart/form-data` przenosi JSON i pliki bez wcześniejszego uploadu.
    Pojedynczy plik ma najwyżej 5 MiB; przyjmowane są rzeczywiste kontenery
    JPEG, PNG i WebP. Świeży proces dekodera weryfikuje pełny kontener i jedną
    klatkę, ogranicza wejście do 4096 px na bok i 16 MP, usuwa metadane,
    uwzględnia orientację EXIF i zapisuje wyłącznie znormalizowany JPEG.
11. Wszystkie zdjęcia są prywatnymi, dokładnie przypiętymi rekordami `File`.
    Intake przechowuje niezmienny porządek i manifest hashy; fingerprint
    idempotencji obejmuje również uporządkowany komplet zdjęć. Odpowiedź
    publiczna nie ujawnia URL-i, nazw ani identyfikatorów plików.
12. Przy kontrolowanej konwersji do `Naprawa` powstają prywatne kopie załączników
    w tej samej kolejności. Snapshot intake pozostaje niezmieniony. Brak skanera
    AV jest zapisywany jawnie jako `NOT_SCANNED`; normalizacja nie może być
    przedstawiana jako skan antywirusowy.

## Granice i konsekwencje

- Intake jest prośbą o rozpoczęcie obsługi, nie potwierdzeniem przyjęcia zegarka,
  wyceną, terminem, ubezpieczeniem ani zleceniem przewozu.
- Konto ułatwia bezpieczne powiązanie, ale nowe zgłoszenie nie staje się widoczną
  naprawą do chwili kontrolowanej konwersji.
- Publiczny odczyt zdjęć i guest-token read pozostają poza zakresem. Zdjęcia
  widzą wyłącznie role `Serwis` i `System Manager` w Desk.
- Rozszerzenie dostępu do plików wymaga osobnej decyzji o AV i retencji. Do tego
  czasu prywatny snapshot intake nie jest automatycznie usuwany.
- Automatyczne czyszczenie intake nie wchodzi do pierwszego pionu. Rekomendacja
  operacyjna: przegląd po 180 dniach dla rekordów odrzuconych i nieprzyjętych;
  purge dopiero po zatwierdzeniu polityki, legal hold i zgodności backupów.
- Samo podanie telefonu lub e-maila nie jest opt-inem do powiadomień statusowych;
  konwersja pozostawia oba kanały wyłączone do osobnej, jawnej decyzji klienta.

## Rollout i rollback

Rollout wymaga migracji addytywnej, testów contract/API/Frappe, próby Guest i
Website User, kontroli mobilnej oraz backupu produkcyjnego. Rollback aplikacji
nie usuwa tabeli ani zgłoszeń. W razie incydentu usuwa się link do formularza i
blokuje metodę na reverse proxy/aplikacji; danych nie kasuje się ad hoc.
