# ADR 0002 — trwały stan readiness audytu napraw

Status: proposed; capability pozostaje wyłączone

Data: 2026-08-14

Aktualizacja 2026-08-21: ten ADR opisuje opcjonalne, rozszerzone monitorowanie po
v1 i nie blokuje kontrolowanego uruchomienia podstawowego `account-read`.
W v1 aktywację chronią odwracalna flaga per-site, bezpośrednie kontrole
strukturalne oraz obowiązkowy trwały ACK audytu na każdym żądaniu; błąd dowolnej
bramki zamyka odczyt. Collector, snapshot CAS i etapowy procentowy rollout
pozostają usprawnieniem po uruchomieniu.

## Kontekst

Trwały sink audytu i aktywny canary istnieją, lecz nie ma collectora,
pasywnego monitora, schedulera ani trwałego snapshotu readiness. Czysty planner
z rewizji `0126ada` nie czyta czasu, bazy ani konfiguracji. Runbook z rewizji
`785f009` określa active freshness 600 s, passive freshness 120 s, osiem bramek,
CAS oraz rollout fail-closed. `public_contract.v1` nadal ma
`_AUDIT_AND_MONITORING_READY = False`.

Źródła lokalne:

- [planner readiness](../../kuck_serwis/audit_readiness.py);
- [aktywny probe](../../kuck_serwis/audit_health.py);
- [publiczny kontrakt](../../kuck_serwis/public_contract/v1.py);
- [runbook readiness](../runbooks/repair-audit-readiness.md);
- [runbook operacji audytu](../runbooks/repair-audit-operations.md);
- [hooki kuck_serwis](../../kuck_serwis/hooks.py).

Audyt wykonano również na przypiętym Frappe
`5c16f12192815204a7eda2b2ab365a557a6e7def`:

- [`Scheduled Job Type`](../../../frappe/frappe/core/doctype/scheduled_job_type/scheduled_job_type.py)
  deduplikuje enqueue przez stały RQ job ID, a wrapper wykonania commit/rollbackuje
  transakcję joba;
- [synchronizacja cron hooków](../../../frappe/frappe/core/doctype/scheduled_job_type/scheduled_job_type.py)
  tworzy rekordy `Cron` z `scheduler_events["cron"]`;
- [`frappe.db.get_value`](../../../frappe/frappe/database/database.py) obsługuje
  `for_update=True`;
- [query builder](../../../frappe/frappe/database/query.py) mapuje tę opcję na
  blokadę `FOR UPDATE`;
- zwykłe `db.set_value` wykonuje update, ale nie jest samo w sobie kontraktem
  compare-and-swap i nie może zastąpić kontroli rewizji pod blokadą.

Stan merge, Redis ani obecność joba w kolejce nie są dowodem readiness.

## Decyzja proponowana

Wprowadzić **dokładnie jeden** prywatny, zwykły DocType
`Kuck Repair Audit Readiness State` z dokładnie jednym wierszem o stałej nazwie
`repair-audit-readiness-v1` na site. Nie używać Single DocType: rozproszenie pól
w `tabSingles` utrudnia atomową blokadę i CAS całego snapshotu. Zwykły wiersz
zapewnia pojedynczy klucz główny i spójny `SELECT ... FOR UPDATE`.

Ten ADR jest projektem przyszłej implementacji. Nie tworzy DocType ani hooka i
nie zmienia `_AUDIT_AND_MONITORING_READY`. Wdrożenie wymaga osobnego zadania,
migracji expand/verify/constrain i zamknięcia blokerów na końcu dokumentu.

## Własność i granice

- `kuck_serwis` jest wyłącznym właścicielem DocType, collectora i odczytu
  readiness.
- Publiczny request tylko odczytuje snapshot, ponownie uruchamia pure planner i
  wykonuje istniejące kontrole strukturalne. Nie uruchamia probe, nie odnawia
  lease i nie zapisuje stanu.
- Scheduler zbiera evidence i zapisuje cały snapshot. Nie zmienia flagi rollout
  ani stałego kill switcha.
- Retencja, legal hold, alerty, runbook i release manifest pozostają własnością
  zatwierdzonych systemów źródłowych. Stan readiness przechowuje tylko ich
  literalny wynik i rewizję, nie dokumenty zatwierdzeń.
- Redis/cache może przechowywać kopię optymalizacyjną, ale baza jest jedynym
  źródłem prawdy. Błąd bazy nie przełącza odczytu na cache.

## DocType i pojedyncza tożsamość

DocType jest zwykły (`issingle=0`), prywatny, bez web view i bez Desk access:

- `permissions=[]`;
- `index_web_pages_for_search=0`, `track_changes=0`;
- stały `name=repair-audit-readiness-v1`, tworzony wyłącznie przez patch expand;
- primary key `name` jest jedynym potrzebnym indeksem; nie dodajemy indeksów
  wtórnych dla pojedynczego wiersza;
- verify wymaga dokładnie jednego wiersza i dokładnej nazwy; zero lub więcej niż
  jeden oznacza fail-closed;
- framework insert/update/delete/rename/import są blokowane przez kontroler;
  zapis wykonuje wyłącznie prywatny store ze ścisłą allowlistą kolumn;
- `owner` i `modified_by` używają stałego technicznego aktora, nie konta osoby.

Nie wolno automatycznie tworzyć brakującego wiersza w request path ani w
collectorze. Brak rekordu oznacza `false`; tylko idempotentny patch może utworzyć
rekord inicjalny.

## Pola i limity

Poniżej wymieniono komplet pól własnych. Standardowe pola Frappe nie są częścią
evidence. Wszystkie pola są `read_only`, `no_copy`; wartości boolowskie są
odczytywane wyłącznie jako DB `0|1` i jawnie konwertowane do literalnego Python
`bool`. Inna wartość jest uszkodzonym stanem.

| Pole | Frappe type | Required / limit | Inwariant |
|---|---|---|---|
| `state_schema_revision` | Int | required | Exact `1`; inna wersja jest nieobsługiwana. |
| `revision` | Long Int | required | `0..9223372036854775807`, rośnie o dokładnie 1 po każdym pełnym snapshot CAS. |
| `collected_at_utc` | Data | nullable, exact 20 znaków | Kanoniczne `YYYY-MM-DDTHH:MM:SSZ`; `null` tylko przy revision 0. |
| `active_probe_ok` | Check | required, default 0 | Status ostatniego wykonanego active probe; nigdy domyślnie true. |
| `active_probe_checked_at_utc` | Data | nullable, exact 20 | Kanoniczny czas probe; brak oznacza brak `ActiveProbeEvidence`. |
| `active_probe_version` | Data | nullable, max 64 | Przy wyniku obecnego probe exact `repair-audit-active/v1`. |
| `active_probe_code` | Select | required | Jedna wartość z active allowlisty poniżej. |
| `passive_probe_ok` | Check | required, default 0 | Status ostatniego pasywnego monitora. |
| `passive_probe_checked_at_utc` | Data | nullable, exact 20 | Kanoniczny czas pasywnego monitora. |
| `passive_probe_code` | Select | required | Jedna wartość z passive allowlisty poniżej. |
| `sink_ready` | Check | required, default 0 | Pierwsza z ośmiu bramek plannera. |
| `schema_ready` | Check | required, default 0 | Druga bramka. |
| `retention_signed_off` | Check | required, default 0 | Trzecia bramka; nie przechowuje approvera ani okresu. |
| `legal_hold_signed_off` | Check | required, default 0 | Czwarta bramka; aktywny poprawny hold nie wymusza false. |
| `alerting_owner_ready` | Check | required, default 0 | Piąta bramka; nie przechowuje nazwiska ani kanału. |
| `alert_threshold_ready` | Check | required, default 0 | Szósta bramka. |
| `rollback_ready` | Check | required, default 0 | Siódma bramka. |
| `runbook_ready` | Check | required, default 0 | Ósma bramka. |
| `plan_ready` | Check | required, default 0 | Wynik zapisany dla diagnostyki; request zawsze przelicza go ponownie. |
| `readiness_codes_json` | Small Text | required, 2..1024 bajtów UTF-8 | Kanoniczny JSON array unikalnych kodów w kolejności enum; żadnego tekstu. |
| `readiness_contract_revision` | Data | required, 1..64 ASCII | Zamknięty identyfikator wersji collector/planner, regex `^[a-z0-9][a-z0-9._/-]{0,63}$`. |
| `policy_revision_sha256` | Data | nullable, exact 64 | Lowercase hex digest zatwierdzonego pakietu polityk; brak utrzymuje właściwe bramki false. |
| `release_manifest_sha256` | Data | nullable, exact 64 | Lowercase hex digest zatwierdzonego release manifest; brak daje fail-closed. |
| `last_collector_code` | Select | required | Jedna wartość z collector allowlisty. |
| `lease_token_hash` | Data | nullable, exact 64 | SHA-256 losowego tokenu lease; raw token istnieje tylko w pamięci workera. |
| `lease_expires_at_utc` | Data | nullable, exact 20 | Kanoniczny UTC; oba pola lease są null albo oba poprawne. |

