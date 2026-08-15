# ADR 0003 — publiczny kontrakt metadanych i odczytu zdjęć napraw

Status: **PROPOSED**; obie capability pozostają wyłączone, FILE-01 pozostaje
`GAP/BLOCKED`

Data: 2026-08-15

Audytowana baza: `be075dee7606fedd5eead8935f2bfde494861dbf`

## Cel i charakter dokumentu

Ten ADR opisuje przyszłą, wersjonowaną granicę metadanych oraz kontrolowanego
odczytu zdjęć napraw. Nie udostępnia endpointu, nie włącza capability, nie
rozstrzyga polityki widoczności, sposobu prezentacji, retencji ani polityki AV.
Nie uznaje istniejących zdjęć za bezpieczne lub przeznaczone dla klienta.

Dokument jest decyzjo-neutralnym kontraktem bramek: wskazuje, jakie decyzje i
dowody muszą istnieć, zanim osobny rollout będzie mógł zwrócić metadane albo
treść. Brak dowolnej bramki oznacza odmowę, a nie fallback do DocType, Desk,
`File`, `/files` lub `/private/files`.

## Źródła i stan obecny

- [ADR 0001](0001-public-contract-v1.md) jawnie wyklucza zdjęcia z
  `kuck-serwis/v1`; child rows i ścieżki `File` nie należą do read modelu.
- [ADR 0002](0002-repair-audit-readiness-state.md) pozostaje `proposed`, a
  trwała readiness audytu nie jest aktywna.
- [metadata-only contract](../../kuck_serwis/repair_photo_metadata.py) zwraca
  wyłącznie pozycję i techniczne potwierdzenia prywatności/powiązania. Nie zwraca
  URL, `File.name`, MIME ani prawa do pobrania.
- [actor-scoped evidence store](../../kuck_serwis/repair_photo_evidence_store.py)
  rewaliduje Website User, `Customer.portal_users`, naprawę, child row i
  prywatny `File` w ograniczonym snapshotcie. Wydawane capability są prywatne i
  nie są kontraktem przeglądarkowym.
- [local storage binding](../../kuck_serwis/repair_photo_storage.py) czyta do
  10 MiB z lokalnego `private/files`, sprawdza ścieżkę/inode i po odczycie
  ponownie rewaliduje binding. Nie obsługuje custom/remote storage i nie
  autoryzuje downloadu.
- [decode process](../../kuck_serwis/repair_photo_decode_process.py) wiąże hash
  odczytanych bajtów z pełnym dekodowaniem JPEG/PNG/WebP w świeżym interpreterze.
  Proces nie jest sandboxem ani skanerem AV.
- [content evidence](../../kuck_serwis/repair_photo_content.py) ma jawne
  `malware_status=NOT_SCANNED`, `polyglot_status=NOT_PROVEN` i
  `downloadable=False`.
- Audyt G0-83 nie znalazł zatwierdzonego silnika AV, transportu, wersji,
  źródła/freshness sygnatur ani health gate. Nie wolno syntetyzować `CLEAN`.
- Audyt G0-85 dopuścił wyłącznie pure dry-run eligibility retencji. Purge nadal
  blokują: brak niezmiennego wieku lifecycle, rejestru legal hold i SoD,
  polityki shared blob/storage, backup/re-purge oraz zgód Legal/Operations.

Audyt przypiętego Frappe `v16.20.0`, commit
`5c16f12192815204a7eda2b2ab365a557a6e7def`, wykazał, że stock route:

1. przejmuje każdy path zaczynający się `/private/files/` przed ograniczeniem
   metod strony;
2. wyszukuje wszystkie `File` o tym samym `file_url` i dopuszcza rekord, jeśli
   dowolny przejdzie ogólne permission `File` (owner/share/read dokumentu);
3. nie stosuje exact actor scope `Customer.portal_users` z tego kontraktu;
4. w wariancie X-Accel ustawia cache na godzinę i stale-while-revalidate na dobę,
   dopuszcza Range i zgaduje MIME z nazwy;
5. w wariancie Werkzeug używa conditional response, automatycznych
   ETag/Last-Modified i Range;
6. nie zapewnia wymaganej polityki `nosniff`, nierozróżnialnej odmowy ani
   zatwierdzonego audytu domenowego zdjęcia naprawy.

Stock `/private/files` oraz `File.unique_url?fid=...` nie są kandydatami do
publicznego odczytu zdjęć napraw.

