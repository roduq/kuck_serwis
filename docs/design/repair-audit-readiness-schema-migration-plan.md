# Plan migracji schematu readiness audytu napraw

Status: plan wykonawczy do review; `BLOCKED`; nie zatwierdza ADR 0002 ani
architektury w nim proponowanej

Data: 2026-08-14

## Cel i granica decyzji

Dokument opisuje odwracalną sekwencję `expand → verify → constrain` dla
proponowanego w ADR 0002 prywatnego, zwykłego DocType
`Kuck Repair Audit Readiness State` i logicznego singletonu: jednego wiersza
`name=repair-audit-readiness-v1`. Nie jest to Frappe Single DocType. DocType ma
pozostać `issingle=0`; dokładnie jeden wiersz jest inwariantem store, patcha i
verify.

Plan nie:

- akceptuje statusu `proposed` ADR 0002;
- tworzy DocType, pól, kontrolera, patcha, hooka ani Scheduled Job Type;
- uruchamia `bench migrate`, backupu, restore, collectora ani canary;
- ustala retencji, legal hold, alertów, lease, retry, RPO/RTO ani rollout;
- zmienia `_AUDIT_AND_MONITORING_READY=False`, flagi rollout lub `features: []`;
- upoważnia do operacji na produkcji.

Każde `NIEUSTALONE` z ADR i runbooka pozostaje blockerem. Ten plan można
wykonać dopiero po osobnej akceptacji architektury, schema, patchy, testów i
okna migracji.

## Źródła i przypięte fakty

Źródła projektowe:

- [ADR 0002 — trwały stan readiness](../adr/0002-repair-audit-readiness-state.md),
  status `proposed`;
- [runbook readiness](../runbooks/repair-audit-readiness.md);
- [runbook operacji audytu](../runbooks/repair-audit-operations.md);
- [pure planner readiness](../../kuck_serwis/audit_readiness.py);
- [aktywny probe](../../kuck_serwis/audit_health.py);
- [public contract v1](../../kuck_serwis/public_contract/v1.py);
- [aktualne hooki](../../kuck_serwis/hooks.py);
- [aktualny rejestr patchy](../../kuck_serwis/patches.txt).

Plan opiera się na lokalnym Frappe
`5c16f12192815204a7eda2b2ab365a557a6e7def`:

- [`SiteMigration`](../../../frappe/frappe/migrate.py) wykonuje kolejno
  `before_migrate`, `pre_model_sync`, `sync_all`, `post_model_sync`, a następnie
  `post_schema_updates`;
- [patch handler](../../../frappe/frappe/modules/patch_handler.py) zapisuje
  `Patch Log` i sam wykonuje commit albo rollback każdego patcha;
- [`sync_jobs`](../../../frappe/frappe/core/doctype/scheduled_job_type/scheduled_job_type.py)
  działa w `post_schema_updates`, po obu sekcjach patchy i po sync modeli;
- `sync_jobs` pomija nieimportowalną metodę z ostrzeżeniem, dlatego sam brak
  wyjątku z migrate nie dowodzi poprawnej rejestracji joba;
- obecny `hooks.py` nie deklaruje aktywnego `scheduler_events` dla collectora;
- obecny `patches.txt` ma jawne sekcje `pre_model_sync` i `post_model_sync`.

Wniosek transakcyjny: nie wolno traktować pełnego `bench migrate` jako jednej
transakcji. Udany preflight lub seed może być już committed, gdy późniejszy
verify zatrzyma migrate. Każdy stan pośredni musi być bezpieczny, idempotentny i
utrzymywać capability wyłączone.

## Blockery wejścia

Przed utworzeniem jakiegokolwiek artefaktu schema wszystkie poniższe pozycje
muszą mieć właściciela, exact decyzję, rewizję dowodu i akceptację. Brak jednej
pozycji jest `NO-GO`.