DocType nie zawiera pól dynamic link, User, Customer, Naprawa, URL, host, path,
exception, payload, correlation/event ID, hash aktora/naprawy, approver ani
arbitralnego komentarza.

## Allowlisty kodów

### Active probe

```text
ACTIVE_NOT_RUN
ACTIVE_CANARY_OK
KEY_UNAVAILABLE
SINK_ACK_INVALID
SINK_UNAVAILABLE
VERIFY_UNAVAILABLE
VERIFY_COUNT_MISMATCH
VERIFY_CONTENT_MISMATCH
```

`ACTIVE_NOT_RUN` jest wyłącznie stanem inicjalnym. Pozostałe kody odpowiadają
obecnemu `audit_health`; dodanie kodu wymaga rewizji kontraktu.

### Passive probe

```text
PASSIVE_NOT_RUN
PASSIVE_OK
PASSIVE_CONNECTION_UNAVAILABLE
PASSIVE_SCHEMA_MISMATCH
PASSIVE_PUBLIC_ID_INVALID
PASSIVE_STATUS_INVALID
PASSIVE_RETENTION_INVALID
PASSIVE_HOLD_UNAVAILABLE
PASSIVE_METRICS_UNAVAILABLE
PASSIVE_PURGE_STALE
PASSIVE_INTERNAL_ERROR
```

### Collector

```text
COLLECTOR_NOT_RUN
COLLECTOR_OK
COLLECTOR_LEASE_BUSY
COLLECTOR_EVIDENCE_INVALID
COLLECTOR_POLICY_MISMATCH
COLLECTOR_RELEASE_MISMATCH
COLLECTOR_DEPENDENCY_UNAVAILABLE
COLLECTOR_CAS_CONFLICT
COLLECTOR_INTERNAL_ERROR
```

### Readiness

`readiness_codes_json` przyjmuje wyłącznie wartości istniejącego
`ReadinessCode`: `READY`, `ACTIVE_PROBE_MISSING`, `ACTIVE_PROBE_FAILED`,
`ACTIVE_PROBE_STALE`, `ACTIVE_PROBE_FUTURE`, `SINK_NOT_READY`,
`SCHEMA_NOT_READY`, `RETENTION_NOT_SIGNED_OFF`,
`LEGAL_HOLD_NOT_SIGNED_OFF`, `ALERTING_OWNER_NOT_READY`,
`ALERT_THRESHOLD_NOT_READY`, `ROLLBACK_NOT_READY`, `RUNBOOK_NOT_READY`.

`READY` jest jedynym kodem wyniku true. Wynik false ma co najmniej jeden kod, bez
duplikatów, w dokładnej kolejności enum. Parser odrzuca dodatkowe klucze JSON,
liczby, obiekty, nieznany kod, niekanoniczny JSON i przekroczenie limitu przed
utworzeniem DTO.

## Stan początkowy

Patch expand tworzy dokładnie jeden wiersz:

- `state_schema_revision=1`, `revision=0`, czasy i digests `null`;
- active/passive `ok=0`, kody `ACTIVE_NOT_RUN`/`PASSIVE_NOT_RUN`;
- wszystkie osiem bramek i `plan_ready=0`;
- `readiness_codes_json` zawiera kanonicznie wszystkie kody brakujących bramek,
  zaczynając od `ACTIVE_PROBE_MISSING`;
- `last_collector_code=COLLECTOR_NOT_RUN`, lease `null`;
- contract revision ma exact wersję obsługiwaną przez kod expand.

Nie ma wartości domyślnej, która może dać `READY`. Powtórzenie patcha weryfikuje
istniejący wiersz i niczego nie nadpisuje; konflikt lub drugi wiersz zatrzymuje
migrację.

## Lease collectora

RQ job ID z Frappe ogranicza podwójne enqueue, ale nie zastępuje lease po crashu,
retry, ręcznym uruchomieniu ani wielu workerach. Collector używa dlatego lease w
tym samym wierszu.

1. W krótkiej transakcji odczytuje stały wiersz przez
   `frappe.db.get_value(..., for_update=True)` bez cache.
2. Waliduje cały minimalny shape, `revision`, oba pola lease i kanoniczny UTC.
3. Lease można przejąć wyłącznie, gdy oba pola są null albo exact expiry jest
   wcześniejsze od przechwyconego raz `acquired_at_utc`. Równość oznacza nadal
   zajęty lease, aby granica była jednoznaczna.
4. Worker generuje 256-bitowy losowy token, przechowuje wyłącznie jego SHA-256 i
   bounded expiry krótszy niż timeout joba oraz interwał schedulera.