## Własność i granice aplikacji

- `kuck_serwis` pozostaje właścicielem identyfikacji naprawy, actor scope,
  widoczności zdjęcia, bindingu `File`, odczytu bajtów, inspekcji treści,
  capability i domenowego audytu.
- `kuck_shop` może być właścicielem przyszłej trasy HTTP i prezentacji wyłącznie
  po zatwierdzeniu decyzji ROUTE-01. Nie może odtwarzać SQL, odczytywać DocType,
  otrzymywać `Naprawa.name`, `File.name`, `file_url`, ścieżki ani capability
  storage.
- Przeglądarka nigdy nie wywołuje ogólnego Resource API ani whitelisted metody
  `File`. Publiczny identyfikator naprawy i pozycja nie są autoryzacją.
- Ten ADR nie definiuje token-read, uploadu, mutacji, miniatur, CDN, remote
  storage ani migracji legacy public files.

## Wersjonowanie i negocjacja capability

Przyszły kontrakt ma niezależną tożsamość:

```text
contract = "kuck-serwis-repair-photo/v1"
schema_revision = 1
features = []
```

Zamknięta allowlista przyszłych feature IDs:

```text
account-photo-metadata-read
account-photo-content-read
```

Rozdzielenie jest wymagane: prawo do bounded listy pozycji nie implikuje prawa
do treści. `account-photo-content-read` wymaga aktywnego
`account-photo-metadata-read`, ale zależność nie działa w drugą stronę. Brak
konfiguracji, `null`, niepoprawny bool, wyjątek, nieznana rewizja albo stary
snapshot readiness daje `features=[]`.

Żadna capability nie może pojawić się tylko dlatego, że kod dark adaptera jest
zainstalowany. Każda wymaga osobnej literalnej flagi per site, zatwierdzonej
rewizji polityk i pełnego snapshotu readiness. Obie wymagają aktywnego
`account-read` z ADR 0001 oraz gotowego trwałego audytu z ADR 0002.

`account-photo-metadata-read` dodatkowo wymaga:

- zatwierdzonej polityki widoczności VIS-01;
- kompletnego inventory i braku public/malformed/duplicate/orphan dla rekordów
  dopuszczanych przez tę politykę;
- versioned public operation i testów IDOR bez URL/File identity.

`account-photo-content-read` dodatkowo wymaga:

- wszystkich bramek metadata;
- dowodu stock-route deny COLLISION-01;
- zatwierdzonego content/AV/polyglot gate AV-01;
- zatwierdzonej polityki retencji i legal hold RET-01;
- zatwierdzonej semantyki odpowiedzi DISP-01 i trasy ROUTE-01;
- zielonej macierzy FILE-01 na izolowanym staging.

## Publiczny model metadanych

Jeśli i tylko jeśli capability metadata jest aktywna, wersjonowana operacja może
zwrócić model:

```json
{
  "schema": "repair-photo-metadata/v1",
  "repair_id": "rpr_...",
  "items": [
    {"position": 1, "state": "metadata_only"}
  ]
}
```

`items` jest tuple/listą maksymalnie 20 unikalnych pozycji dodatnich,
posortowanych rosnąco. Dokładny zbiór pozycji zależy od zatwierdzonej VIS-01;
do tego czasu wynik nie może być publicznie zbudowany. `state` ma dokładnie
`metadata_only` i nie obiecuje, że treść jest dostępna, bezpieczna lub
downloadable.

Model nie zawiera tytułu/opisu zdjęcia, autora, czasu, MIME, rozmiaru, wymiarów,
hasha, statusu skanera, oryginalnej nazwy, URL, ścieżki, `File.name`,
`Naprawa.name`, Customer/User ani danych warsztatu. Brak treści nie jest
automatycznie błędem listy metadanych.

## Kontrolowany pipeline treści

Po zatwierdzeniu wszystkich decyzji pojedyncze żądanie treści musi przejść
poniższą kolejność. Kroku nie wolno pominąć ani zastąpić cache'em:

1. **Request ownership** — exact trasa, HTTPS, dozwolona metoda, bounded path i
   brak query-derived identity. `Host` nie jest źródłem site origin.
2. **Actor** — bieżąca sesja daje exact aktywnego `Website User`; Guest, System
   User i wyłączony User są odrzucani.
