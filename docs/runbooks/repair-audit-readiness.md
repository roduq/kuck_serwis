# Readiness trwałego audytu odczytu napraw

Status: proponowany runbook wdrożeniowy; capability `account-read` pozostaje
wyłączone.

Data audytu: 2026-08-14

Aktualizacja 2026-08-21: poniższy collector i trwały snapshot są zakresem po
v1, a nie warunkiem uruchomienia podstawowego portalu. Kontrolowany rollout v1
używa flagi per-site, istniejących bezpośrednich kontroli gotowości oraz
obowiązkowego trwałego ACK audytu; każda awaria nadal daje wynik fail-closed.

Zakres dokumentu: przyszły collector i trwała bramka łączące istniejący aktywny
probe z czystym plannerem readiness. Dokument nie dodaje hooka, schedulera,
DocType, migracji ani konfiguracji i nie zatwierdza retencji, legal hold,
alertów ani rollout.

Źródła lokalne:

- [runbook operacji audytu](repair-audit-operations.md);
- [aktywny probe](../../kuck_serwis/audit_health.py);
- [czysty planner readiness](../../kuck_serwis/audit_readiness.py);
- [publiczny kontrakt v1](../../kuck_serwis/public_contract/v1.py);
- [aktualne hooki](../../kuck_serwis/hooks.py);
- [ADR publicznego kontraktu](../adr/0001-public-contract-v1.md).

## Jak czytać dokument

- **Fakt** opisuje zachowanie obecnego kodu.
- **Wymaganie przyszłe** opisuje warunek implementacji collectora lub aktywacji.
- **Decyzja otwarta** wymaga jawnego zatwierdzenia Kuck, operations albo legal;
  dokument nie uzupełnia jej domyślnie.

Wartość brakująca, nieczytelna, przeterminowana, sfałszowana albo niezgodna z
aktywną rewizją zawsze oznacza `false`. W żadnym miejscu `None`, pusty rekord,
brak błędów w logu, cisza metryk ani sam fakt istnienia tabeli nie mogą być
interpretowane jako gotowość.

## Stan obecny — fakty

1. `audit_health.run_active_repair_audit_probe()` tworzy syntetyczny event bez
   danych klienta, przekazuje go do `DurableRepairAuditSink.emit()`, a po ACK
   otwiera nowe połączenie i porównuje dokładnie jeden utrwalony wiersz.
2. Wynik probe ma zamknięty kształt `ok`, `checked_at`, `probe_version`, `codes`.
   Sukces ma wersję `repair-audit-active/v1` i kod `ACTIVE_CANARY_OK`. Błędy są
   zamieniane na kody z allowlisty bez treści wyjątku.
3. Probe sam pobiera aktualny czas UTC, lecz nie ma whitelisted endpointu,
   scheduler hooka ani wpływu na readiness.
4. `audit_readiness.plan_audit_readiness()` jest pure: przyjmuje immutable
   evidence, jawny `checked_at` i jawny `max_probe_age_seconds`. Nie importuje
   Frappe, nie czyta zegara, bazy, konfiguracji ani środowiska.
5. Planner wymaga literalnych `bool`, kanonicznego UTC
   `YYYY-MM-DDTHH:MM:SSZ`, odrzuca probe z przyszłości i uznaje granicę wieku za
   domkniętą: wiek równy limitowi jest świeży, limit + 1 sekunda jest stary.
6. Wszystkie domyślne bramki `AuditReadinessEvidence` mają wartość `False`, a
   brak aktywnego probe daje `ACTIVE_PROBE_MISSING`.
7. `AuditReadinessPlan` zawiera wyłącznie `capability_ready` oraz uporządkowane
   kody. `READY` może wystąpić tylko jako jedyny kod wyniku `true`.
8. W historycznej rewizji objętej audytem
   `public_contract.v1._AUDIT_AND_MONITORING_READY` było stałe `False`.
   Od aktualizacji v1 z 2026-08-21 `_account_read_enabled()` wymaga literalnie
   włączonej flagi rollout i przejścia `_is_ready()`; wyjątek daje `False`.
