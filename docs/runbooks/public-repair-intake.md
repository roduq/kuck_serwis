# Runbook — zdalne zgłoszenia i naprawy

## Model danych i bezpieczeństwo

- Formularz: `/serwis/zglos-naprawe`.
- Kolejka Desk: `Kuck Repair Intake`, status domenowy `Nowe`.
- Portal konta: `/konto/naprawy` pokazuje zgłoszenia przed i po konwersji.
- Widok pojedynczego zgłoszenia: `/serwis/zgloszenie/<rqi_...>` dla konta lub gościa z ważnym grantem.
- `Naprawa` powstaje wyłącznie po fizycznym otrzymaniu zegarka i operatorskim potwierdzeniu.
- Grant gościa jest hashowany, wygasa po 30 dniach i może zostać odwołany. Surowy token występuje tylko w fragmencie linku, nie w bazie, query stringu, logach ani analityce.

## Obsługa zgłoszenia

1. Otwórz `Nowe zgłoszenia online`. Sprawdź kontakt, zegarek, opis, zdjęcia, gwarancję, sposób przekazania/odbioru, adres i wartość do 10 000 PLN.
2. Odszukaj istniejącego `Customer` lub utwórz go po niezależnym potwierdzeniu tożsamości. Nigdy nie łącz kartoteki tylko po podobnym e-mailu lub telefonie.
3. Ustaw rodzaj naprawy. Dla gwarancji producenta zachowaj informację, że klient opłaca transport do Kuck, a Kuck przekazuje zegarek dalej na koszt producenta.
4. Wybierz `Zatwierdź do procesu i płatności`. Dopiero ta akcja udostępnia płatność wejściową. Zgłoszenie nie może być zatwierdzone bez Customer i kompletnego adresu dla COURIER.
5. Dla SALON nie ma etykiety wejściowej. Dla COURIER klient opłaca aktualną wycenę Apaczki przez Tpay i nadaje paczkę w punkcie. Adres odbioru: Kuck, ul. 11 Listopada 8, 10-104 Olsztyn.
6. Po fizycznym otrzymaniu wybierz `Utwórz naprawę po przyjęciu` i potwierdź checkbox. Klient otrzyma jedno transakcyjne powiadomienie o utworzeniu naprawy.
7. Spam/omyłkę odrzuć przyciskiem `Odrzuć zgłoszenie` z krótkim powodem. Nie usuwaj intake ręcznie.

Ponowienie akcji przyjęcia zwraca tę samą naprawę i nie wysyła drugiego e-maila. Konflikt rewizji oznacza zmianę przez innego operatora — odśwież dokument.

## Wycena i wynik gwarancji

- `Wyślij wycenę` ustawia `AWAITING_QUOTE_DECISION`. Klient akceptuje lub odrzuca w panelu z aktualną rewizją.
- Akceptacja zapisuje zgodę i używa workflow `Akceptacja klienta`. Nie rozpoczynaj naprawy bez niej.
- Odrzucenie używa `Klient rezygnuje` i otwiera zwrot, bez obowiązkowego kontaktu manualnego.
- Pole `Wynik gwarancji` ustawia serwis: `Uzasadniona`, `Nieuzasadniona` lub `Przekazana producentowi`. Deklaracja klienta nie wystarcza do darmowego zwrotu.

## Koszty dla klienta

| Sytuacja | Koszt |
|---|---:|
| Przesyłka klienta do Kuck, każdy rodzaj | aktualna etykieta opłacona przez klienta |
| Zwrot po zwykłej naprawie | naprawa + 20 PLN, jedna płatność |
| Odrzucenie wyceny/zgłoszenia po dostarczeniu | 20 PLN Tpay lub 25 PLN COD |
| Nieuzasadniona gwarancja | 20 PLN Tpay lub 25 PLN COD |
| Uzasadniona gwarancja | 0 PLN |
| Gwarancja producenta przekazana dalej | przekazanie na koszt producenta; klient płaci tylko transport do Kuck |
| Odbiór w salonie | bez etykiety zwrotnej |

## Przesyłki i statusy

- `INBOUND_LABEL_READY`: etykieta jest dostępna dla klienta, ale zegarek nie jest jeszcze przyjęty.
- `AWAITING_PHYSICAL_RECEIPT`: tracking potwierdził dostarczenie, lecz operator nadal musi przyjąć zegarek fizycznie.
- `RETURN_LABEL_READY`: zwrot jest przygotowany. `COMPLETED` wymaga doręczenia zwrotu lub wydania w salonie.
- Timeout `order_send` po wysłaniu oznacza `REVIEW_REQUIRED`; nigdy nie wykonuj ślepego retry create. Retry dotyczy tylko odczytu etykiety i trackingu.
- Etykiety są prywatnymi `File` i pobierane wyłącznie przez scope konta/grantu.

## Powiadomienia

Transakcyjne e-maile obejmują: zgłoszenie z linkiem, zatwierdzenie, etykietę, utworzenie naprawy, wycenę i zwrot. Nie są zgodą marketingową. Przełączniki SMS/e-mail statusów pozostają niezależne i domyślnie wyłączone.

## Monitoring i incydenty

Logi nie mogą zawierać tokenów, CRC, Tpay `tr_id`, adresów, e-maili, telefonów, payloadów, etykiet ani tracebacków. Obserwuj:

- zaległe `Nowe` i zgłoszenia bez Customer;
- płatności oczekujące i niepełne;
- `CREATE_UNCERTAIN`, `REVIEW_REQUIRED`, stare przesyłki i brak etykiet;
- duplikaty/konflikty idempotencji i rewizji;
- błędy powiadomień i uprawnień Guest/A/B.

Przy nadużyciu zachowaj dowody, wyłącz publiczne zapisy i flagi providerów. Nie kasuj historii ani danych ad hoc.

## Wdrożenie i rollback

1. Sprawdź release i obecne różnice wszystkich aplikacji. Wykonaj pełny backup.
2. Wdróż obie aplikacje, uruchom `bench --site <site> migrate` i build. Migracja dodaje public ID intake, backfill i nowe DocType płatności/przesyłek.
3. Włącz `enable_kuck_serwis_intake_read`, `enable_kuck_serwis_remote` i `enable_kuck_repair_shipping` dopiero po zielonym preflightu. Skonfiguruj Tpay i Apaczkę przez istniejące secret-managed settings.
4. Uruchom syntetyczne: Guest POST, podgląd konta i link gościa, akceptację serwisu, Tpay test, etykietę testową, decyzję, zwrot 20/25 i gwarancję 0. Nie wykonuj przypadkowych produkcyjnych płatności/przesyłek.
5. Sprawdź desktop/mobile, CSP, brak tokenów/PII w odpowiedziach i logach. Realny smoke finansowo-fizyczny wymaga osobnej autoryzacji, limitu kosztu i planu anulowania.

Rollback wyłącza flagi i przywraca obraz. Nie usuwa DocType, intake, płatności, przesyłek, plików ani audytu.