5. Zapisuje lease i commit. Nie zmienia `revision`, evidence ani planu.
6. Probe oraz pozostałe odczyty odbywają się poza transakcją lease; nie wolno
   trzymać blokady DB podczas I/O lub aktywnego canary.
7. Raw token nie trafia do Frappe doc, logu, job kwargs, telemetry ani błędu.

Brak lease daje code-only `COLLECTOR_LEASE_BUSY` w telemetrii, ale przegrany
worker nie modyfikuje wiersza należącego do zwycięzcy. Stary snapshot nadal
podlega zwykłemu limitowi freshness; lease nigdy go nie przedłuża.

## Transactional CAS snapshotu

Po zebraniu evidence worker buduje DTO i pure plan w pamięci. Finalizacja jest
drugą krótką transakcją:

1. `SELECT ... FOR UPDATE` dokładnego wiersza po stałym primary key;
2. ponowna walidacja całego wiersza;
3. wymagane jednocześnie: `revision == expected_revision`, stored token hash
   odpowiada tokenowi workera i lease nie wygasł względem jednego
   `finalized_at_utc`;
4. update **wszystkich** pól snapshotu, `revision=expected_revision+1`,
   wyczyszczenie obu pól lease;
5. ponowna walidacja rowcount/odczytanej rewizji i commit;
6. zero/multiple row albo mismatch powoduje rollback i
   `COLLECTOR_CAS_CONFLICT`, nigdy częściowy update.

Zwykłe `doc.save`, `db_set` lub niezabezpieczone `db.set_value` nie są CAS.
Prywatny store może użyć parametryzowanego update pod blokadą, ale nie obchodzi
walidacji DTO. Nie wykonuje `commit` caller requestu; działa wyłącznie we własnym
scheduled jobie. Frappe wrapper commit/rollback joba jest dodatkową granicą, nie
zamiennikiem dwóch jawnych krótkich transakcji lease/finalizacji.

Przy konflikcie worker zwalnia wyłącznie lease, który nadal należy do jego tokenu;
nie czyści cudzego lease. Retry ma bounded backoff z jitterem i ponownie zbiera
evidence — nie zapisuje starego planu przeciw nowej rewizji.

## Scheduler

Docelowo istnieje jeden hook, bez osobnego joba active probe:

```python
scheduler_events = {
    "cron": {
        "* * * * *": ["kuck_serwis.audit_readiness_collector.collect"]
    }
}
```

Nazwa modułu jest kontraktowa na potrzeby ADR; kod nie istnieje. Collector działa
co minutę, wykonuje pasywne kontrole za każdym razem, a active canary tylko gdy
nie ma poprawnego wyniku albo od ostatniego wykonania minęło co najmniej 300 s.
Planner zawsze dostaje `max_probe_age_seconds=600`.

Hook jest rejestrowany dopiero w etapie expand po dodaniu implementacji, przez
standardową synchronizację `Scheduled Job Type`. Verify sprawdza exact method,
frequency `Cron`, cron format, `stopped=0`, działający scheduler i świeżość
snapshotu. Obecność rekordu joba bez kolejnych snapshotów nie jest sukcesem.

Frappe może pominąć enqueue, gdy job jest już w kolejce albo scheduler/site jest
nieaktywny. Takie zachowanie jest bezpieczne tylko dlatego, że request ponownie
ocenia freshness; brak joba przez 601 s daje fail-closed. Collector ma timeout
krótszy niż 60 s, single-flight lease i nie uruchamia nieograniczonego retry.

## Pasywny monitor

Pasywny monitor jest częścią collectora i nie wywołuje publicznego endpointu.
W bounded odczytach sprawdza:

- dostępność izolowanego połączenia i tabeli audytu;
- exact pola/constraints/permissions/append-only contract;
- wymagany indeks purge;
- brakujące/duplikowane `Naprawa.public_id` i nieznane statusy;
- konfigurację i świeżość purge, czytelność holdów;
- odbiór bieżących metryk oraz brak awarii sinka w oknie 5 min.

Sukces ma `PASSIVE_OK` i czas nie starszy niż 120 s w momencie requestu. Pierwsza
porażka ustawia odpowiednią bramkę false, nawet jeżeli alert ma jeszcze poziom
warning. Wynik zawiera jeden najbardziej pierwotny kod według stałej kolejności;
pełne liczniki są code-only telemetry, nie payloadem wiersza.