9. `_is_ready()` sprawdza także sink i klucze, pole oraz unikalny indeks
   `Naprawa.public_id`, brak pustych public ID i brak nieznanych statusów.
10. W `hooks.py` blok `scheduler_events` jest tylko komentarzem. Nie istnieje
    collector, pasywny monitor ani trwały rekord readiness.

Wniosek ten opisuje historyczną rewizję z 2026-08-14. Aktualny v1 nie uzależnia
podstawowego odczytu od przyszłego collectora; nadal wymaga wszystkich
bezpośrednich bramek i trwałego audytu.

## Docelowa bramka aktywacji — wymaganie przyszłe

Po osobnym ADR, schemacie, migracji i implementacji wynik capability ma być
logicznie równoważny:

```text
account_read =
  rollout_flag is literal True
  AND dynamic_audit_readiness(current_utc, max_active_age=600) is READY
  AND existing_public_contract_checks are True
```

`dynamic_audit_readiness` musi odczytać trwały snapshot, defensywnie zbudować
`AuditReadinessEvidence` i ponownie wywołać planner z bieżącym, zaufanym UTC oraz
`max_probe_age_seconds=600`. Nie wolno używać samego zapisanego wcześniej
`capability_ready=True`, ponieważ taki wynik może się zestarzeć pomiędzy
przebiegami collectora.

Nie należy zmieniać `_AUDIT_AND_MONITORING_READY` na bezwarunkowe `True`.
Przyszła, zatwierdzona zmiana ma zastąpić ten statyczny bezpiecznik dynamicznym
odczytem albo pozostawić go jako osobny literalny kill switch, domyślnie
`False`. Brak rekordu, błąd odczytu, konflikt CAS, stara rewizja polityki,
niekanoniczny czas lub wyjątek zawsze zwraca `features: []`.

## Exact evidence collectora — wymaganie przyszłe

Collector buduje dokładnie jeden snapshot na site i środowisko. Nie przyjmuje
danych przez HTTP. Źródłem czasu jest serwerowy UTC przechwycony raz na początku
przebiegu; nie wolno używać czasu klienta ani ruchomego `now()` pomiędzy
kontrolami.

| Pole `AuditReadinessEvidence` | Exact pozytywny dowód | Fail-closed |
|---|---|---|
| `active_probe` | Ostatni wynik ma dokładny zestaw pól, `ok is True`, `probe_version == repair-audit-active/v1`, `codes == [ACTIVE_CANARY_OK]` i kanoniczny `checked_at`; planner potwierdza wiek `<= 600 s`. | Brak, dodatkowe pole, inny typ/kod/wersja, przyszły czas, wiek `> 600 s`, wyjątek albo niejednoznaczny wynik. |
| `sink_ready` | Pasywny monitor zakończył się sukcesem nie dawniej niż 120 s, izolowane połączenie jest dostępne, nie ma przyrostu `audit_sink_failure_total` w ostatnich 5 min i exporter metryk potwierdził odbiór bieżącej serii. | Cisza monitoringu, stary monitor, brak ACK/exportera, dowolna awaria sinka lub nieczytelne liczniki. |
| `schema_ready` | Exact schema `Kuck Repair Audit Event`, `permissions=[]`, komplet allowlisty, unikalność `event_id` i `correlation_id`, append-only guards oraz wymagany indeks purge; ponadto przechodzą obecne kontrole `Naprawa.public_id` i `STATUS_MAP`. | Brak/mismatch pola, indeksu, guardu lub permissions; pusty/duplikowany `public_id`, nieznany status, błąd metadanych albo nieukończona migracja. |
| `retention_signed_off` | Aktywna rewizja polityki wskazuje jawnie zatwierdzony wariant/okres, zgodny lifecycle backupów, zakończony dry-run i zapisowy test jednego batcha z pojednaniem. Zatwierdzenie dotyczy exact rewizji konfiguracji. | Brak/revoked/stare zatwierdzenie, rozbieżny okres, brak testu, zaległy purge poza zatwierdzonym progiem lub nieczytelna konfiguracja. |
| `legal_hold_signed_off` | Polityka i role hold/release są zatwierdzone, rejestr jest czytelny, test place/release przeszedł, a każdy aktywny hold jest prawidłowo egzekwowany przez purge. Aktywny poprawny hold sam nie wyłącza odczytu. | Brak zatwierdzenia, uszkodzony/nieosiągalny rejestr, hold po terminie przeglądu, niejednoznaczny stan lub możliwość obejścia holda. |
| `alerting_owner_ready` | Dla exact środowiska istnieje zatwierdzony właściciel dyżuru, kanał eskalacji, godziny reakcji i zastępstwo; syntetyczny alert został odebrany i potwierdzony. | Brak właściciela/zastępstwa, niepotwierdzona trasa, wygasła eskalacja albo odbiór tylko w logu aplikacji. |
| `alert_threshold_ready` | Aktywna rewizja progów odpowiada zatwierdzonemu runbookowi, exporter i reguły są zdrowe, a test warning/critical dowiódł zamknięcia readiness tam, gdzie wymagane. | Brak reguły, niezgodna rewizja, dowolne wartości domyślne, cisza danych, nieznany kod lub poluzowanie bez zatwierdzenia. |
| `rollback_ready` | Właściciel i okno rollbacku są zatwierdzone; drill dowiódł, że literalne wyłączenie flagi natychmiast daje `features: []`, bez kasowania audytu, holdów i monitoringu. Dowód dotyczy bieżącego release. | Brak właściciela/drillu, stary release, rollback wymagający downgrade danych albo wyłączający ochronę audytu. |
| `runbook_ready` | Ta i operacyjna instrukcja mają zatwierdzoną, wdrożoną rewizję; dyżur potwierdził dostęp i przećwiczył awarię sinka, stare probe i recovery. | Draft, niezgodna rewizja, brak ćwiczenia, brak dostępu dyżuru lub nierozwiązana krytyczna pozycja. |