3. **Repair scope** — jedno actor-scoped zapytanie wiąże `public_id`, wewnętrzną
   naprawę, `Customer.portal_users` i zatwierdzoną pozycję widoczną według
   VIS-01. Obcy, cofnięty i brakujący zasób są nierozróżnialne.
4. **Sealed File capability** — G0-74 wydaje wyłącznie wewnętrzne capability dla
   dokładnie jednego prywatnego, niefolderowego i poprawnie przypiętego `File`.
   Duplicate URL/idx/File, orphan, public lub malformed kończą request.
5. **Bounded read** — G0-80 odczytuje lokalny regular file bez symlink/hardlink,
   maksymalnie 10 MiB, wiąże SHA-256 i rewaliduje actor/File po odczycie.
   Custom/remote storage pozostaje unsupported do osobnego kontraktu.
6. **Structural decode** — G0-82 w świeżym procesie potwierdza exact hash,
   kontener, pełny decode, typ, wymiary, frames i limity. Nie nadaje statusu AV.
7. **AV/polyglot gate** — zatwierdzony adapter sprawdza te same exact bytes/hash
   konkretnym silnikiem oraz rewizją polityki. Timeout, stale signatures,
   unavailable, unknown lub niekanoniczne evidence są odmową. Kryteria `CLEAN`
   pozostają otwarte w AV-01.
8. **Final revalidation** — po decode i AV, bezpośrednio przed autoryzacją
   odpowiedzi, ponownie sprawdzane są actor, Portal User, naprawa, widoczność,
   child, File identity/revision/attachment i hash. Drift odrzuca body.
9. **Durable audit ACK** — append-only sink utrwala code-only zdarzenie
   `PHOTO_RESPONSE_AUTHORIZED` albo odmowę. Brak ACK zamyka odpowiedź. Zdarzenie
   nie twierdzi, że klient odebrał wszystkie bajty, czego WSGI nie dowodzi.
10. **Response** — adapter buduje odpowiedź wyłącznie z już odczytanych,
    zweryfikowanych bajtów w pamięci. Nie przekazuje ścieżki do Werkzeug,
    X-Accel, reverse proxy ani `File.get_content`; nie emituje stock URL.

Współbieżne cofnięcie dostępu po ostatniej rewalidacji jest granicą każdego
request-response. Nie wolno trzymać transakcji ani blokady DB podczas transmisji.
Krótki bounded body i finalna rewalidacja minimalizują okno, ale go nie usuwają;
test rollback musi traktować kill switch jako podstawową reakcję.

## Kolizja ze stock `/private/files`

Przed aktywacją content capability musi istnieć udowodniona metoda, która dla
każdego `File` objętego kontraktem blokuje portalowym aktorom:

- bezpośredni `/private/files/<name>`;
- wariant z `fid`/`File.unique_url`;
- ogólny API/RPC download;
- dostęp przez duplicate `File`, owner, share albo ogólne read DocType;
- cache/proxy/CDN replay po cofnięciu actor scope.

Nie wolno zmieniać rdzenia Frappe. Ten ADR nie wybiera pomiędzy dedykowanym
storage poza stock route, request-layer deny, kontrolowaną konfiguracją reverse
proxy ani migracją File. Wybrany wariant musi mieć osobny threat model,
rollback i testy, nie może opierać się wyłącznie na braku linku w HTML.

Capability content pozostaje false, jeśli stock route jest osiągalna choćby dla
jednego portalowego owner/share/duplicate edge case. Dostęp pracownika Desk i
jego relacja do deny są osobną decyzją COLLISION-02.

## Kontrakt HTTP i cache

Poniższa macierz definiuje bezpieczne minima, nie wybierając URL ani
inline/attachment. Wszystkie odpowiedzi, w tym błędy, mają
`Cache-Control: private, no-store, max-age=0`, `Pragma: no-cache`,
`X-Content-Type-Options: nosniff` oraz brak danych osoby/zasobu w body i
nagłówkach.

