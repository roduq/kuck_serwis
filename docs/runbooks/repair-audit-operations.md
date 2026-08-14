# Operacje trwałego audytu odczytu napraw

Status: kontrakt proponowany; `account-read` pozostaje wyłączone do zatwierdzenia
decyzji z sekcji „Minimalne decyzje” i spełnienia całej bramki gotowości.

Zakres: trwały sink `Kuck Repair Audit Event` z rewizji `b9fb74a`, używany przez
`kuck_serwis.public_contract.v1`. Dokument nie zmienia publicznego API, schematu
ani polityki biznesowej. Każda implementacja nowego DocType, indeksu lub zmiany
semantyki zdarzenia wymaga osobnego ADR i migracji expand/verify/constrain.

## Niezmienniki bezpieczeństwa

- Zdarzenie pozostaje append-only od przyjęcia do kontrolowanego purge. Aplikacja,
  Desk, import, rename i zwykłe `delete_doc` nie mogą go edytować ani usuwać.
- Sink przyjmuje wyłącznie obecną allowlistę pól. Nie zapisuje User, Customer,
  `Naprawa.name`, cursora, odpowiedzi portalu, tokenu, tracebacka ani sekretu.
- Publiczne wywołanie zwraca dane tylko wtedy, gdy `emit()` zwróci literalne
  `True` po commit na izolowanym połączeniu. Timeout, wyjątek, brak połączenia,
  niejednoznaczny wynik lub konflikt replay kończy się
  `DEPENDENCY_UNAVAILABLE`; logger rotacyjny nigdy nie zastępuje trwałego ACK.
- Purge, probe, metryki i eksporty nie korzystają z endpointu portalu i nie
  rozszerzają `features`. Nie przyjmują identyfikatorów klienta z HTTP.
- Wszystkie czasy, cutoffy, holdy i metryki są liczone w UTC. Etykiety metryk
  mają zamkniętą, niskokardynalną allowlistę i nie zawierają hashy, correlation
  ID, nazw dokumentów ani komunikatów wyjątków.
- Sama pseudonimizacja HMAC nie anonimizuje zdarzeń. Retencja i legal hold nadal
  podlegają zatwierdzonej polityce ochrony danych.

## Warianty retencji do zatwierdzenia

Każdy wariant liczy wiek od `creation` zdarzenia i usuwa rekord po upływie pełnej
liczby dni. Zmiana okresu działa tylko naprzód: skrócenie wymaga zatwierdzonego
dry-run, a wydłużenie oceny celu i podstawy prawnej.

| Wariant | Online | Archiwum audytu | Zaleta | Koszt / ryzyko |
|---|---:|---:|---|---|
| A — minimalny | 90 dni | brak | najmniejsza ekspozycja i koszt | krótsze okno dochodzeniowe |
| B — zrównoważony **(rekomendowany)** | 180 dni | brak | obejmuje typowe dochodzenia i sezonowość bez drugiej kopii | większy zbiór niż A; wymaga potwierdzenia podstawy i pojemności |
| C — rozszerzona forensics | 90 dni | 365 dni w szyfrowanym, immutable/WORM archiwum | długie okno dochodzeniowe poza bazą operacyjną | osobne klucze, koszty, procedura restore i większy zakres compliance |

Rekomendacją startową jest B. Nie tworzy dodatkowego magazynu i zachowuje
rozsądne okno analizy incydentu. Jest rekomendacją techniczną, nie decyzją:
właściciel biznesowy i osoba odpowiedzialna za ochronę danych muszą zatwierdzić
cel, okres oraz zgodność backupów. Jeżeli brak takiego zatwierdzenia, readiness
pozostaje `false`.

Backup nie jest ukrytym archiwum. Okres przechowywania backupów zawierających tę
tabelę nie może przekraczać zatwierdzonego okresu bez odrębnej decyzji. Wariant C
wymaga ponadto jawnego RPO/RTO, właściciela klucza i testu kasowania archiwum.

## Legal hold

Legal hold ma pierwszeństwo przed retencją, ale nie włącza capability. Rejestr
holdów jest niedostępny w Desk i przechowuje wyłącznie: losowy `hold_id`, stan,
UTC `from`/`to` (jedna z granic może być otwarta), opcjonalną zamkniętą listę
correlation ID, numer sprawy poza systemem, zatwierdzającego, daty utworzenia i
zwolnienia. Nie zapisuje opisu sprawy ani danych osoby.