Pure `plan_passive_probe_freshness_v1()` wiąże punktowy wynik z exact progiem
`120 s` oraz digestem zaakceptowanej polityki v1 z ADR 0004. Wyłącznie
`PASSIVE_OK` o wieku `0..120 s` daje `FRESH`; brak wyniku, porażka, czas z
przyszłości albo wiek `>120 s` są fail-closed. Plan nie czyta zegara ani runtime,
nie ustawia żadnej z ośmiu bramek i ma wszystkie flagi purge, delivery,
activation, capability oraz readiness literalnie false. Collector, trwałość,
alert streak i kompozycja pełnego readiness nadal nie istnieją.

### Read-only existing-DB preflight (G0-218)

`collect_existing_db_preflight_v1()` jest ciemnym, wywoływanym jawnie
preflightem istniejącej bazy, a nie collectorem pasywnego monitora. Korzysta z
izolowanego połączenia audytu, wykonuje tylko stałą allowlistę `SELECT` i
`EXPLAIN SELECT`, nie steruje transakcją i zwraca wyłącznie uporządkowane kody.
Dowodzi osobno obecności pól, unikalnych kluczy, braku uprawnień DocPerm,
indeksu purge oraz tych negatywnych kontroli danych, dla których plan jest
indeksowy i mieści się w budżecie 10 000 szacowanych wierszy.

Brak dowodu bounded planu nie uruchamia zapytania do danych. W szczególności
obecny check nieznanego statusu kończy się `STATUS_DATA_NOT_PROVEN`, gdy
`EXPLAIN` pokazuje pełny skan; nie jest to dowód poprawności statusów. Dialekty
inne niż MariaDB są w v1 jawnie `DATABASE_DIALECT_NOT_PROVEN`. Wynik nie buduje
`PassiveProbeObservations`, nie może zwrócić `PASSIVE_OK`, nie komponuje
readiness i ma wszystkie flagi assessment, purge, delivery, activation,
capability oraz readiness literalnie false. Nie ma hooka, schedulera, zapisu,
metryki ani automatycznego wywołania. Dodanie indeksu, obsługi kolejnego
dialektu albo użycie kodów jako pełnego passive evidence wymaga osobnego etapu
i ponownej walidacji na realnym site.

## Exact odczyt w `_is_ready()`

Przyszła implementacja zachowuje `_account_read_enabled()` jako nadrzędną bramkę
literalnego rollout flag. `_is_ready()` wykonuje w tej kolejności:

1. jeżeli build-time `_AUDIT_AND_MONITORING_READY` nie jest literalnym `True`,
   zwraca `False`;
2. bez cache odczytuje dokładnie jeden wiersz o stałej nazwie; brak, drugi wiersz,
   zły schema revision lub błąd daje `False`;
3. defensywnie waliduje wszystkie typy, limity, allowlisty, związki nullability,
   monotonic revision oraz zgodność contract/policy/release revision z bieżącym
   zatwierdzonym runtime;
4. wymaga `passive_probe_ok`, `PASSIVE_OK` i wieku pasywnego wyniku `<=120 s`;
5. buduje `ActiveProbeEvidence` tylko przy kompletnym timestamp/version/code;
   następnie buduje exact `AuditReadinessEvidence` z ośmiu bramek;
6. wywołuje `plan_audit_readiness` z jednym bieżącym kanonicznym UTC i
   `max_probe_age_seconds=600`;
7. porównuje przeliczony plan ze stored `plan_ready` i kanonicznym codes JSON;
   mismatch oznacza uszkodzenie i `False`, nie automatyczną naprawę;
8. wykonuje istniejące kontrole sink/audit key, cursor key,
   `Naprawa.public_id`, unikalnego indeksu i `STATUS_MAP`;
9. zwraca `True` wyłącznie po przejściu wszystkiego; każdy wyjątek jest
   sanitizowany i zwraca `False`.

Odczyt nie używa `FOR UPDATE`, nie odnawia lease, nie zapisuje `modified`, nie
wykonuje canary ani zewnętrznego I/O. Jeden wiersz daje spójny snapshot w ramach
pojedynczego SELECT. Cache, jeśli później zatwierdzony, musi być porównany z
rewizją w bazie; błąd bazy zawsze wygrywa i daje `False`.

## Kill switch i rollout

`_AUDIT_AND_MONITORING_READY` pozostaje obecnie `False`. W przyszłym zatwierdzonym
release może być wyłącznie build-time kill switchem, domyślnie false. Jego
ustawienie na true wymaga code review oraz pełnej macierzy, ale nadal nie może
ominąć dynamicznego snapshotu, istniejących kontroli ani rollout flag.

Kolejność aktywacji:

1. schema, collector i hook wdrożone przy build kill switch false oraz rollout
   flag false;