Każdy extractor zwraca literalne `True` wyłącznie po pełnej pozytywnej
walidacji. Nie wolno implementować `value or True`, `bool(value)`, „ostatni znany
dobry” bez sprawdzenia wieku ani traktować nieobsługiwanego kodu jako warning.

## Algorytm collectora — wymaganie przyszłe

1. Uzyskaj single-flight lease dla dokładnego site i spodziewanej rewizji stanu.
   Brak lease kończy przebieg bez równoległego probe.
2. Przechwyć raz kanoniczny `assessment_checked_at` UTC i odczytaj bieżącą
   `expected_revision` trwałego rekordu.
3. Wykonaj pasywne, read-only kontrole. Zwracają tylko literalne booleany i kody
   allowlisty; nie zwracają nazw rekordów, SQL, ścieżek ani komunikatów wyjątków.
4. Jeżeli od ostatniej aktywnej próby minęło 300 s albo nie ma poprawnego wyniku,
   uruchom `run_active_repair_audit_probe()` poza transakcją stanu readiness.
   Nigdy nie kopiuj correlation ID canary do snapshotu ani telemetrii.
5. Zwaliduj exact shape wyniku probe. Przy każdym odchyleniu skonstruuj evidence
   niespełniające bramki; nie próbuj naprawiać lub zgadywać wartości.
6. Zbierz zatwierdzenia retencji/holdów/alertów/runbooka z prywatnego,
   wersjonowanego źródła operacyjnego. Zatwierdzenie musi odpowiadać aktywnej
   rewizji polityki i release.
7. Zbuduj immutable `AuditReadinessEvidence` i wywołaj
   `plan_audit_readiness(..., checked_at=assessment_checked_at,
   max_probe_age_seconds=600)`.
8. Zapisz snapshot i plan przez compare-and-swap `expected_revision →
   expected_revision + 1`. Konflikt wymaga ponownego odczytu i pełnej ponownej
   oceny; stary wynik `READY` nie może nadpisać nowszej porażki.
9. Zwolnij lease i wyemituj wyłącznie code-only metryki. Nie loguj evidence,
   timestampów zatwierdzeń, wyjątków, konfiguracji ani danych eventu.

Porażkę probe lub krytycznej kontroli należy utrwalić jako `false` w tym samym
przebiegu. Jeżeli nie można zapisać stanu, request path nie może korzystać z
cache jako substytutu; ostatni rekord sam wygaśnie najpóźniej na granicy 600 s,
a awaria trwałego odczytu daje `false` natychmiast.