| Warunek | Status | Body i nagłówki |
|---|---:|---|
| Guest/brak sesji | 401 | code-only `AUTH_REQUIRED`; zero zdjęcia. |
| Obcy, nieistniejący, cofnięty, niewidoczny lub zły format identity | 404 | jeden publiczny `NOT_FOUND`; brak rozróżnienia. |
| Capability/readiness/AV/audit/storage niedostępne | 503 | code-only `DEPENDENCY_UNAVAILABLE`; bez fallbacku. |
| Malformed/duplicate/public/orphan/drift/content rejected | 404 | `NOT_FOUND` na publicznej granicy; zaufany kod wyłącznie w audycie. |
| Query string lub niedozwolony header sterujący identity | 400 | code-only `INVALID_REQUEST`; żadnego lookupu z query. |
| Metoda spoza zatwierdzonego GET/HEAD | 405 | `Allow: GET, HEAD`; zero pipeline body. |
| Range lub If-Range po pozytywnej autoryzacji zasobu | 416 | `Accept-Ranges: none`; zero partial body. |
| If-None-Match/If-Modified-Since | 200 po pełnym pipeline | Serwer ignoruje validators; nie emituje 304. |
| GET po pełnym sukcesie | 200 | exact MIME z zatwierdzonego evidence, bounded Content-Length i pełny body. |
| HEAD po pełnym sukcesie | 200 | te same status/headers co GET, pusty body; pipeline i audyt nadal wykonane. |

Success nie emituje `ETag`, `Last-Modified`, `Expires`, `Accept-Ranges: bytes`,
oryginalnej nazwy ani content hash. Nie powstaje 304 bez pełnej autoryzacji.
Odpowiedź dodaje `Cross-Origin-Resource-Policy: same-origin` i
`Referrer-Policy: no-referrer`. DISP-01 wybierze exact
`Content-Disposition: inline` albo `attachment` oraz bezpieczną techniczną nazwę;
do tego czasu nie ma success response.

Statusy 401/404/503 nie mogą różnić się przez traceback, File existence,
Content-Length zdjęcia, nazwę pliku, MIME, timing cache hit ani dodatkowy lookup
globalny. Rate limiting nie zapisuje public ID, IP w metryce wysokiej
kardynalności ani danych aktora w browser analytics.

## Audyt, prywatność i obserwowalność

Przed body wymagany jest trwały ACK istniejącej, zatwierdzonej granicy audytu.
Minimalne allowlisted pola zdarzenia to: contract/schema revision, operacja,
wynik, code-only reason, actor class, pseudonimizowany actor key, jednostronny
skrót public repair ID, pozycja, byte-count bucket, MIME enum, policy/release
revision i correlation ID. Szczegółowy zestaw wymaga AUDIT-01.

Zakazane są: e-mail, Customer/User/File/Naprawa names, URL/path, filename, body,
hash contentu, wymiary, opis zdjęcia, exception, cookie, header auth i query.
Metryki mają niską kardynalność: operation/result/reason/MIME/bucket, bez actor lub
resource labels.

Retencja audytu i zdjęć to odrębne polityki. Dry-run eligibility G0-85 nie daje
purge capability, nie usuwa legal hold i nie jest warunkiem automatycznie true.

## Rollout

1. **ADR approval** — zamknąć decyzje poniżej i przypiąć zatwierdzoną rewizję.
2. **Dark contracts** — wdrożyć typed public models, pipeline ports i deny-only
   adapters; obie flags false, brak tras i hooków.
3. **Containment** — wdrożyć stock-route deny/storage strategy, inventory,
   migrację legacy i test wszystkich alternatywnych ścieżek. Capability false.
4. **Content gates** — wdrożyć AV/polyglot, retention/hold readiness i trwały
   audit; failure injection i stale evidence. Capability false.
5. **Metadata staging** — osobno włączyć metadata dla syntetycznych kont, bez
   linku do treści; porównać IDOR, audit i code-only telemetry.
6. **Content staging** — po FILE-01 matrix uruchomić exact route na izolowanym
   staging; test browser/proxy/cache i kill switch.
7. **Staff canary** — jawna allowlista syntetycznych/wewnętrznych kont, bounded
   traffic, zero produkcyjnych zdjęć w artefaktach.
8. **Stopniowy rollout** — metadata i content mają osobne flagi/progi. Content
   nigdy nie może być szerszy niż metadata.

Każdy etap wymaga immutable release/policy evidence. Sam zielony unit test,
obecność pliku lub `is_private=1` nie podnosi readiness.

## Rollback

Pierwszy krok rollbacku to atomowe wyłączenie `account-photo-content-read`, a
następnie w razie potrzeby `account-photo-metadata-read`. Konsument zwraca 503
bez fallbacku i usuwa linki/obrazy z kolejnego SSR. Już otwarty request nadal
podlega finalnej rewalidacji i audit ACK.