2. staging i production dark generują snapshoty, lecz `features: []`;
3. po zatwierdzonych testach osobny release ustawia build kill switch true,
   nadal z rollout flag false;
4. staff przez minimum 24 h;
5. 5% → 25% → 100%, każdorazowo ze świeżym snapshotem i pojednaniem.

Operator nie może ręcznie ustawić pól readiness ani `plan_ready`. Flaga rollout
jest odrębnym natychmiastowym kill switchem i nigdy nie wymusza true.

## Retencja stanu readiness

DocType przechowuje wyłącznie bieżący snapshot, nadpisywany atomowo przez CAS;
nie jest append-only historią. Nie tworzymy drugiego DocType historii, aby
utrzymać minimalną liczbę schematów i uniknąć nowego zbioru operacyjnego.

Stan nie podlega codziennemu purge audytu. Jest przechowywany przez cały okres
istnienia capability, uwzględniany w backup/restore site i usuwany wyłącznie w
osobnym, zatwierdzonym decommission. Restore unieważnia readiness przez mismatch
release/policy albo jawne ustawienie bramek false przed ruchem. Historia zmian
jest code-only w metrykach/logu operacyjnym zgodnie z ich osobno zatwierdzoną
retencją; nie włączamy `track_changes`, które mogłoby utrwalić dodatkowy payload.

Wartości prawne retencji eventów, backupów i telemetrii pozostają otwarte. Pole
`retention_signed_off` potwierdza exact zewnętrzną rewizję polityki, ale jej nie
definiuje.

## Migracja expand/verify/constrain

### Expand

1. Utwórz DocType z `permissions=[]`, kontrolerami blokującymi framework writes
   i bez hooka schedulera.
2. Idempotentny patch tworzy stały wiersz revision 0 w pełni fail-closed.
3. Wdróż prywatny store, mapper probe, pasywny monitor i collector; build kill
   switch oraz rollout flag pozostają false.
4. Dodaj cron hook i uruchom standardową synchronizację jobów na staging.

### Verify

1. Exact schema, primary key, jeden wiersz, permissions, guards i wartości
   początkowe.
2. Exact Scheduled Job Type, cron i działający worker bez duplikatów.
3. Minimum 24 h snapshotów dark, active/passive freshness, CAS conflicts zero
   albo wyjaśnione oraz code-only telemetry.
4. Test retention/legal hold, alert route, backup/restore i rollback drill.
5. Pełna regresja `kuck_serwis` i kontraktu konsumenta.

### Constrain i aktywacja

Po verify kolejna rewizja schematu ustawia wymagane `reqd`, długości i Select
allowlisty, jeżeli expand musiał pozostawić pola nullable dla kompatybilności.
Ponowny verify sprawdza zero wartości legacy/unknown. Dopiero osobny release może
ustawić build kill switch true; rollout flag nadal pozostaje false do staff.

Migracja nie czyta sekretów, nie uruchamia active canary, nie ustala approval i
nie włącza feature. Awaria dowolnego kroku zatrzymuje migrate przed aktywacją.

## Rollback

1. Natychmiast ustaw rollout flag na literalne false i potwierdź `features: []`.
2. Przy problemie z kodem collectora wyłącz jego Scheduled Job Type/hook w
   kontrolowanym release, ale zachowaj sink, eventy, stan, holdy i telemetry.
3. Nie usuwaj DocType, nie zmniejszaj revision i nie wykonuj downgrade danych.
4. Jeżeli collector nadal może bezpiecznie pisać, CAS snapshot false z kodem
   allowlisty; jeśli baza jest niedostępna, request fail-closed bez cache.
5. Revert build kill switch do false w kolejnym release, jeżeli dynamiczny odczyt
   jest podejrzany. Rollout flag daje ochronę natychmiast do czasu deployu.
6. Ponowna aktywacja wymaga zgodnych contract/policy/release digests, świeżych
   probe, pełnego verify i etapu dark odpowiedniego do incydentu.

Rollback nie zwalnia legal hold, nie uruchamia purge, nie usuwa evidence i nie
wyłącza alertów.

## Macierz testów akceptacyjnych

Poniższe testy są wymaganiami przyszłej implementacji, nie wynikiem obecnego ADR.
Używają syntetycznych danych i odrębnego site.

### Schema i walidacja

1. DocType ma exact nazwę, pola, typy, limity, `permissions=[]` i wyłącznie PK
   `name` jako indeks.
2. Patch tworzy jeden wiersz o exact nazwie i revision 0; retry niczego nie
   zmienia.