| Właściciel | Wymagana decyzja | Stan |
|---|---|---|
| Kuck | Akceptacja jednego prywatnego DocType, logicznego singleton row i zakresu `account-read` | NIEUSTALONE |
| Kuck | Zakres staff/canary, okna 5%/25%/100% oraz właściciel stop | NIEUSTALONE |
| Operations | Owner dyżuru, zastępstwo, kanał warning/critical i SLA | NIEUSTALONE |
| Operations | Lease/timeout collectora, retry budget, jitter i maintenance window | NIEUSTALONE |
| Operations | Backend metryk, pseudonimowy `site_id` i retencja telemetry | NIEUSTALONE |
| Operations | RPO/RTO, harmonogram backupu, restore drill i rollback owner | NIEUSTALONE |
| Legal/compliance | Wariant i okres retencji eventów, backupów i ewentualnego archiwum | NIEUSTALONE |
| Legal/compliance | Role place/release legal hold, maksymalny okres i przegląd holda | NIEUSTALONE |
| Security/release | Źródło, podpis i akceptacja `policy_revision_sha256` | NIEUSTALONE |
| Release | Źródło i akceptacja `release_manifest_sha256` | NIEUSTALONE |
| Architecture | Build-time kill switch i exact `readiness_contract_revision` | NIEUSTALONE |

Wymagane są ponadto osobne review: DocType/store, controller guards, patchy,
syntetycznych fixtures, wspieranych silników bazy oraz późniejszego hooka
schedulera. Rekomendacje z runbooka nie zastępują decyzji.

## Docelowe inwarianty, nie zgoda na schema

Jeżeli ADR 0002 zostanie zaakceptowany bez zmiany, przyszła implementacja musi
zachować poniższy kontrakt:

1. dokładnie jeden zwykły prywatny DocType, `issingle=0`, bez web view, Desk
   permissions, importu i indeksowania website;
2. dokładnie jeden wiersz o stałym primary key
   `repair-audit-readiness-v1`; brak albo dodatkowy wiersz jest konfliktem;
3. exact zestaw pól, typów, limitów i allowlist z sekcji „Pola i limity” ADR;
4. tylko PK `name` jako indeks wymagany dla tabeli singleton row; żadnego
   wtórnego indeksu bez osobnego dowodu query plan;
5. stan początkowy `revision=0`, bez timestampów i digestów, wszystkie bramki
   false, kody `*_NOT_RUN`, lease null i kanoniczne fail-closed codes JSON;
6. `permissions=[]`, stały techniczny actor oraz zakaz User, Customer,
   `Naprawa.name`, URL, path, payload, exception, tokenu lease i danych PII;
7. framework insert/update/delete/rename/import zablokowane także przy
   `ignore_permissions=True`; przyszły store jest jedyną granicą zapisu;
8. brak auto-create w request path, collectorze, `after_migrate` albo schedulerze;
9. brak wartości domyślnej, która może utworzyć `READY`;
10. build kill switch i rollout pozostają literalnie false przez wszystkie
    etapy schema.

Frappe `reqd`, `read_only`, `no_copy` i opcje Select są metadanymi modelu; plan
nie zakłada automatycznie fizycznego `NOT NULL` lub SQL `CHECK`, jeżeli przypięty
sync v16 ich nie materializuje. Każdy dodatkowy constraint SQL wymaga osobnego
review DDL, preflightu nazw i testu na wszystkich wspieranych bazach. Nie wolno
udawać constraintu aplikacyjnego jako fizycznego.

## Kandydackie artefakty i kolejność wydań

Nazwy poniżej są identyfikatorami planu do review, nie istniejącymi modułami ani
zgodą na ich utworzenie.

| Wydanie | Kandydacki artefakt | Rola | Aktywacja |
|---|---|---|---|
| E — expand | DocType/controller/store w trybie dark | Addytywny schema i write guards | zabroniona |
| E — expand | `preflight_repair_audit_readiness_expand_v1` | Pre-model collision preflight | brak |
| E — expand | `seed_repair_audit_readiness_state_v1` | Post-model idempotentny singleton row | brak |
| E — expand | `verify_repair_audit_readiness_expand_v1` | Post-model fail-closed verify | brak |
| V — verify | read-only verifier i runtime tests | Dowód schema/replay/rollback | brak |
| C — constrain | `preflight_repair_audit_readiness_constrain_v1` | Sprawdzenie danych przed sync bardziej ścisłego modelu | brak |
| C — constrain | docelowa rewizja metadanych DocType | `reqd`, allowlisty i limity zaakceptowanego ADR | brak |
| C — constrain | `verify_repair_audit_readiness_constrain_v1` | Post-model sprawdzenie target shape | brak |
| S — scheduler | collector oraz osobny cron hook | Dopiero po E/V/C i osobnym approval | nadal `features: []` |