Rollback nie przywraca stock `/private/files`, nie zmienia plików na publiczne,
nie usuwa audytu, legal hold ani evidence i nie uruchamia purge. Cofnięcie kodu
nie cofa schema/migracji storage; wymagany jest osobny expand/verify/constrain
oraz restore plan. Cache/CDN, jeśli kiedykolwiek zatwierdzone, musi mieć purge
test przed rolloutem; obecny kontrakt wymaga no-store.

## Wymagane testy akceptacyjne

Przed metadata capability:

1. brak/false/malformed flag daje pustą listę features;
2. metadata feature nie implikuje content feature;
3. content feature bez metadata jest stanem invalid i kończy się pustą listą;
4. niegotowy `account-read` lub audit readiness wyłącza oba features;
5. Guest, System User i disabled Website User dostają AUTH_REQUIRED;
6. A widzi wyłącznie zatwierdzone pozycje własnych Customer;
7. A/B, missing, revoked i malformed public ID są nierozróżnialne;
8. zmiana Portal User przed odczytem natychmiast odbiera dostęp;
9. max 20, limit+1, duplicate idx/URL/File i orphan fail-closed;
10. DTO nie ma URL, File ID, MIME, size, hash, filename ani PII;
11. unknown visibility state wyłącza capability;
12. audit failure blokuje odpowiedź metadata bez częściowego payloadu.

Przed content capability:

13. direct `/files`, `/private/files`, `fid` i RPC są deny dla Guest/A/B;
14. portalowy File owner/share nie omija deny;
15. duplicate File o tym samym URL nie tworzy alternatywnego dostępu;
16. stock route/cache nie zwraca body po cofnięciu dostępu;
17. dokładnie jeden sealed File odpowiada zatwierdzonej pozycji;
18. public/malformed/orphan/wrong attachment są NOT_FOUND;
19. symlink/hardlink/FIFO/device/directory/outside-root są odrzucane;
20. empty, 10 MiB+1 i mutation inode/size/mtime podczas read są odrzucane;
21. JPEG/PNG/WebP magic, container, full decode, dimension/frame limits są
    sprawdzane na exact hash;
22. expected MIME nie pochodzi z requestu i mismatch kończy request;
23. AV positive, timeout, unavailable, unknown engine/revision i stale
    signatures nigdy nie dają success;
24. polyglot/unknown policy evidence nie daje success;
25. actor/Portal User/child/File drift podczas decode/AV jest wykryty finalnie;
26. hash/byte count między read, decode, AV i response są exact;
27. brak durable audit ACK oznacza zero body;
28. audit event nie twierdzi, że transmisja została ukończona;
29. GET/HEAD mają równoważny status/headers, HEAD ma zero body;
30. POST/PUT/DELETE/PATCH/OPTIONS nie uruchamiają pipeline body;
31. Range/If-Range nie zwracają partial body;
32. conditional headers nie tworzą 304 ani pominięcia reautoryzacji;
33. success/error mają no-store, no-cache, nosniff, CORP i referrer policy;
34. brak ETag, Last-Modified, content hash i oryginalnej nazwy;
35. inline/attachment jest exact zgodne z zatwierdzonym DISP-01;
36. browser/proxy nie odzyskuje body po logout/revocation/kill switch;
37. public logs/metrics/errors nie zawierają markerów PII/path/File/hash/body;
38. failure po każdym kroku pipeline jest code-only i fail-closed;
39. BaseException/process kill nie jest maskowany jako success;
40. pełne Desk/workflow/public-contract v1 pozostają bez regresji przy flags off.

FILE-01 może przejść z `GAP/BLOCKED` dopiero po realnej macierzy na izolowanym
staging, nie po samym zatwierdzeniu tego ADR.

## Otwarte decyzje