Do implementacji rekomendowany jest osobny, bez-permisyjny DocType operacyjny z
unikalnym `hold_id`; jest to zmiana schematu wymagająca ADR. Do czasu jego
wdrożenia każdy aktywny hold oznacza globalne wstrzymanie purge. Brak dostępu do
rejestru, niepoprawny przedział albo niejednoznaczny stan również zatrzymuje
purge i podnosi alert — nigdy nie oznacza „brak holda”. Hold może utworzyć lub
zwolnić wyłącznie zatwierdzający compliance; operator uruchamiający purge nie
może sam zwolnić holda. Zwolnienie pozostawia niemutowalny ślad zatwierdzenia.

## Kontrakt purge

### Interfejs i harmonogram

Dedykowana funkcja utrzymaniowa powinna przyjmować:

```text
purge_repair_audit_events(
  dry_run=True,
  as_of_utc=None,
  retention_days=<zatwierdzona wartość>,
  batch_size=1000,
  max_batches=20
)
```

`dry_run=True` jest wartością domyślną. Tryb zapisu wymaga jawnego argumentu
`dry_run=False` oraz konfiguracji retencji o statusie zatwierdzonym. Parametry są
walidowane: `retention_days > 0`, `1 <= batch_size <= 5000`,
`1 <= max_batches <= 100`. Scheduler uruchamia purge raz dziennie w kolejce
`long`, przez `scheduler_events["cron"]`, z jednym stałym `job_id` per site i
deduplikacją. Ręczne uruchomienie stosuje ten sam kod, nie SQL ad hoc.

### Algorytm

1. Na początku przechwyć jedno `as_of_utc` i policz niezmienny
   `cutoff = as_of_utc - retention_days`. Nie używaj ruchomego `NOW()` pomiędzy
   batchami.
2. Pobierz i zweryfikuj aktywne holdy. Jakikolwiek błąd kończy przebieg bez
   usuwania. Wyklucz rekordy objęte przedziałem lub correlation ID.
3. W porządku `creation ASC, name ASC` wybierz najwyżej `batch_size` nazw z
   warunkiem `creation < cutoff`. Nigdy nie używaj offsetu. Indeks złożony
   `(creation, name)` jest warunkiem produkcyjnego rollout i wymaga migracji.
4. Dry-run wykonuje te same selekcje i liczniki, ale zero `DELETE` i zero
   `COMMIT`; raportuje tylko liczbę kwalifikowanych, wstrzymanych i batchy oraz
   min/max datę UTC. Nie raportuje nazw, hashy ani correlation ID.
5. Tryb zapisu usuwa dokładnie nazwy wybrane w danym batchu przez prywatny port
   utrzymaniowy i parametryzowane zapytanie. To jedyny świadomy wyjątek od
   kontrolera append-only; nie może być whitelisted ani dostępny w Desk.
6. Commit następuje po każdym batchu. Po commit ponownie policz, czy wybrane nazwy
   zniknęły; niezgodność zatrzymuje job i alarmuje. Awaria przed commit powoduje
   rollback bieżącego batcha, wcześniejsze batch'e pozostają poprawnie usunięte.
7. Następny batch ponawia warunek `< cutoff`; dzięki temu restart i powtórzenie są
   idempotentne. Job kończy się po pustym batchu, `max_batches` albo budżecie czasu
   mniejszym niż timeout workera. Pozostały backlog obsłuży kolejny przebieg.
8. Zapisz sanitizowane podsumowanie przebiegu: run ID, dry-run/apply, cutoff,
   liczniki, czas, wynik i kod błędu z allowlisty. Nie loguj zapytań ani danych
   rekordów. Błąd nie może zostać zamieniony na sukces schedulera.

Pierwszy zapisowy przebieg w każdym środowisku wymaga: aktualnego backupu,
zatwierdzonego dry-run na tej samej konfiguracji, próbki maksymalnie jednego
batcha, pojednania liczników i obserwacji rozmiaru/locków bazy. Zmiana retencji
powtarza tę procedurę.

## Health probe i readiness

Probe ma dwie warstwy i nie wykonuje zapytania portalowego:

1. **Pasywna, co minutę:** sprawdza istnienie tabeli i wymaganych indeksów,
   połączenie izolowane, liczbę brakujących/duplikujących `public_id`, nieznane
   statusy, poprawność konfiguracji retencji/holdów, świeżość ostatniego purge i
   połączenie eksportera metryk.