Każdy patch ma unikalną pełną nazwę modułu w `patches.txt`. Nazwy nie są
przenoszone ani ponownie używane. `skip_failing` jest zabronione w acceptance i
release migrate.

### Exact kolejność w lifecycle Frappe

Po zaakceptowaniu nazw modułów wpisy mają zachować poniższą kolejność. Nie wolno
przenosić seed do `pre_model_sync` ani preflightu C za `sync_all`.

| Kolejność | Release E | Release C | Granica commit |
|---:|---|---|---|
| 1 | istniejące `before_migrate` | istniejące `before_migrate` | atomic wrapper Frappe |
| 2 | `preflight_repair_audit_readiness_expand_v1` w `pre_model_sync` | E preflight jest już w Patch Log; potem `preflight_repair_audit_readiness_constrain_v1` | każdy wykonany patch commitowany osobno |
| 3 | `frappe.model.sync.sync_all()` tworzy target E | `sync_all()` stosuje target C | atomic wrapper, z zastrzeżeniem wcześniejszych commitów patch handlera |
| 4 | trzy obecne post-model patche, jeśli nie są jeszcze w Patch Log | istniejące post-model patche są replay-safe/Patch Log skipped | każdy patch osobno |
| 5 | `seed_repair_audit_readiness_state_v1` | E seed jest już w Patch Log | osobny commit patcha |
| 6 | `verify_repair_audit_readiness_expand_v1` | E verify jest już w Patch Log; potem `verify_repair_audit_readiness_constrain_v1` | osobny commit patcha, domenowo read-only |
| 7 | `post_schema_updates`, w tym `sync_jobs`; bez hooka collectora | `post_schema_updates`, w tym `sync_jobs`; nadal bez hooka collectora | atomic wrapper Frappe |
| 8 | obecny `after_migrate` | obecny `after_migrate` | w `post_schema_updates` |

Nowe post-model wpisy E są dopisywane po obecnych
`przelacz_na_powiadom_per_kanal`, `backfill_kategoria_glowna` i
`backfill_naprawa_public_id`. Nie zmienia się kolejności ani treści istniejących
patchy. Release C dopisuje swoje wpisy po E; Patch Log gwarantuje, że stare
moduły nie wykonają się drugi raz, natomiast własny kod każdego patcha nadal
musi być idempotentny.

## Collision preflight przed expand

Preflight ma jeden kontrakt read-only używany w dwóch miejscach: obowiązkowy
release gate uruchamia go przed każdym `bench migrate`, a patch E wywołuje tę
samą logikę w `pre_model_sync`, zanim `sync_all` może utworzyć lub zmienić
tabelę. Jest read-only z wyjątkiem standardowego `Patch Log` zarządzanego przez
Frappe dla samego patcha. Odczyty są bounded, parametryzowane i zwracają tylko
kody/liczniki.

Zewnętrzny release gate jest konieczny, ponieważ Frappe pomija patch już obecny
w Patch Log. Gdy wpis patcha istnieje, lecz schema/row został później uszkodzony
albo usunięty, ponowny migrate nie uruchomi tego patcha. Gate przed migrate oraz
verifier po migrate muszą więc zawsze działać niezależnie od Patch Log; nie są
hookiem runtime ani obejściem lifecycle.

Preflight rozstrzyga exact cztery stany:

1. brak DocType metadata i brak fizycznej tabeli — dozwolony fresh expand;
2. exact, app-owned kandydat zgodny z bieżącą fazą — dozwolony replay;
3. częściowy, ale jednoznacznie app-owned stan po wcześniejszym E — dozwolony
   wyłącznie, gdy wszystkie już istniejące elementy odpowiadają targetowi E;
