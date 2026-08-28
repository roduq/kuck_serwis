# Runbook — publiczne zgłoszenia napraw

## Zakres

- Publiczny formularz: `/serwis/zglos-naprawe`.
- Kolejka Desk: `Kuck Repair Intake`, domyślnie status `Nowe`.
- Docelowy rekord warsztatowy: `Naprawa`, tworzony dopiero po fizycznym
  otrzymaniu zegarka.

## Obsługa zgłoszenia

1. Otwórz `Nowe zgłoszenia online` w obszarze Kuck Serwis.
2. Zweryfikuj kontakt, zegarek, opis, gwarancję i sposób przekazania/odbioru.
3. Odszukaj istniejącego `Customer` lub utwórz go dopiero po niezależnym
   potwierdzeniu tożsamości zgodnie z procedurą serwisu. Nigdy nie twórz ani nie
   łącz kartoteki Guest wyłącznie na podstawie podobnego e-maila lub telefonu.
4. Ustaw sugerowany rodzaj naprawy. Dla wysyłki wartość musi być dodatnia i nie
   może przekroczyć 10 000 PLN.
5. Gdy zegarek fizycznie dotrze, wybierz `Utwórz naprawę po przyjęciu`, potwierdź
   checkbox przyjęcia i otwórz utworzony rekord `Naprawa`.
6. Jeśli zgłoszenie jest omyłkowe lub spamowe, użyj `Odrzuć zgłoszenie` i podaj
   krótki powód. Nie usuwaj ręcznie rekordu intake.

Ponowienie akcji przyjęcia zwraca tę samą naprawę. Konflikt rewizji oznacza, że
drugi operator zmienił rekord — odśwież formularz i ponownie oceń stan.

## Oczekiwane zachowanie klienta

- Sukces nie pokazuje numeru ani linku do prywatnych danych. Klient otrzymuje
  informację, by nie wysyłać zegarka przed instrukcją.
- Formularz nie generuje etykiety, płatności ani zlecenia Apaczka.
- Telefon i e-mail służą do kontaktu w sprawie intake; konwersja nie włącza
  automatycznie powiadomień SMS/e-mail o statusach naprawy.
- Gość nie zobaczy zgłoszenia w portalu. Zalogowany klient zobaczy standardową
  naprawę dopiero po bezpiecznym powiązaniu i konwersji.

## Monitoring i incydenty

Log `repair_intake` zawiera wyłącznie kod zdarzenia, klasę źródła i informację,
czy konto było bezpiecznie powiązane; nie powinien zawierać PII, payloadu ani
identyfikatora intake. Obserwuj:

- wzrost odrzuceń HTTP/rate limit;
- błędy insertu lub konflikty idempotencji;
- zaległe rekordy `Nowe` i zgłoszenia bez `Customer`;
- błędy konwersji do `Naprawa`.

Przy podejrzeniu nadużycia najpierw zachowaj dowody i czas zdarzenia, następnie
czasowo wyłącz publiczną metodę lub trasę. Nie publikuj identyfikatorów, payloadów
ani danych kontaktowych w tickecie i logach. Nie uruchamiaj purge bez osobnej
zatwierdzonej procedury.

## Wdrożenie

1. Zweryfikuj dokładny obraz/release wszystkich aplikacji site.
2. Wykonaj backup bazy i plików.
3. Wdróż `kuck_serwis`, uruchom `bench --site <site> migrate` i przebuduj assets.
4. Uruchom smoke: GET formularza, jeden syntetyczny Guest POST, kontrola kolejki,
   konwersja testowego intake po potwierdzeniu przyjęcia i kontrola uprawnień.
5. Sprawdź stronę na 390 px i desktopie oraz potwierdź brak ID/PII w odpowiedzi i
   logach. Syntetyczny rekord oznacz i odrzuć; nie twórz wysyłki ani płatności.
6. Potwierdź na dokładnej trasie nagłówek `Content-Security-Policy`, w tym
   `default-src 'self'`, `object-src 'none'`, `frame-ancestors 'none'` i
   `form-action 'self'`; brak nagłówka zatrzymuje rollout.

Rollback kodu pozostawia addytywny DocType i dane. Nie cofaj migracji przez
kasowanie tabeli; przywróć poprzedni obraz, pozostaw metodę publiczną wyłączoną i
zachowaj rekordy do pojednania.