2. **Aktywna, co pięć minut:** buduje syntetyczne zdarzenie zgodne z obecną
   allowlistą, bez PII, z losowym correlation ID i `actor_hash` wyprowadzonym
   domenowo z wartości stałej `health-probe`. Wysyła je przez dokładnie
   `DurableRepairAuditSink.emit()`, a po otrzymaniu literalnego `True` otwiera
   **nowe** izolowane połączenie i odczytuje dokładnie jeden, identyczny wiersz.
   Dopiero widoczność wszystkich pól po ponownym połączeniu dowodzi commit.
   Canary pozostaje append-only i podlega zwykłej retencji; agregator wyklucza je
   po wyliczanym lokalnie hash-u aktora, nigdy po etykiecie z danych.

Canary używa dokładnie: bieżących `event`, `contract` i `schema_revision`,
`operation=list`, `outcome=success`, `actor_class=unknown`, `result_code=OK`,
`count=0`, `repair_handle_hash=null` oraz zmierzonego `latency_ms`. Nie wolno
dodawać specjalnego pola do istniejącego eventu bez nowej rewizji schematu.

Probe nie zwraca danych eventu. Wynik ma zamknięty kształt: `ok`, UTC timestamp,
wersja probe oraz lista kodów kontrolnych z allowlisty. Nie zawiera wartości
konfiguracji, nazw tabel, wyjątków, hashy ani ID. HTTP health, jeśli powstanie,
może zwrócić tylko zbiorcze `ready: true|false`, wymaga uwierzytelnienia
operatora i nigdy nie jest źródłem ACK dla wywołania publicznego.

Stan ostatniego sukcesu musi być utrwalony w bazie, a nie tylko w Redis/cache.
Readiness w ścieżce requestu jest szybkim odczytem tego stanu; nie wykonuje
canary synchronicznie. Stan jest gotowy wyłącznie, gdy pasywny probe ma sukces
nie starszy niż 2 minuty, aktywny nie starszy niż 10 minut i żaden warunek
krytyczny nie jest aktywny. Brak/starość/niepoprawny stan oznacza `false`.
Utrwalenie tego stanu wymaga prywatnego DocType operacyjnego i ADR.

`_AUDIT_AND_MONITORING_READY` może zostać zastąpione dynamicznym checkiem dopiero
po spełnieniu wszystkich poniższych warunków:

- wybrano i zatwierdzono retencję, a purge przeszedł dry-run oraz zapisową próbę;
- rejestr holdów działa fail-closed, a test hold/release jest udokumentowany;
- oba probe są świeże i przetestowano ich realną awarię;
- metryki są odbierane, alert testowy dotarł do dyżuru i ma właściciela;
- backup/restore audytu przeszedł test, a lifecycle backupów jest zgodny z
  retencją;
- pozostałe warunki `_is_ready()` oraz flaga rollout są spełnione.

## Metryki i progi alertów

Metryki są licznikami/histogramami bez PII, z etykietami wyłącznie `site_id`
(nie nazwa hosta, jeśli jest wrażliwa), `operation`, `outcome`, `result_code` i
`probe_version`. Nie dodawać correlation ID, actor/repair hash, User, Customer,
URL, exception ani surowego statusu naprawy.

| Sygnał | Warning | Critical / skutek |
|---|---|---|
| `audit_sink_failure_total` | — | każdy przyrost w 5 min; readiness `false` natychmiast |
| aktywny probe | 1 porażka | 2 kolejne lub brak sukcesu 10 min; readiness `false` |
| pasywny probe | 1 porażka | 2 kolejne lub brak sukcesu 2 min; readiness `false` |
| brak/duplikat `public_id`, nieznany status | — | wartość > 0; readiness `false` |
| `DEPENDENCY_UNAVAILABLE` | >= 5 i > 1% wywołań przez 5 min | >= 20 lub > 5% przez 5 min |
| latency publicznego kontraktu | p95 > 500 ms przez 10 min | p95 > 1000 ms przez 5 min |
| zaufane odrzucenia IDOR | >= 10/5 min i > 3× mediana 7 dni | >= 50/5 min; incydent bezpieczeństwa |
| `AUTH_REQUIRED` + `INVALID_CURSOR` | > 3× mediana 7 dni przez 15 min, min. 30 | > 10× mediana, min. 100/15 min |
| purge | brak sukcesu 26 h albo rekord starszy niż cutoff + 24 h | brak sukcesu 48 h albo cutoff + 72 h |
| aktywny legal hold | informacja codzienna dla właściciela | hold po dacie przeglądu lub nieczytelny rejestr |
| pojemność tabeli | prognoza 30 dni do 80% przydziału | >= 90% przydziału |