4. wszystko inne — stabilny `SCHEMA_COLLISION`, zero prób adopcji lub naprawy.

Fail-closed collision obejmuje co najmniej:

- Custom DocType albo DocType o tej nazwie z innego app/module;
- `issingle=1`, web view, Desk permissions lub inny owner metadanych;
- orphan table bez dokładnej metadanej albo metadata bez oczekiwanej tabeli na
  replay;
- kolumnę o tej samej nazwie z innym typem, dodatkowe pole własne, złą nazwę PK
  lub wtórny indeks nieujęty w zaakceptowanym schema;
- istniejący wiersz o innej nazwie, więcej niż jeden wiersz albo wiersz o
  nieobsługiwanej rewizji;
- wpis `Patch Log` twierdzący sukces przy braku odpowiadającego stanu;
- tabelę, rekord lub metadata zawierające nieoczekiwany shape, którego nie można
  przypisać wyłącznie do tej aplikacji.

Preflight nie wykonuje rename, `DROP`, `ALTER`, delete, truncate, backfill ani
automatycznej adopcji. Raport nie zawiera nazw kolumn spoza allowlisty,
wartości wiersza, SQL, ścieżek ani exception text.

## Wydanie E — expand

### E0 — dowody przed migrate

1. Zatwierdź exact candidate SHA sześciu aplikacji i clean worktree.
2. Potwierdź literalne false build kill switcha i rollout; smoke musi zwracać
   `features: []` przed dotknięciem schema.
3. Uruchom zawsze-on read-only collision/recovery preflight niezależny od Patch
   Log; jakikolwiek mismatch zatrzymuje wykonanie przed `bench migrate`.
4. Wykonaj pełny szyfrowany backup izolowanego staging site z checksumą; RPO/RTO
   i owner restore muszą być zatwierdzone, nie domyślne.
5. Odtwórz ten backup na oddzielnym site bez schedulera, maili, webhooków i ruchu
   klienta; dopiero udany restore smoke dopuszcza E.
6. Uruchom statyczny schema/patch test oraz collision fixtures bez bazy
   produkcyjnej.

### E1 — model addytywny

Po akceptacji ADR sync modelu może dodać exact prywatny DocType. Pola docelowo
required mogą w E pozostać nullable wyłącznie wtedy, gdy:

- target field set i typ już odpowiadają ADR;
- brak wartości legacy jest jawnie rozróżnialny od READY;
- seed wypełnia każdy field fail-closed przed końcem patcha;
- request path nadal nie odczytuje nowego stanu;
- późniejszy C ma jawny preflight zero-null i osobny verify.

Nie dodawać schedulera ani `scheduler_events` w E. Dzięki temu standardowe
`sync_jobs()` podczas migrate nie może uruchomić przyszłego collectora.

### E2 — idempotentny seed singleton row

Patch post-model działa tylko po utworzeniu exact tabeli i metadanych:

1. ponownie wykonuje bounded collision check;
2. przy zero wierszy tworzy exact `repair-audit-readiness-v1` z pełnym stanem
   początkowym ADR;
3. przy jednym exact wierszu waliduje wszystkie pola i wykonuje zero update;
4. przy innym/dodatkowym wierszu albo różnicy wartości zatrzymuje migrate;
5. potwierdza rowcount i odczytuje rekord z nowego query przed sukcesem;
6. nie ustawia timestampów, digestów, lease, READY ani approval;
7. nie uruchamia probe, collectora, scheduler sync ani zewnętrznego I/O.

Retry nigdy nie resetuje `revision`, nie nadpisuje istniejącego stanu i nie
„naprawia” różnicy. Seed zapisuje wyłącznie exact initial row albo nic.

### E3 — verify po seed

Kolejny post-model patch jest read-only dla domenowej tabeli. Weryfikuje target
E, jeden initial row, guards, permissions i brak nieplanowanych indeksów. Błąd
zatrzymuje migrate, ale seed może być już committed przez patch handler. Ten stan
jest akceptowalny wyłącznie dlatego, że schema jest prywatny, fail-closed, bez
schedulera i bez request read path.