3. Brak wiersza, drugi wiersz lub inna nazwa daje fail-closed i zatrzymuje verify.
4. Wszystkie booleany początkowo są DB 0; żadna wartość domyślna nie daje true.
5. Framework insert/update/delete/rename/import są odrzucone również z
   `ignore_permissions=True`.
6. Zły schema revision, ujemna/za duża revision i bool poza `0|1` są odrzucone
   kodem bez echa.
7. Każdy UTC odrzuca offset, fractional seconds, whitespace, złą datę i czas w
   przyszłości tam, gdzie kontrakt go zabrania.
8. Digests odrzucają uppercase, złą długość, znaki poza lowercase hex i bool.
9. Contract revision odrzuca Unicode, whitespace, separator spoza allowlisty i
   przekroczenie 64 znaków.
10. Codes JSON odrzuca unknown, duplikat, złą kolejność, dodatkową strukturę,
    niekanoniczny JSON i ponad 1024 bajty.
11. `READY` z innym kodem albo przy `plan_ready=0` jest odrzucony.
12. Lease ma oba pola null albo oba poprawne; stan połowiczny jest nieważny.

### Lease i CAS

13. Pierwszy worker przejmuje pusty lease i commit nie zmienia revision/evidence.
14. Drugi worker przed expiry nie uruchamia active probe ani nie modyfikuje row.
15. Granica `acquired_at == lease_expires_at` pozostaje zajęta; sekundę później
    pozwala na takeover.
16. Raw lease token nie występuje w DB, job kwargs, repr wyniku, logu ani metryce.
17. Stary worker po takeover nie może zapisać snapshotu ani wyczyścić nowego
    lease.
18. Final CAS wymaga exact revision, token i niewygasły lease; mismatch daje zero
    zmian i rollback.
19. Udany CAS zmienia cały snapshot, revision dokładnie +1 i czyści oba pola
    lease w jednym commit.
20. Wymuszona awaria w połowie update nie pozostawia mieszanki starego i nowego
    evidence.
21. Dwa równoległe final CAS: dokładnie jeden wygrywa; stary READY nie nadpisuje
    nowszego false.
22. Overflow revision zatrzymuje collector bez wraparound.
23. Deadlock/lock timeout ma bounded retry i po limicie nie przedłuża freshness.

### Scheduler i probe

24. Sync hooka tworzy exact jeden aktywny Scheduled Job Type z cron `* * * * *`.
25. Kolejne sync nie duplikuje joba; usunięcie hooka w rollback usuwa/wyłącza
    wyłącznie właściwy job.
26. Collector co minutę wykonuje passive, a active nie częściej niż co 300 s.
27. Brak active wyniku wymusza canary przy pierwszym collector run.
28. Exact `audit_health` sukces mapuje do active DTO; dodatkowe pole/kod lub inna
    wersja daje false.
29. `emit=True` bez jednego identycznego wiersza na nowym połączeniu nie zapisuje
    active success.
30. Active wiek 600 s przechodzi, 601 s nie; pure passive freshness plan związany
    z exact policy v1 przyjmuje wyłącznie `PASSIVE_OK` w wieku `0..120 s`, a brak,
    failure, przyszłość i wiek 121 s odrzuca bez ustawiania readiness.
31. Pierwsza porażka active/passive zapisuje false mimo warning severity alertu.
32. Scheduler disabled, dormant/skipped enqueue, queue outage i crash workera nie
    zachowują READY po expiry.
33. Job success commit i exception rollback są potwierdzone na przypiętym Frappe;
    jawne transakcje lease/CAS nie commitują cudzej pracy.

### Request path, rollout i operacje

34. `_is_ready()` nie używa cache, nie zapisuje, nie odnawia lease i nie uruchamia
    probe.
35. Brak DB, zły row shape, unknown code lub exception daje `False` bez
    tracebacka i bez danych stanu.
36. Request przelicza planner: stored READY staje się false po 601 s bez nowego
    scheduler run.
37. Stored plan różny od przeliczonego daje false, nie samonaprawę.
38. Niezgodny contract, policy lub release digest daje false.
39. Pasywny failure, brak indeksu, duplikat public ID lub nieznany status daje
    false mimo active success.
40. Build kill switch false zawsze daje `features: []` bez odczytu stanu.
41. Rollout flag false zawsze daje `features: []`; true nie omija żadnej bramki.
42. Guest i obcy użytkownik nie mogą odczytać DocType ani stanu przez API/Desk.
43. Snapshot, telemetry i błędy nie zawierają PII, ID, hashy, hostów, ścieżek,
    payloadów ani arbitrary exception text.