`NOT_FOUND` samodzielnie nie jest sygnałem IDOR; alert IDOR używa wyłącznie
zaufanej klasyfikacji serwera. Progi należy skalibrować po 14 dniach dark/staff,
ale ich poluzowanie wymaga zatwierdzonej zmiany runbooka. Cisza metryk jest
awarią monitoringu, nie stanem zdrowym.

## RBAC i granica DBA

| Rola | Dozwolone | Niedozwolone |
|---|---|---|
| Runtime aplikacji | insert przez sink; odczyt własnego wyniku canary | update/delete, eksport, zmiana retencji/holda |
| Operator/SRE | agregaty, stan probe, dry-run, start zatwierdzonego purge | surowe eventy, zwolnienie holda, zmiana kodu polityki |
| Security Auditor | czasowo ograniczony, rejestrowany eksport minimalnego zakresu po numerze sprawy | stały Desk access, wyszukiwanie po User/Customer, modyfikacja |
| Compliance/DPO | zatwierdzenie retencji, utworzenie/zwolnienie holda, okresowy przegląd | bezpośredni SQL i wykonywanie purge |
| DBA | backup/restore, indeksy, awaryjne read-only zapytania i zatwierdzony purge | decyzja o celu/retencji/holdzie, użycie danych poza sprawą |

DocType nadal ma pustą listę permissions. Nie dodawać roli `System Manager` ani
`Serwis` tylko dla wygody. Dostęp uprzywilejowany jest just-in-time, wymaga ticketu,
MFA, minimalnego zakresu i osobnego audytu administracyjnego poza tą tabelą.
Eksport jest szyfrowany, ma termin usunięcia i nie trafia do repo, e-maila ani
logów CI. Operacje DBA nie mogą używać credentialu ujawnionego w diagnostyce;
jego rotacja pozostaje warunkiem środowiskowym.

## Backup i restore

- Standardowy szyfrowany backup site obejmuje tabelę audytu i prywatny rejestr
  operacyjny. Harmonogram, RPO/RTO i retencja backupu muszą odpowiadać wybranemu
  wariantowi; dostęp ma tylko zespół backup/DBA.
- Przed pierwszym purge, skróceniem retencji, migracją indeksu i rolloutem
  wykonaj pełny backup z checksumą. Nie zapisuj sekretów ani ścieżek zawierających
  dane dostępowe w outputcie testu.
- Co kwartał odtwórz backup do izolowanego site bez ruchu, maili, webhooków i
  schedulera. Sprawdź liczbę rekordów, unikalność `event_id`/`correlation_id`,
  losową syntetyczną próbkę, indeksy oraz działanie sinka/probe.
- Po restore najpierw wczytaj aktualne holdy i politykę, wykonaj dry-run purge, a
  potem usuń rekordy, których aktualna retencja już nie obejmuje. Dopiero po
  pojednaniu i świeżym health probe wolno włączyć `account-read`.
- Restore nie może ponownie wysłać eventów, alertów klientom ani danych do
  analityki. Wynik testu zawiera tylko liczniki i checksumy artefaktów.

## Rollout i rollback

1. **Implementacja wyłączona:** ADR dla prywatnego stanu operacyjnego, holdów i
   indeksu; kod, testy oraz scheduler przy stałym readiness `false`.
2. **Staging:** syntetyczne dane, dry-run i jedno-batchowy purge, aktywny canary,
   wymuszone awarie DB/queue/metryk, restore oraz test alert routing.
3. **Production dark:** deploy z `features: []`; co najmniej 24 h zdrowych probe,
   obserwacja tabeli i zerowy dry-run. Bez publicznego ruchu.
4. **Staff/canary:** flaga tylko dla zatwierdzonego site/okna; minimum 24 h,
   pojednanie liczby wywołań z eventami i sprawdzenie progów bez surowych danych.
5. **Stopniowy rollout:** 5% → 25% → 100%, minimum jedno okno obserwacji między
   etapami. Każdy etap wymaga świeżej bramki i właściciela rollbacku.

Rollback jest bezpieczny i pierwszorzędny: ustaw flagę rollout na `false`,
potwierdź `features: []`, zatrzymaj ruch konsumenta i zachowaj sink, probe, holdy,
purge oraz dane. Nie cofaj schematu, nie kasuj eventów i nie wyłączaj alertów.
Jeżeli awaria dotyczy purge, wyłącz wyłącznie scheduler purge; capability także
pozostaje wyłączone do pojednania. Rollback wersji kodu nie może uruchamiać
downgrade danych. Ponowne włączenie przechodzi od staging/dark odpowiedniego do
przyczyny incydentu.