Po udanym E należy ponownie uruchomić migrate na kopii staging. Drugi przebieg ma
wykonać zero domenowych write i pozostawić identyczny checksum kanonicznej
projekcji schema/row.

## Wydanie V — verify bez constrain

V jest osobną bramką dowodową, nie patchem aktywującym. Read-only verifier na
nowym połączeniu potwierdza:

- exact DocType metadata i physical columns wspierane przez Frappe v16;
- `issingle=0`, `permissions=[]`, brak web/search/Desk/import surface;
- exact jeden wiersz i primary key;
- initial values oraz kanoniczne JSON/UTC/allowlist validation;
- brak wtórnych indeksów i brak drugiej nazwy rekordu;
- skuteczność controller/store guards;
- request path nadal nie czyta tabeli, a `features: []` pozostaje bezwarunkowe;
- backup oraz restore zachowują schema i initial row, lecz nie aktywują feature;
- fresh install, upgrade z poprzedniego release oraz replay migrate dają ten sam
  target E.

Nie wolno przejść do C przy null, unknown code, innym row, nierozwiązanej
kolizji, braku restore drill albo dowolnym `NIEUSTALONE` wymaganym dla migration
window i rollback.

## Wydanie C — constrain

C jest kolejnym release, nie częścią pierwszego expand.

### C1 — pre-model preflight

Przed `sync_all` patch `preflight_repair_audit_readiness_constrain_v1` wymaga:

- dokładnie jednego target E row;
- zero null w polach docelowo required;
- exact schema revision i revision mieszczące się w bound;
- literalne DB `0|1` dla Check;
- kanoniczne UTC, digest, contract revision i codes JSON;
- wartości Select wyłącznie z zaakceptowanych allowlist;
- poprawną parę nullability lease;
- brak legacy/dodatkowego pola, dodatkowego wiersza i nieplanowanego indeksu;
- build kill switch oraz rollout nadal false.

Błąd kończy C przed sync target metadata. Patch nie backfilluje, nie cofa rewizji
i nie zmienia row.

### C2 — sync target metadata

Po udanym preflight `sync_all` stosuje zaakceptowane `reqd`, `read_only`,
`no_copy`, options Select oraz limity obsługiwane przez Frappe v16. Wprowadzenie
fizycznego constraintu poza standardowym sync jest poza C, dopóki osobny review
DDL nie potwierdzi nazw, backendów, lock impact i rollbacku.

### C3 — post-model verify

Post patch ponownie odczytuje metadata, physical schema i jeden row. Potwierdza
brak utraty danych, zmian revision i wartości oraz zero nowych permissions.
Następnie replay migrate ma wykazać zero domenowych write.

C nie ustawia build kill switcha, rollout, approval fields ani READY. Nie dodaje
hooka schedulera.

## Scheduler dopiero w osobnym wydaniu S

Scheduler jest świadomie oddzielony od E/V/C. Wydanie S wymaga osobnego review
collectora, lease/CAS, timeoutów, metryk, alertów i hooka oraz zamknięcia
właściwych `NIEUSTALONE`.

Sekwencja S:

1. deploy collectora i importowalnej metody przy obu kill switchach false;
2. statycznie zweryfikuj exact hook `cron`, expression `* * * * *` i jedną
   metodę collectora;
3. wykonaj standardowy migrate; Frappe `post_schema_updates` wywoła `sync_jobs`;
4. read-only verify wymaga dokładnie jednego `Scheduled Job Type` z exact method,
   `frequency=Cron`, cron format i oczekiwanym stanem `stopped`;
5. brak joba po ostrzeżeniu importu, duplikat albo near-miss method jest FAIL;
6. dopiero dark test potwierdza kolejne snapshoty; samo istnienie joba nie jest
   PASS readiness;
7. build kill switch i rollout nadal false.

Nie wolno ręcznie wywoływać `sync_jobs` z patcha seed/constrain. Rollback S to
kontrolowany release usuwający wyłącznie hook collectora i standardowy migrate,
po czym verify potwierdza brak dokładnie tego joba. Nie usuwa DocType ani row.

