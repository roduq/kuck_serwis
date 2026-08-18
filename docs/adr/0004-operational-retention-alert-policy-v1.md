# ADR 0004 — operacyjna polityka retencji i alertów v1

Status: **ACCEPTED jako pure/dark policy; runtime pozostaje wyłączony**

Data: 2026-08-18

Właściciel biznesowy i operacyjny: `KUCK ZEGARKI BIŻUTERIA SP.J.`

Policy ID: `kuck-operational-evidence/v1`

Canonical policy SHA-256:
`d5f14c0bdc2a55ff0ea42319c7ccd3b218ed5c0d787a17e2c3cc287ee31591d1`

## Cel i zakres

Kuck jawnie delegował wybór rozsądnych okresów retencji backupów, technicznego
audytu i zdjęć napraw oraz progów i tras alertów dla małej organizacji. Ten ADR
utrwala wynik jako wersjonowany kontrakt wejściowy dla przyszłych adapterów.

Implementacja [operational_policy_v1.py](../../kuck_serwis/operational_policy_v1.py)
jest pure. Nie czyta czasu, konfiguracji, User, ról, bazy ani metryk; nie wysyła
maila, nie usuwa danych, nie tworzy schedulera i nie włącza capability. Każdy
plan, także z kompletnym evidence, ma literalnie:

```text
purge_authorized = false
delivery_authorized = false
activation_authorized = false
```

ADR nie zatwierdza okresu dokumentacji finansowej, podatkowej, payment review,
Payment Entry ani księgowego ledgeru. Te rekordy pozostają bez purge do osobnej
decyzji księgowo-prawnej z właściwą kotwicą ustawowego okresu.

## Retencja v1

| Klasa evidence | Okres | Exact kotwica | Dodatkowe zabezpieczenie |
|---|---:|---|---|
| `REPAIR_AUDIT_EVENT` | 180 dni | immutable `created_at`/`creation` | legal hold |
| `READINESS_ALERT_EVIDENCE` | 180 dni | immutable `created_at` | legal hold |
| `OPERATIONAL_METRICS` | 90 dni | immutable `created_at` | legal hold |
| `REPAIR_PHOTO` | 1095 dni | immutable `repair_terminal_at` | legal hold i cały istniejący photo evidence gate |
| `SEO_SNAPSHOT` | 90 dni | immutable `created_at` | legal hold oraz current/N-1/newest safety floor z istniejącego plannera |

Nieznana klasa, brak kotwicy, niepoprawna rewizja polityki, nieczytelny hold lub
niepełne evidence oznaczają `KEEP/BLOCKED`, nigdy automatyczne dopasowanie.

Dla naprawy `Wydano` przyszły adapter może wyprowadzić terminalną kotwicę z
utrwalonego `data_wydania` dopiero po zatwierdzeniu exact konwersji daty do UTC.
Dla `Anulowano` obecny model nie ma niezmiennego terminal timestampu, więc
zdjęcia pozostają `KEEP` do addytywnego, osobno zatwierdzonego schematu. `modified`
nie jest bezpiecznym substytutem.

Istniejące `repair_photo_retention.py` pozostaje `DRY_RUN_ONLY`. Nadal wymaga
prywatnego zdjęcia, exact attachment oraz dokładnie jednego child/File/blob
reference. Ta polityka nie osłabia żadnego z tych warunków i nie dodaje delete.

## Backup i restore

Każdy set jest pełny: DB, public/private files i chroniona konfiguracja, z
checksumami. Obowiązują istniejące wymagania `umask 077`, katalog `0700` i pliki
`0600`.

| Klasa | Retencja |
|---|---:|
| daily | 14 dni |
| weekly | 56 dni |
| monthly | 180 dni |
| izolowany restore clone | maksymalnie 7 dni po zaakceptowaniu drill |

Wymagane cele operacyjne:

- co najmniej dwie kopie na odrębnych failure domains, jedna off-host;
- RPO 24 h;
- RTO 8 h;
- pełny restore drill co 90 dni;
- żaden backup bez aktywnego holda nie przekracza 180 dni;
- restore zaczyna się z wyłączonym outbound, schedulerem, mailami i webhookami;
- przed ruchem po restore należy odczytać bieżącą politykę i holdy, wykonać
  retention dry-run/re-purge, reconciliation i świeże probe.