## Trwały stan i CAS — wymaganie przyszłe

Potrzebny jest prywatny, niedostępny w Desk rekord operacyjny. Jego konkretny
DocType, indeksy i migracja wymagają osobnego ADR. Logiczny snapshot ma zawierać
wyłącznie:

- bounded monotonic `revision`;
- kanoniczny `collected_at` UTC;
- `ActiveProbeEvidence` bez correlation ID;
- osiem literalnych bramek evidence;
- wynik `capability_ready` i uporządkowane `ReadinessCode`;
- zamknięte identyfikatory rewizji kontraktu, probe, polityki i release;
- kod ostatniej operacji collectora z allowlisty.

Nie przechowuje User, Customer, `Naprawa.name`, correlation/event ID, hashy
aktorów/napraw, nazw hostów, adresów, URL, ścieżek, surowych metryk, wyjątków,
payloadów ani wartości konfiguracji. Rekord ma `permissions=[]`; zapisuje go
wyłącznie prywatny collector. Cache może przyspieszać odczyt dopiero po
porównaniu rewizji z bazą, ale nie jest źródłem prawdy.

CAS obejmuje cały snapshot. Nie wolno aktualizować pojedynczych bramek osobno,
bo mieszanka wyników z różnych chwil lub rewizji mogłaby fałszywie dać `READY`.
Restore backupu, downgrade release albo zmiana polityki unieważnia poprzedni
snapshot i wymaga świeżego collectora, probe oraz nowej rewizji.

## Scheduler i pasywny monitor — wymaganie przyszłe

- Pasywny monitor działa co minutę i zapisuje swój ostatni sukces/porażkę do
  prywatnego stanu; świeżość wymagana dla `sink_ready` wynosi najwyżej 120 s.
- Aktywny probe działa co pięć minut. Planner zawsze dostaje limit 600 s; brak
  sukcesu przez 601 s zamyka readiness niezależnie od alertu „dwie porażki”.
- Collector ocenia i zapisuje pełny snapshot co minutę, również gdy aktywny
  canary nie jest jeszcze należny.
- Job ma stały, niskokardynalny identyfikator per site, single-flight lease,
  limit czasu krótszy od interwału i ograniczony retry z jitterem.
- Scheduler failure jest porażką monitoringu. Brak kolejnego przebiegu nie
  przedłuża ważności ostatniego `READY`.
- Implementacja `scheduler_events` i prywatnego stanu jest osobnym zadaniem;
  aktualne komentowane przykłady w `hooks.py` niczego nie uruchamiają.

Pasywny monitor nie czyta danych portalu jako użytkownik i nie używa publicznego
endpointu. Sprawdza metadane, agregaty i zdrowie zależności na prywatnym porcie,
z bounded zapytaniami oraz stałym zestawem kodów.

## Telemetria code-only i alerty

Dozwolone metryki readiness:

- gauge `repair_audit_readiness` o wartości `0|1`;
- `repair_audit_readiness_transition_total` z kodem przyczyny;
- `repair_audit_probe_result_total` z `probe_version` i kodem;
- `repair_audit_evidence_age_seconds` z zamkniętym rodzajem `active|passive`;
- `repair_audit_collector_result_total` z kodem collectora.

Dozwolone etykiety to wyłącznie pseudonimowy `site_id`, zamknięty kod,
`probe_version`, rodzaj probe i środowisko z allowlisty. Owner, kanał, hostname,
URL, correlation ID, actor/repair hash, exception, SQL i dowolny tekst z
konfiguracji nie są etykietami.

Obowiązują progi z runbooka operacyjnego; ich właściciele są decyzją otwartą:

| Sygnał | Warning | Critical i wpływ na readiness |
|---|---|---|
| Awarie sinka | — | każdy przyrost w 5 min; natychmiast `false` |
| Aktywny probe | pierwsza porażka; bieżące evidence już daje `false` | dwie kolejne lub brak sukcesu 600 s; eskalacja critical |
| Pasywny probe | pierwsza porażka; `sink_ready=False` | dwie kolejne lub brak sukcesu 120 s; eskalacja critical |
| Brak/duplikat `public_id`, nieznany status | — | wartość większa od zera; `false` |
| `DEPENDENCY_UNAVAILABLE` | co najmniej 5 i ponad 1% przez 5 min | co najmniej 20 lub ponad 5% przez 5 min |
| Latency kontraktu | p95 ponad 500 ms przez 10 min | p95 ponad 1000 ms przez 5 min |
| Zaufane IDOR | co najmniej 10/5 min i ponad 3× mediana 7 dni | co najmniej 50/5 min; incydent |
| `AUTH_REQUIRED` + `INVALID_CURSOR` | ponad 3× mediana przez 15 min, minimum 30 | ponad 10× mediana, minimum 100/15 min |
| Purge | brak sukcesu 26 h lub cutoff + 24 h | brak sukcesu 48 h lub cutoff + 72 h |
| Legal hold | codzienna informacja właściciela | po terminie przeglądu/nieczytelny rejestr; odpowiednia bramka `false` |
| Pojemność | prognoza 30 dni do 80% | co najmniej 90% |

Każda porażka evidence zamyka odpowiadającą bramkę niezależnie od tego, czy próg
eskalacji ma jeszcze poziom warning czy już critical. Musi spowodować CAS nowego
snapshotu `false`; sam alert nie jest mechanizmem sterującym. Test alertu musi
potwierdzić odbiór przez zatwierdzonego właściciela i eskalację zastępczą.

## Retencja i legal hold

Runbook operacyjny opisuje warianty A/B/C, lecz żaden nie jest wybrany przez ten
dokument. `retention_signed_off=True` wymaga wspólnego zatwierdzenia właściciela
biznesowego i legal/compliance dla celu, okresu online, backupów i ewentualnego
archiwum. Zmiana okresu albo rewizji konfiguracji automatycznie unieważnia
evidence do czasu nowego dry-run, próby zapisu i pojednania.

`legal_hold_signed_off=True` potwierdza gotowość mechanizmu, nie brak aktywnych
holdów. Poprawny aktywny hold zatrzymuje kwalifikowane usuwanie, ale audyt i
odczyt napraw mogą działać. Nieczytelny rejestr, nieznana akcja, brak aktualnego
przeglądu albo możliwość zwolnienia holda przez operatora purge daje `false` i
zatrzymuje purge.

## Rollout i aktywacja

1. **Implementacja wyłączona:** osobny ADR prywatnego stanu, collectora i CAS;
   schema expand/verify/constrain; scheduler działa przy `features: []`.
2. **Staging:** minimum pełna macierz akceptacji poniżej, syntetyczny alert,
   purge dry-run/jeden batch, restore oraz wymuszone awarie.
3. **Production dark:** flaga rollout literalnie `False`, minimum 24 h ciągłych
   świeżych snapshotów bez nierozwiązanej krytycznej pozycji.
4. **Staff/canary:** flaga ograniczona do zatwierdzonego site/okna, minimum 24 h;
   pojednanie wywołań, eventów i kodów bez odczytu surowych danych.
5. **Stopniowo:** 5% → 25% → 100%; przed każdym etapem świeży snapshot `READY`,
   właściciel rollbacku i co najmniej jedno zatwierdzone okno obserwacji.

Operator nie ustawia żadnej bramki ręcznie na `True`. Zatwierdzenia są wejściem
do extractora, a ostateczny wynik zawsze wylicza planner. Flaga rollout jest
dodatkowym warunkiem i nie może nadpisać `false` readiness.

## Rollback

Pierwsza akcja: ustaw flagę rollout na literalne `False` i potwierdź
`features: []`. Następnie zatrzymaj ruch konsumenta, ale zachowaj sink, collector,
probe, metryki, holdy, retencję oraz dane. Nie usuwaj schematu i nie wykonuj
downgrade danych.

Jeżeli awaria dotyczy collectora, unieważnij/oznacz snapshot jako `false` przez
CAS i pozostaw monitory aktywne. Jeżeli CAS lub baza są niedostępne, request path
ma zwracać `false`; nie przełączaj się na cache ani stałe `True`. Jeżeli awaria
dotyczy purge, wyłącz wyłącznie purge, ale capability pozostaje wyłączone do
reconciliation. Ponowne włączenie zaczyna się od etapu odpowiedniego dla źródła
awarii i wymaga świeżego active probe.