## Idempotencja i stany pośrednie

| Punkt awarii | Stan trwały | Bezpieczna reakcja |
|---|---|---|
| E preflight przed sync | brak zmian domenowych | usuń kolizję przez jawne ownership review; retry tego samego SHA |
| `sync_all` przed seed | tabela może istnieć, zero row | capability false; popraw przyczynę, retry E; request/collector nie tworzą row |
| seed po insert przed verify | exact initial row może być committed | pozostaw row; retry tylko waliduje, nigdy nie resetuje |
| verify E | exact albo konfliktowy expanded state | zatrzymaj release; zero aktywacji; restore tylko według zatwierdzonego planu |
| C preflight | target E bez zmian | popraw dane wyłącznie osobnym reviewed repair planem; nie w C |
| `sync_all` C | metadata może być target C | verify i replay; bez downgrade schema |
| verify C | target C lub jawny mismatch | capability false; forward fix, nie drop/downgrade |
| S sync job | schema C bez lub z jobem | verify exact job; hook rollback osobnym release |

Idempotencja wymaga zarówno Patch Log, jak i własnych inwariantów. Patch uznany
przez Patch Log, lecz bez exact stanu, jest blockerem recovery; nie wolno usuwać
Patch Log ani uruchamiać patcha `force` bez osobnej procedury incydentowej.

## Backup, restore i rollback

### Backup/restore gate

Przed E i C wymagany jest świeży backup site z checksumą oraz udany restore do
izolowanego site. Backup nie jest zgodą na przechowywanie danych bez końca;
retencja backupu pozostaje `NIEUSTALONE`.

Restore test:

1. wyłącza scheduler, maile, webhooki i ruch;
2. weryfikuje exact app versions i schema przed migrate;
3. wykonuje E albo C na kopii;
4. potwierdza jeden row, constraints i brak feature;
5. nie uruchamia active probe ani collectora;
6. nie eksportuje wartości row, site config ani credentiali;
7. po ponownym restore stare policy/release digests nie mogą dać READY.

### Rollback

Rollback jest addytywny i forward-only:

1. rollout pozostaje/ustawia się na literalne false; potwierdź `features: []`;
2. nie usuwa się tabeli, DocType, singleton row ani Patch Log;
3. nie zmniejsza się `revision` i nie wykonuje downgrade danych;
4. kod poprzedniego release musi tolerować nieużywany prywatny schema;
5. problem C naprawia kolejny forward patch kompatybilny z danymi;
6. problem S usuwa hook w osobnym release, pozostawiając schema i monitoring
   wymagany przez politykę;
7. restore produkcyjny wymaga zatwierdzonego ownera i RPO/RTO; po restore
   capability pozostaje false do pełnego verify i świeżych evidence;
8. legal hold, audit sink i eventy nie są usuwane ani zwalniane przez rollback.

Destrukcyjne drop/downgrade można rozważyć wyłącznie w osobnym decommission ADR,
nie w rollbacku migracji.

## Exact runtime test matrix

Poniższe przypadki są wymaganiami przyszłej implementacji, nie wynikami tego
dokumentu. Każdy test używa syntetycznych danych na izolowanym site; żaden nie
jest operacją produkcyjną.