Backup nie jest ukrytym archiwum. Nieczytelne mapowanie legal hold do setu
zatrzymuje rotację odpowiedniego zakresu i generuje alert critical. Ten ADR nie
wykonuje backupu, restore, rotacji ani teardown clone.

## Legal hold i segregacja obowiązków

Legal hold ma bezwzględne pierwszeństwo przed każdą retencją. Stan `UNKNOWN`,
nieczytelny rejestr, zła rewizja, niepełne mapowanie lub przeterminowany przegląd
oznaczają `KEEP`, zatrzymanie purge i critical.

- Hold nie wygasa automatycznie.
- Maksymalny interwał przeglądu wynosi 90 dni.
- Przyszły system powinien przypominać 7 dni przed terminem.
- Place i release pozostawiają append-only action.
- Operator purge nie może zwolnić holda.
- Minimalne SoD wymaga wskazanego approvera innego niż wykonujący purge.
- Release wyłącznie ponownie kwalifikuje rekord do dry-run; nie wykonuje delete.

Ten kontrakt przyjmuje jedynie code-only evidence gotowości rejestru. Nie tworzy
hold DocType, ról ani capability i nie twierdzi, że mechanizm obecnie istnieje.

## Routing odbiorców i model ról

Minimalną rolą routingu jest `Kuck Store Moderator`. Prywatna przyszła
konfiguracja wskazuje aktywnych System Users jako primary i escalation, a adresy
rozwiązuje dopiero warstwa transportowa. Wymagani są co najmniej dwaj różni,
aktywni użytkownicy z poprawnym adresem: minimum jeden primary i jeden
escalation.

Rola alertowa nie nadaje prawa do raw eventów, zdjęć, holdów, backupu ani purge.
Późniejsze role `Retention Operator`, `Compliance Approver`, `Backup Operator`
i `Security Auditor` wymagają osobnego kontraktu RBAC/JIT/MFA i nie są tworzone
przez ten slice.

Test delivery musi przejść po każdej zmianie routingu oraz przynajmniej co 30
dni. Brak odbiorcy, nieaktywny User, błędny adres, stale test lub failure
transportu daje `alerting_owner_ready=false`.

## Eskalacja

| Severity | Exact przebieg od pierwszej obserwacji, o ile brak ACK/resolution |
|---|---|
| `WARNING` | 0 s primary; 4 h escalation; 24 h wszyscy i potem raz dziennie |
| `CRITICAL` | 0 s primary 24/7; 15 min escalation; 60 min business owner; 4 h wszyscy i potem co 4 h |

Pure wynik zawiera harmonogram, ale `delivery_authorized=false`. ACK, suppress,
deduplikacja, resolved i transport wymagają przyszłego trwałego adaptera.

## Progi i freshness

Granice czasu są domknięte: dokładna wartość limitu nie alarmuje; dopiero
`limit + 1` przekracza limit, chyba że tabela jawnie używa progu `>=`.

| Sygnał | Warning | Critical |
|---|---|---|
| awarie audit sink / 5 min | — | każdy przyrost |
| aktywny probe | pierwsza porażka | 2 kolejne lub success age `>600 s` |
| pasywny probe | pierwsza porażka | 2 kolejne lub success age `>120 s` |
| collector | success age `>90 s` | `>120 s` |
| exporter metryk | — | success age `>120 s` |
| invalid/duplicate `public_id`, unknown status | — | count `>0` |
| `DEPENDENCY_UNAVAILABLE` / 5 min | count `>=5` i rate `>1%` | count `>=20` lub rate `>5%` |
| contract latency | p95 `>500 ms` / 10 min | p95 `>1000 ms` / 5 min |
| audit purge | success age `>26 h` lub cutoff overdue `>24 h` | `>48 h` lub `>72 h` |
| photo dry-run | success age `>26 h` | `>48 h` |
| photo apply | success age `>8 dni` | `>15 dni` lub oldest eligible `>14 dni` |
| hold registry/unknown/overdue | — | dowolna awaria/count `>0` |
| verified backup | age `>26 h` | `>48 h` albo dowolny integrity/ACL/component failure |
| restore drill | age `>90 dni` | `>100 dni` |
| capacity | `>=80%` lub dojście do 80% w 1–30 dni | `>=90%` |
| alert routing | — | dowolny delivery/configuration failure |

Każdy warning albo critical daje `readiness_evidence_ok=false`. Alert nie steruje
capability; przyszły collector musi osobno zapisać fail-closed evidence.