## Macierz testów akceptacyjnych

Testy używają danych syntetycznych i oddzielnego site. Poniższe przypadki są
wymaganiami przyszłej implementacji, nie deklaracją obecnie wykonanych testów.

### Pure planner i mapowanie evidence

1. Wszystkie exact evidence `True` i active probe o wieku mniejszym niż 600 s
   dają wyłącznie `READY`.
2. Domyślne `AuditReadinessEvidence()` daje `false` i wszystkie brakujące kody.
3. Każda z ośmiu bramek ustawiona osobno na `False` daje właściwy pojedynczy kod.
4. `ok=False` przy świeżym probe daje `ACTIVE_PROBE_FAILED`.
5. Wiek dokładnie 600 s jest świeży, a 601 s daje `ACTIVE_PROBE_STALE`.
6. Probe o sekundę w przyszłości daje `ACTIVE_PROBE_FUTURE`.
7. Offset UTC, fractional seconds, zła data i whitespace są odrzucane kodem bez
   echa wartości.
8. `max_probe_age_seconds` odrzuca `bool`, zero, float, string oraz wartość ponad
   bound.
9. Każda bramka odrzuca `0`, `1`, `None` i string zamiast literalnego `bool`.
10. Forged outer/nested DTO nie wypuszcza `AttributeError` ani danych wejścia.
11. Wynik jest frozen, bounded, uporządkowany i nie pozwala połączyć `READY` z
    kodem porażki.
12. Różna kolejność budowania równoważnego evidence daje identyczny plan.

### Collector, trwałość i współbieżność

13. Exact sukces `audit_health` mapuje się na `ActiveProbeEvidence(True, ...)`;
    dodatkowe pole, dodatkowy kod lub inna wersja mapują się fail-closed.
14. `emit=True` bez jednego identycznego wiersza widocznego z nowego połączenia
    nigdy nie zapisuje `READY`.
15. Brak trwałego rekordu, uszkodzony rekord lub nieznana rewizja polityki daje
    `features: []`.
16. Request ponownie ocenia wiek; zapisany wcześniej `READY` staje się `false`
    po 601 s bez oczekiwania na kolejny scheduler.
17. Pasywny wynik w wieku 120 s jest akceptowany, a 121 s zamyka `sink_ready`.
18. Każdy przyrost awarii sinka w oknie 5 min natychmiast zamyka `sink_ready`.
19. Dwa równoległe collectory: tylko poprawny CAS zapisuje; stary `READY` nie
    nadpisuje nowszego `false`.
20. Utrata lease przerywa zapis, nie uruchamia drugiego canary i nie przedłuża
    ważności poprzedniego snapshotu.
21. Awaria zapisu stanu nie używa Redis/cache jako źródła prawdy i kończy
    request path fail-closed.
22. Restore starszego snapshotu nie przechodzi kontroli release/policy revision
    przed świeżym collectorem.
23. Snapshot nie zawiera correlation/event ID, User, Customer, nazw napraw,
    hashy, hosta, ścieżek, URL, wyjątków ani payloadu.

### Operacje, bezpieczeństwo i rollout

24. Brak/duplikat `public_id`, nieznany status, brak indeksu lub zmienione
    permissions dają `schema_ready=False`.
25. Brak zatwierdzenia retencji, niezgodna konfiguracja backupu albo brak próby
    purge daje `retention_signed_off=False`.
26. Aktywny poprawny hold chroni purge bez automatycznego wyłączenia capability;
    nieczytelny lub przeterminowany hold daje `legal_hold_signed_off=False`.
27. Alert testowy dociera do właściciela i zastępstwa; brak ACK daje
    `alerting_owner_ready=False`.
28. Brak jednej zatwierdzonej reguły albo cisza exportera daje
    `alert_threshold_ready=False`.
29. Telemetria zawiera tylko allowlisted kody/etykiety i nie zawiera PII,
    correlation ID, tracebacka ani arbitralnego tekstu.