| ID | Właściciel | Wymagana decyzja | Brak decyzji |
|---|---|---|---|
| VIS-01 | Kuck + Service + Privacy | Które zdjęcia i od którego momentu są widoczne klientowi; czy potrzebne pole approval/author/source. | Obie capability false. |
| VIS-02 | Kuck + UX | Czy metadata bez dostępnej treści ma być pokazywana i z jakim neutralnym stanem. | Metadata false. |
| ROUTE-01 | Kuck + `kuck_shop` + Security | Exact URL/owner i czy content jest osobnym zasobem czy częścią portalu. | Brak route/hooka. |
| DISP-01 | Kuck + UX + Security | `inline` albo `attachment`, bezpieczna nazwa techniczna i zachowanie mobile. | Brak success response. |
| COLLISION-01 | Platform + Security | Dedykowany storage, request deny, reverse proxy lub inny mechanizm eliminujący stock route bez core fork. | Content false. |
| COLLISION-02 | Service + Security | Czy i jak staff Desk zachowuje dostęp po stock-route containment. | Brak containment rollout. |
| AV-01 | Security + Operations + Legal | Silnik/version, source/max-age sygnatur, health, timeout, retry, `CLEAN` i polyglot policy. | Content false; NOT_SCANNED. |
| MIME-01 | Security | Allowlista typów i trusted source expected MIME; obsługa legacy mismatch. | Content false. |
| RET-01 | Legal + Operations + Kuck | Okresy per lifecycle, `eligible_at`, legal hold/SoD, backup/re-purge i shared blob. | Purge i content false. |
| AUDIT-01 | Security + Operations + Privacy | Event fields, sink ACK, retencja, progi, owner alertu i outage policy. | Obie capability false. |
| RATE-01 | Security + Operations | Limity per actor/IP, progi abuse i code-only telemetry. | Public rollout false. |
| STORAGE-01 | Platform + Service | Local-only pozostaje wymaganiem czy potrzebny custom/remote storage contract. | Unsupported storage fail-closed. |
| LEGACY-01 | Service + Legal | Migracja/quarantine public legacy photos oraz dowód kompletności. | Dotknięte rekordy niewidoczne. |
| RELEASE-01 | Release owner + Security | Exact schema/policy/release evidence i staging sign-off podnoszące flags. | Features empty. |

## Literalny formularz akceptacji

Akceptacja jest ważna wyłącznie jako kompletny, datowany wpis powiązany z exact
rewizją tego ADR. Puste pole, `TBD`, zgoda częściowa albo odpowiedź opisowa nie
włącza capability.

```text
REPAIR_PHOTO_CONTRACT_APPROVAL=APPROVED|REJECTED
ADR_REVISION_SHA=<exact commit SHA>
APPROVED_AT_UTC=<YYYY-MM-DDTHH:MM:SSZ>

VIS_01=<approved policy revision or REJECTED>
VIS_02=<approved policy revision or REJECTED>
ROUTE_01=<approved exact route/owner or REJECTED>
DISP_01=INLINE|ATTACHMENT|REJECTED
COLLISION_01=<approved containment revision or REJECTED>
COLLISION_02=<approved staff policy revision or REJECTED>
AV_01=<approved engine/signature/polyglot policy revision or REJECTED>
MIME_01=<approved MIME policy revision or REJECTED>
RET_01=<approved retention/legal-hold policy revision or REJECTED>
AUDIT_01=<approved audit/readiness policy revision or REJECTED>
RATE_01=<approved rate-limit policy revision or REJECTED>
STORAGE_01=<approved storage policy revision or REJECTED>
LEGACY_01=<approved migration/quarantine revision or REJECTED>
RELEASE_01=<approved release/staging evidence revision or REJECTED>

KUCK_OWNER=<role, no personal data in Git>
SERVICE_OWNER=<role>
SECURITY_OWNER=<role>
PRIVACY_LEGAL_OWNER=<role>
OPERATIONS_OWNER=<role>
RELEASE_OWNER=<role>

I explicitly approve only the revisions listed above. I understand that this
approval does not activate either capability, does not make FILE-01 pass, and
does not authorize production data migration, purge, upload or rollout.
```

Po kompletnej akceptacji nadal potrzebne są osobne: implementacja, review,
migracja jeśli wymagana, staging evidence i jawny rollout approval. Ten ADR nie
jest takim approval.

## Konsekwencje

- Metadane i treść nie rozszerzą przypadkiem `kuck-serwis/v1` ani
  `account-read`.
- Default-off i osobne flags pozwalają cofnąć content bez ukrywania całego
  portalu napraw.
- Stock route containment oraz AV/retencja/audyt są twardymi preconditions, nie
  zadaniami „po uruchomieniu”.
- In-memory body do 10 MiB upraszcza finalną kontrolę i eliminuje path handoff,
  ale wymaga capacity testu i nie jest wyborem dla remote storage.
- Brak polityki widoczności oznacza brak publicznych metadanych, nawet jeśli
  `File` jest private i strukturalnie poprawny.
- FILE-01 pozostaje `GAP/BLOCKED` do pełnego realnego evidence oraz approvals.