| ID | Etap | Fixture / akcja | PASS |
|---:|---|---|---|
| M01 | Preflight E | Brak metadata i tabeli | Kod `FRESH`, zero domenowych write |
| M02 | Preflight E | Exact app-owned E istnieje | Kod `REPLAY`, zero zmian |
| M03 | Preflight E | Custom DocType o tej nazwie | `SCHEMA_COLLISION`, migrate stop przed sync |
| M04 | Preflight E | Orphan physical table | `SCHEMA_COLLISION`, brak adopcji/DDL |
| M05 | Preflight E | `issingle=1` near-miss | Fail code-only, brak zmiany metadata |
| M06 | Preflight E | Wrong module/app owner | Fail bez echa obcej wartości |
| M07 | Preflight E | Nieplanowany secondary index | Fail przed seed |
| M08 | Preflight E | Patch Log success bez exact state | Recovery blocker, bez force/delete logu |
| M09 | Expand | Fresh migrate | Exact DocType i jeden initial row |
| M10 | Expand | Drugi migrate tego samego SHA | Zero domenowych insert/update; identyczny checksum |
| M11 | Expand | Fresh install app | Ten sam target E co upgrade |
| M12 | Expand | Upgrade z poprzedniego release | Ten sam target E co fresh install |
| M13 | Seed | Zero row | Tworzy tylko `repair-audit-readiness-v1` |
| M14 | Seed | Exact initial row | Zero update i bez zmiany `modified` domenowego snapshotu |
| M15 | Seed | Row o innej nazwie | Fail; brak drugiego row i brak delete |
| M16 | Seed | Exact row plus drugi row | Fail; oba pozostają do jawnego recovery |
| M17 | Seed | Revision inna niż 0 | Fail; brak resetu revision |
| M18 | Seed | Unknown code lub niekanoniczny JSON | Fail bez echa payloadu |
| M19 | Seed | Wymuszony wyjątek przed insert | Rollback patcha; zero row |
| M20 | Seed | Wymuszony wyjątek po insert przed Patch Log | Retry daje exact jeden row albo jawny fail, nigdy duplikat |
| M21 | Schema E | Exact fields/types/options phase E | Metadata i physical schema zgodne z allowlistą |
| M22 | Schema E | Permissions/web/search/import audit | Zero public/Desk surface |
| M23 | Guards | Insert/update/delete/rename/import | Wszystkie odrzucone, także `ignore_permissions=True` |
| M24 | Privacy | Initial row, error i report scan | Brak PII, tokenu, URL, path, SQL i exception text |
| M25 | Request | Tabela istnieje przy kill switch false | `features: []`, brak SELECT stanu zgodnie z kolejnością ADR |
| M26 | Request | Brak row/uszkodzony row | Fail-closed, bez auto-create i update |
| M27 | Constrain preflight | Exact E row | Dopuszcza sync C, zero write |
| M28 | Constrain preflight | Null w required target | Stop przed sync C |
| M29 | Constrain preflight | Check poza DB `0|1` | Stop code-only |
| M30 | Constrain preflight | Zły UTC/digest/contract revision | Stop bez echa wartości |
| M31 | Constrain preflight | Unknown Select/code | Stop przed zmianą metadata |
| M32 | Constrain preflight | Połowiczny lease | Stop, brak samonaprawy |
| M33 | Constrain | Udany C migrate | Target metadata, row bez zmian |
| M34 | Constrain | Replay C migrate | Zero domenowych write, identyczny checksum |
| M35 | Failure recovery | Stop po `sync_all` E przed seed | Retry tworzy exact row; feature cały czas false |
| M36 | Failure recovery | Stop po seed przed verify | Retry waliduje istniejący row bez resetu |
| M37 | Failure recovery | Stop po `sync_all` C przed verify | Forward verify/retry, brak downgrade |
| M38 | Concurrency | Dwa równoległe seed attempts | Dokładnie jeden insert lub exact replay; zero dodatkowych rows |
| M39 | Backup | Backup przed E/C z checksumą | Artefakt odtwarzalny według zatwierdzonej procedury |
| M40 | Restore | Restore pre-E, następnie E/C | Exact target i `features: []` |
| M41 | Restore | Restore starego snapshotu/digestu | Readiness false do świeżego verify/collectora |
| M42 | Rollback | Powrót kodu przy obecnej tabeli | Poprzedni kod działa, schema zachowany, feature false |
| M43 | Rollback | Próba drop/downgrade w zwykłym rollbacku | Test/policy blokuje operację |
| M44 | Scheduler separation | E/V/C bez hooka | Brak joba collectora po `sync_jobs` |
| M45 | Scheduler S | Exact importowalny hook | Jeden job `Cron`, exact method i `* * * * *` |
| M46 | Scheduler S | Nieimportowalna metoda | Verify FAIL mimo ostrzeżenia bez wyjątku z `sync_jobs` |
| M47 | Scheduler S | Powtórny sync | Zero duplikatów; exact ten sam job |
| M48 | Scheduler rollback | Hook usunięty w osobnym release | Tylko job collectora nie istnieje; schema/row zachowane |
| M49 | Scheduler runtime | Job istnieje, ale scheduler/queue nie działa | Readiness false; obecność rekordu nie daje PASS |
| M50 | Regression | Pełny `kuck_serwis` i konsument `kuck_shop` | Zielone przy capability wyłączonym po E/V/C/S |