30. Rollback drill po literalnym wyłączeniu flagi natychmiast zwraca
    `features: []`, pozostawiając sink, probe, holdy i monitoring aktywne.
31. Flaga rollout `True` przy dowolnej bramce `False` nadal daje `features: []`.
32. Pełne 24 h production dark bez świeżego probe choćby przez jedno okno nie
    spełnia warunku przejścia do staff.
33. Przejścia 5%/25%/100% wymagają osobnego świeżego snapshotu, ownera rollbacku
    i pojednania poprzedniego etapu.
34. Pełna regresja `kuck_serwis` i konsumenta `kuck_shop` pozostaje zielona przy
    capability wyłączonym i w kontrolowanym staff rollout.

## Minimalny formularz decyzji

Każde pole jest obowiązkowe. `NIEUSTALONE` utrzymuje odpowiadającą bramkę jako
`False`; formularz nie może być automatycznie uzupełniany wartościami
rekomendowanymi.

### Kuck — właściciel biznesowy

| Pole | Decyzja |
|---|---|
| Czy aktywować `account-read` po spełnieniu bramki? | NIEUSTALONE |
| Zatwierdzony zakres staff/canary i kryterium promocji | NIEUSTALONE |
| Akceptowane okna rollout 5%/25%/100% | NIEUSTALONE |
| Właściciel biznesowy zatrzymania rollout | NIEUSTALONE |
| Akceptowana polityka komunikatu przy fail-closed | NIEUSTALONE |

### Operations/SRE

| Pole | Decyzja |
|---|---|
| Właściciel dyżuru i zastępstwo | NIEUSTALONE |
| Kanał warning/critical i SLA ACK/reakcji | NIEUSTALONE |
| Akceptacja progów z tego runbooka / zatwierdzona rewizja zmian | NIEUSTALONE |
| Backend metryk i pseudonimowy `site_id` | NIEUSTALONE |
| Właściciel oraz okna rollbacku | NIEUSTALONE |
| RPO/RTO, harmonogram backupu i data ostatniego restore drill | NIEUSTALONE |
| Okna maintenance dla purge i collectora | NIEUSTALONE |

### Legal/compliance

| Pole | Decyzja |
|---|---|
| Wariant retencji A/B/C, cel i liczba dni online | NIEUSTALONE |
| Retencja backupów i ewentualnego archiwum | NIEUSTALONE |
| Role zatwierdzające place/release legal hold | NIEUSTALONE |
| Maksymalny okres i częstotliwość przeglądu aktywnego holda | NIEUSTALONE |
| Wymagany okres śladu zatwierdzeń i działań administracyjnych | NIEUSTALONE |
| Zatwierdzenie zgodności purge/backup/restore z polityką | NIEUSTALONE |

## Warunek rozpoczęcia implementacji runtime

Przed kodem collectora potrzebne są: zatwierdzony ADR prywatnego stanu i CAS,
zamknięty formularz decyzji, projekt schema expand/verify/constrain, syntetyczne
fixtures oraz jawny zakres hooka schedulera. Implementacja ma powstać nadal przy
`features: []`; samo ukończenie kodu nie upoważnia do rollout.

## Walidacja dokumentu

Uruchamiana z repozytorium `apps/kuck_serwis`:

```bash
test -s docs/runbooks/repair-audit-readiness.md
test -e docs/runbooks/repair-audit-operations.md
test -e kuck_serwis/audit_health.py
test -e kuck_serwis/audit_readiness.py
test -e kuck_serwis/public_contract/v1.py
test -e kuck_serwis/hooks.py
test -e docs/adr/0001-public-contract-v1.md
rg -n '^## ' docs/runbooks/repair-audit-readiness.md
rg -ni '(password|passwd|secret|api[_ -]?key|encryption_key)\s*[:=]' docs/runbooks/repair-audit-readiness.md
git diff --check -- docs/runbooks/repair-audit-readiness.md
git diff --no-index /dev/null docs/runbooks/repair-audit-readiness.md
```

Skan sekretów ma zwrócić brak dopasowań. Ostatnia komenda zwraca `1`, ponieważ
pokazuje oczekiwany diff nowego pliku; inny kod oznacza błąd narzędzia.