44. Restore starego backupu unieważnia release/policy i nie aktywuje feature.
45. Rollback flagi jest natychmiastowy, zachowuje sink, holdy, monitoring i stan.
46. Expand oraz constrain pozostawiają feature wyłączony; tylko osobny release i
    rollout mogą przejść do staff.
47. Minimum 24 h dark/staff i etapy 5%/25%/100% wymagają świeżego snapshotu oraz
    pojednania liczników.
48. Pełne testy `kuck_serwis`, public contract i konsument `kuck_shop` pozostają
    zielone po każdym etapie.

## Odrzucone alternatywy

- **Single DocType:** brak jednego naturalnego wiersza snapshotu do row lock/CAS.
- **Redis jako źródło prawdy:** utrata/eviction może przywrócić stary stan albo
  ukryć brak trwałego evidence.
- **Osobny DocType historii:** niepotrzebny drugi schema i większa retencja;
  code-only telemetry wystarcza dla przejść readiness.
- **Ustawienie `_AUDIT_AND_MONITORING_READY=True` bez stanu:** stała nie dowodzi
  freshness, approval, alertów ani CAS.
- **Ręczna edycja bramek:** omija źródła evidence i rozdziela snapshot.
- **Sam RQ dedupe bez lease:** nie chroni przed crash/takeover/ręcznym retry.
- **`db.set_value` bez row lock i revision compare:** nie daje kontraktu CAS.

## Decyzje i blokery

ADR nie ustala poniższych wartości. Każde `NIEUSTALONE` utrzymuje właściwą bramkę
false i blokuje aktywację:

| Właściciel | Decyzja / blocker | Stan |
|---|---|---|
| Kuck | Akceptacja modelu jednego prywatnego DocType i zakresu `account-read` | NIEUSTALONE |
| Kuck | Okna staff oraz rollout 5%/25%/100% i właściciel stop | NIEUSTALONE |
| Operations | Owner dyżuru, zastępstwo, kanał i SLA | NIEUSTALONE |
| Operations | Timeout/lease duration, retry budget i maintenance window | NIEUSTALONE |
| Operations | Backend metryk, pseudonimowy site ID i retencja telemetry | NIEUSTALONE |
| Operations | RPO/RTO, backup/restore i rollback owner | NIEUSTALONE |
| Legal/compliance | Wariant i okres retencji eventów/backupów | NIEUSTALONE |
| Legal/compliance | Role place/release hold i termin przeglądu | NIEUSTALONE |
| Security/release | Źródło oraz podpis/zatwierdzenie policy revision digest | NIEUSTALONE |
| Release | Źródło zatwierdzonego release manifest digest | NIEUSTALONE |
| Architecture | Zatwierdzenie build-time kill switch i exact collector contract revision | NIEUSTALONE |

Implementację blokuje ponadto osobny ADR/review prywatnego store, migracji i
hooka, syntetyczne fixtures, test na wszystkich wspieranych bazach oraz pełna
macierz powyżej.

## Konsekwencje

Pozytywne: jeden atomowy snapshot, minimalny schema, jednoznaczna freshness,
odporność na konkurencję i natychmiastowy rollout kill switch. Negatywne: dwa
krótkie commity w jobie, dodatkowy prywatny DocType, konieczność jawnych approval
sources i zależność aktywacji od zdrowego schedulera/bazy. Są to świadome koszty
fail-closed; nie można ich obchodzić cache ani ręcznym true.

## Walidacja dokumentu

Uruchamiana z repozytorium `apps/kuck_serwis`:

```bash
test -s docs/adr/0002-repair-audit-readiness-state.md
test -e kuck_serwis/audit_readiness.py
test -e kuck_serwis/audit_health.py
test -e kuck_serwis/public_contract/v1.py
test -e docs/runbooks/repair-audit-readiness.md
test -e docs/runbooks/repair-audit-operations.md
test -e ../frappe/frappe/core/doctype/scheduled_job_type/scheduled_job_type.py
test -e ../frappe/frappe/database/database.py
test -e ../frappe/frappe/database/query.py
rg -n '^## ' docs/adr/0002-repair-audit-readiness-state.md
rg -ni '(password|passwd|secret|api[_ -]?key|encryption_key)\s*[:=]' docs/adr/0002-repair-audit-readiness-state.md
git diff --check -- docs/adr/0002-repair-audit-readiness-state.md
git diff --no-index /dev/null docs/adr/0002-repair-audit-readiness-state.md
```

Skan sekretów ma zwrócić brak dopasowań. Ostatnia komenda zwraca `1`, ponieważ
pokazuje oczekiwany diff nowego pliku; inny kod oznacza błąd narzędzia.