## No-PII telemetry

Dozwolone pola metryk i alert evidence to zamknięte code-only enums, severity,
czasy/age, bounded counts/rates, policy/probe revision i pseudonimowy `site_id`.

Zakazane są: e-mail odbiorcy, User, Customer, `Naprawa.name`, repair/file/hold ID,
adres, telefon, hostname, URL, correlation ID, actor/repair hash, SQL, payload,
arbitralny status, exception i traceback. Adres odbiorcy może istnieć wyłącznie
w prywatnej konfiguracji i warstwie transportu; nie trafia do policy digest,
metryk, alert body ani logu aplikacji.

## Readiness planner

`plan_operational_policy_readiness_v1()` wymaga evidence związanych z exact
policy SHA:

1. business, legal i operations approval;
2. moderator role, primary/escalation, dwóch różnych enabled User i świeżego
   testu delivery;
3. czytelnego hold registry, approvera, SoD, exact 90-day review i zero
   unknown/overdue;
4. off-host backup, exact RPO/RTO/drill target, backup age nie większy niż 26 h
   i restore drill age nie większy niż 90 dni.

Komplet może dać `policy_ready=true`, ale nigdy nie daje zgody na purge,
delivery ani activation. Digest mismatch jest błędem code-only, a nie zwykłą
brakującą bramką.

## Dark preflight istniejącego inventory zdjęć

`repair_photo_retention_preflight.py` składa istniejący, bounded i count-only
`RepairPhotoInventoryReport` w code-only dowód częściowy. Nie wykonuje nowego
zapytania: collector deleguje dokładnie jeden świeży odczyt do
`collect_repair_photo_inventory()`, z jego istniejącymi limitami i kontrolą
izolacji. Nie używa cache ani zapisanego wcześniej sukcesu.

Kompletny inventory bez publicznych lub malformed referencji, niepoprawnych
child identities, luk private File binding, duplikatów i orphan File może dać
`inventory_evidence_ok=true`. Jedynym kodem takiego wyniku jest
`EXISTING_INVENTORY_PARTIAL_EVIDENCE`: nadal nie dowodzi lifecycle anchor,
blob cardinality, legal hold, zatwierdzenia polityki ani gotowości purge.

Truncation, błąd odczytu, unsafe isolation, malformed wynik lub wyjątek są
fail-closed. Wynik nie zawiera identyfikatorów napraw, File, URL, Customer/User,
SQL ani tekstu wyjątku. Wszystkie pola `retention_evidence_ok`, assessment,
dry-run, purge, download, activation i capability pozostają literalnie false.

Preflight jest prywatnym diagnostycznym odczytem, nie publicznym endpointem.
Nie emituje audit eventu, ponieważ w tym pionie nie ma operacji dostępu ani
zapisu. Przyszły scheduler i code-only telemetry wymagają osobnego zadania.

## Nadal otwarte przed runtime

- exact User IDs i adresy primary/escalation;
- osoby Compliance Approver, purge/backup operator i dowód SoD;
- podstawa/cel prawny dla 180 dni audytu i 1095 dni zdjęć;
- retencja i kotwica payment/ERPNext evidence;
- immutable timestamp anulowania naprawy;
- off-host backend, szyfrowanie/key custody i capacity evidence;
- schema hold/ACK/readiness, adaptery, scheduler/hook i alert transport;
- realny backup/restore, purge rehearsal i test routingu;
- osobna zgoda schema/migrate/rollout.

Brak powyższych danych nie zmienia wartości polityki, ale utrzymuje odpowiednie
evidence jako false i wszystkie capability wyłączone.

## Granice wdrożonych dark slice'ów

Wdrożone są pure policy/plannery oraz read-only inventory preflight. Zakazane
pozostają zmiany `hooks.py`, DocType, patches, `install.py`, schema, site,
scheduler, mail, metrics backend, backup/restore, purge,
`_AUDIT_AND_MONITORING_READY` i photo public capability.

Testy potwierdzają exact digest, okresy, progi i granice, fail-closed hold,
minimum dwóch odbiorców, stable ordering/kody, redacted DTO, brak PII fields
oraz bezwarunkowo fałszywe flagi wykonawcze. Preflight dodatkowo testuje pełne
mapowanie istniejących counters, canonical code order, truncation, błędy
collectora i brak jakiegokolwiek uprawnienia wykonawczego.
