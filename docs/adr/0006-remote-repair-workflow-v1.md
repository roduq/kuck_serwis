# ADR 0006 — zdalny workflow zgłoszenia i naprawy v1

Status: accepted for implementation; aktywacja providerów poza `kuck_serwis`

Data: 2026-11-09

## Decyzja

`kuck_serwis` pozostaje właścicielem zgłoszenia, naprawy, klienta, kosztów i
wszystkich decyzji domenowych. Rozszerza publiczny kontrakt o podgląd intake,
bezpieczny grant gościa, zatwierdzanie zdalnego przyjęcia, decyzje wyceny,
powiadomienie o utworzeniu naprawy oraz snapshoty wymagane przez `kuck_shop` do
Tpay i Apaczki. Integracja sklepu jest opisana w jego ADR 0036.

## Reguły Kuck

- Serwis akceptuje zgłoszenie przed udostępnieniem płatności.
- Limit deklarowanej wartości wynosi 10 000 PLN; nadanie odbywa się w punkcie.
- Adres odbioru: Kuck, ul. 11 Listopada 8, 10-104 Olsztyn.
- Każdy klient opłaca przesyłkę do Kuck, także dla gwarancji naszej naprawy i
  gwarancji producenta.
- Po zwykłej naprawie klient płaci naprawę + 20 PLN zwrotu jedną kwotą.
- Po odrzuceniu zgłoszenia/wyceny lub nieuznaniu gwarancji zwrot kosztuje 20 PLN
  z góry/przelewem albo 25 PLN za pobraniem.
- Uzasadniona gwarancja ma zwrot na koszt Kuck. Gwarancja producenta jest
  przekazywana dalej na koszt producenta; klient niezgadzający się na dostarczenie
  do Kuck kontaktuje się z producentem samodzielnie.

## Publiczny kontrakt i bezpieczeństwo

Intake otrzymuje losowy, niezmienny `public_id`, backfill i unikalny indeks.
Kontrakt account-intake-read zwraca wyłącznie zgłoszenia aktualnego Website User
lub przypisanego Customer. Publiczny model zawiera public ID, status, markę/model,
datę, sposób dostarczenia/odbioru i dozwolone akcje; nie zawiera RIN, NAP,
Customer/User, kontaktu, adresu, opisu, numeru seryjnego ani plików.

Grant gościa jest hashowany, wygasający, odwoływalny i ograniczony do jednego
intake. Surowy token nie jest utrwalany ani logowany. Operacje finansowe wymagają
aktualnego scope, CSRF, expected revision i request ID. Obca i nieistniejąca
naprawa/intake mają identyczne `NOT_FOUND`.

## Zmiany domenowe

Operator zatwierdza klienta, rodzaj i adres przed płatnością. Fizyczne przyjęcie
nadal jest jedynym momentem utworzenia `Naprawa`. Jego utworzenie wywołuje jedno
transakcyjne powiadomienie do klienta z linkiem do właściwego widoku, niezależne
od newslettera i domyślnie wyłączonych powiadomień statusowych.

Koszt zwrotu jest liczony serwerowo z rodzaju i wyniku naprawy, nigdy z kwoty
przekazanej przez browser. Gwarancja pozytywna daje 0 PLN; zwykła naprawa dolicza
20 PLN do wyceny; odrzucenie lub gwarancja negatywna daje wybór 20 PLN online albo
25 PLN COD. Akceptacja wyceny prowadzi do `IN_REPAIR`, a płatność naprawy i zwrotu
odblokowuje dopiero stan `Gotowe do odbioru`. Odrzucenie przed dostarczeniem nie
tworzy zwrotu; po dostarczeniu operator najpierw potwierdza fizyczne przyjęcie,
a `Anulowano` otwiera sam zwrot 20/25 PLN bez naliczania wyceny. Publiczne komendy
nie ustawiają bezpośrednio pola `status`; używają kontrolowanych przejść i workflow.

## Integracja i niezawodność

`kuck_shop` przechowuje płatności, shipmenty i prywatne etykiety. Serwis wystawia
minimalne, wersjonowane komendy/snapshoty i przyjmuje potwierdzone wyniki.
Każda mutacja ma request ID, expected revision, idempotentny replay, konflikt
payloadu i sanitizowany audyt. Płatność z redirectu, tracking `DELIVERED` i
sukces HTTP same w sobie nie zmieniają stanu domenowego.

Logi nie zawierają danych kontaktowych/adresowych, tokenów, identyfikatorów Tpay,
payloadów, treści powiadomień ani etykiet. Testy obejmują IDOR, gościa, wyceny,
gwarancje, duplikaty, konflikty rewizji, powiadomienie o nowej naprawie i brak
utworzenia naprawy przed fizycznym przyjęciem.