Minimalny dowód każdego testu: ID, candidate SHA, exact środowisko i wersje,
exit code, code-only wynik, checksum oczekiwanego schema/row projection oraz
referencja do sanitizowanego artefaktu. Brak uruchomienia ma status `NOT_RUN`,
nie PASS.

## Bramki promocji i NO-GO

| Przejście | Wymagany dowód | NO-GO |
|---|---|---|
| plan → E | zaakceptowany ADR/schema/patches, backup+restore, M01–M08 | dowolne `NIEUSTALONE` wymagane dla migration/rollback, collision |
| E → V | E migrate i replay, M09–M26 | częściowy/nieznany row, public surface, feature inne niż false |
| V → C | exact verify na fresh+upgrade+restore | null/unknown, brak wspieranego DB testu, brak rollback ownera |
| C → S | C replay i M27–M43 | schema mismatch, brak collector/hook approval |
| S → dark | M44–M50, exact job i code-only monitoring | brak joba/duplikat, brak alert ownera, niezamknięte approval |
| dark → aktywacja | osobny release/approval ADR 0002 i pełna macierz ADR/runbooka | ten dokument nigdy nie wystarcza do aktywacji |

Wszystkie etapy kończą się build kill switchem false i rollout false. Scheduler
S może działać wyłącznie dark; aktywacja jest osobnym procesem po zamknięciu
wszystkich decyzji.

## Handoff przyszłej implementacji

Każdy etap przekazuje integratorowi:

- listę dokładnie zmienionych plików i candidate SHA;
- diff DocType/controller/store/patches bez cudzych zmian;
- raport collision preflight bez danych i nazw spoza allowlisty;
- wynik migrate, replay i macierzy M01–M50;
- checksum schema i kanonicznej projekcji singleton row, bez surowych wartości;
- dowód backup/restore i ownera rollbacku;
- listę nadal otwartych `NIEUSTALONE`;
- jawne `NOT_RUN` dla każdego niewykonanego testu;
- potwierdzenie `features: []`, braku schedulera przed S i braku aktywacji po S.

## Walidacja tego dokumentu

Uruchamiana z repozytorium `apps/kuck_serwis`:

```bash
test -s docs/design/repair-audit-readiness-schema-migration-plan.md
test -e docs/adr/0002-repair-audit-readiness-state.md
test -e docs/runbooks/repair-audit-readiness.md
test -e docs/runbooks/repair-audit-operations.md
test -e kuck_serwis/audit_readiness.py
test -e kuck_serwis/audit_health.py
test -e kuck_serwis/public_contract/v1.py
test -e kuck_serwis/hooks.py
test -e kuck_serwis/patches.txt
test -e ../frappe/frappe/migrate.py
test -e ../frappe/frappe/modules/patch_handler.py
test -e ../frappe/frappe/core/doctype/scheduled_job_type/scheduled_job_type.py
rg -n '^## ' docs/design/repair-audit-readiness-schema-migration-plan.md
rg -ni '(password|passwd|secret|token|api[_ -]?key|encryption_key)\s*[:=]' docs/design/repair-audit-readiness-schema-migration-plan.md
git diff --check -- docs/design/repair-audit-readiness-schema-migration-plan.md
git diff --no-index /dev/null docs/design/repair-audit-readiness-schema-migration-plan.md
```

Skan sekretów ma zwrócić brak dopasowań. Ostatnia komenda zwraca kod `1`, bo
pokazuje oczekiwany diff nowego pliku; inny kod oznacza błąd narzędzia.