## Wymagane testy, w tym negatywne

Przed zmianą readiness na dynamiczne muszą przejść co najmniej:

1. sink ACK dopiero po commit; timeout, rollback, zerwane połączenie, `False`,
   `1`, `None`, wyjątek i konflikt replay nie zwracają danych publicznych;
2. identyczny replay jest idempotentny, konflikt tego samego correlation ID nie;
3. probe nie przechodzi po samym `emit=True`: drugie połączenie musi zobaczyć
   identyczny wiersz; brak/więcej niż jeden/różnica pola daje fail-closed;
4. event canary nie ma PII, nie podbija metryk ruchu i podlega retencji;
5. brak/stary/uszkodzony stan probe, brak konfiguracji, brak indeksu, brak
   eksportera albo wyjątek checku daje readiness `false`;
6. dry-run przy każdej konfiguracji usuwa zero wierszy; raport nie zawiera ID,
   hashy, SQL ani wyjątków;
7. purge usuwa tylko `creation < cutoff`, respektuje granicę równą cutoff,
   batch size/max batches, kolejność i commit per batch;
8. powtórzenie po sukcesie usuwa zero; restart po awarii batcha usuwa tylko
   pozostałe kwalifikowane rekordy bez błędu i bez podwójnych liczników;
9. aktywny hold globalny, zakres czasu i correlation ID chronią rekord;
   nieczytelny/niepoprawny rejestr oraz wygaśnięte poświadczenie zatrzymują purge;
10. operator purge nie może utworzyć ani zwolnić holda; role `Serwis`, Website
    User, System Manager i Guest nie czytają DocType ani funkcji utrzymaniowych;
11. konkurencyjny insert na granicy cutoff nie jest usuwany; równoległe joby są
    deduplikowane, a utrata locka zatrzymuje bezpiecznie następny batch;
12. wymuszone alarmy sink/probe/IDOR/purge/pojemność docierają do dyżuru, nie
    zawierają PII i zamykają readiness tam, gdzie wskazano;
13. backup/restore zachowuje dokładne liczniki i constraints; restore starszego
    backupu nie włącza capability przed aktualnym purge i probe;
14. wyłączenie flagi natychmiast daje `features: []`, nie usuwa danych i nie
    przerywa ochrony hold/retencji;
15. pełna regresja `kuck_serwis` i kontrakt konsumenta pozostają zielone.

Testy używają wyłącznie syntetycznych eventów i oddzielnego site. Test purge
nigdy nie jest uruchamiany na produkcji bez zatwierdzonego dry-run i backupu.

## Minimalne decyzje potrzebne od użytkownika

Do rozpoczęcia implementacji bez włączania capability potrzebne są odpowiedzi:

1. Wariant retencji A, B albo C oraz zatwierdzający biznesowy/compliance.
2. Retencja backupów; dla C także 365 dni archiwum, właściciel klucza i RPO/RTO.
3. Kto może zatwierdzać i zwalniać legal hold oraz maksymalny termin przeglądu
   aktywnego holda.
4. Kanał dyżuru, właściciel alertów i godziny reakcji; czy podane progi są
   akceptowane jako wartości startowe.
5. Osoby/role uprawnione do Security Auditor i DBA oraz system ticketów/MFA.
6. Okna staging, dark, staff i produkcyjne oraz osoba uprawniona do rollbacku.

Do czasu zapisania tych decyzji i dowodu testów `_AUDIT_AND_MONITORING_READY`
pozostaje `False`, nawet jeśli sink zapisuje prawidłowo.

## Komendy weryfikacji dokumentu

Uruchamiane z repozytorium `apps/kuck_serwis`:

```bash
test -s docs/runbooks/repair-audit-operations.md
rg -n '^## ' docs/runbooks/repair-audit-operations.md
rg -ni '(password|passwd|secret|token|api[_ -]?key|encryption_key)\s*[:=]' docs/runbooks/repair-audit-operations.md
git diff --check -- docs/runbooks/repair-audit-operations.md
git diff --no-index /dev/null docs/runbooks/repair-audit-operations.md
```

Trzecia komenda ma zwrócić brak dopasowań. Ostatnia zwraca kod `1`, ponieważ
pokazuje oczekiwany diff nowego pliku; inne kody oznaczają błąd narzędzia.
