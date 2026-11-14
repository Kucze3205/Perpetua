# Interfejs GitHub Actions: co da się pokazać właścicielowi

Research do [#332](https://github.com/Kucze3205/Perpetua/issues/332), mapa
[#331](https://github.com/Kucze3205/Perpetua/issues/331). Pytanie: jakie możliwości i limity interfejsu GitHub mają
znaczenie dla **Session timeline**, **Session summary** i **Loop status**, oraz skąd biorą się runy Skipped.

Źródła: GitHub Docs, blog i changelog GitHub, repozytoria `actions/toolkit` i `actions/runner`, schemat GraphQL API
GitHub (introspekcja przez `gh api graphql`), własne workflowy w `engine/.github/workflows/` i lista runów tego repo.
Języki: źródła pierwotne są tylko po angielsku; nie ma wersji oryginalnej w innym języku. Gdzie fakt nie jest
udokumentowany, piszę to wprost i podaję, jak go sprawdzić.

Data: 2026-11-14.

## 1. Summary runu (`GITHUB_STEP_SUMMARY`) — miejsce na Session summary

Fakty z dokumentacji ([Workflow commands → Adding a job summary](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-commands#adding-a-job-summary)):

- Każdy krok pisze Markdown do pliku `$GITHUB_STEP_SUMMARY`. „Job summaries support GitHub flavored Markdown”.
- Limit: **1 MiB na krok**. Po przekroczeniu upload się nie udaje i powstaje adnotacja z błędem; status kroku i jobu
  się nie zmienia („Upload failures for job summaries do not affect the overall status of a step or a job”).
- Wyświetla się **najwyżej 20 summary z kroków na job**.
- **Nie na żywo.** Summary kroku jest wysyłane po zakończeniu kroku, kolejne kroki nie mogą go zmienić; summary
  wszystkich kroków są łączone, gdy job się kończy, i pokazywane na stronie runu. Wniosek: Session summary i timeline
  „po sesji” pasują do Summary; widok na żywo musi iść w log jobu.
- Summary samo maskuje sekrety, które przypadkiem do niego trafią.
- Nie ma limitu na cały job podanego w dokumentacji (tylko per krok).

Co renderuje się w Summary:

- GFM: nagłówki, tabele, listy, bloki kodu, linki, obrazki. Pomocnik `core.summary` z
  [`actions/toolkit`](https://github.com/actions/toolkit/blob/main/packages/core/src/summary.ts) generuje HTML:
  `<details><summary>` (`addDetails`, czyli **zwijane sekcje**), `<table>` z `colspan`/`rowspan` (`addTable`),
  `<pre><code>`, `<h1>`–`<h6>`, `<img>`, `<a>`, `<blockquote>`, `<hr>`, `<ul>/<ol>`. To pokazuje, że te tagi HTML
  przechodzą w Summary (pomocnik nie miałby sensu, gdyby były wycinane).
- **Mermaid**: dokumentacja diagramów ([Creating diagrams](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/creating-diagrams))
  wymienia issues, dyskusje, PR-y, wiki i pliki Markdown, **nie wymienia** Summary runu. W praktyce działa: projekt
  [Kesin11/actions-timeline](https://github.com/Kesin11/actions-timeline) rysuje wykres Gantta Mermaid w Summary.
  Traktować jako działające, ale nieudokumentowane.
- Brak przewijanych bloków o stałej wysokości ani JavaScriptu (to sanityzowany GFM, jak w issue).

## 2. Log jobu na żywo — miejsce na Session timeline

- **Grupy**: `::group::tytuł` … `::endgroup::` tworzy zwijany fragment logu
  ([Grouping log lines](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-commands#grouping-log-lines)).
  **Grup nie da się zagnieżdżać.** Zgłoszenie [actions/runner#802](https://github.com/actions/runner/issues/802)
  zamknięto jako „nie po stronie runnera”; prośba o drzewo grup
  [actions/toolkit#1001](https://github.com/actions/toolkit/issues/1001) jest nadal otwarta. Wniosek: timeline ma
  jeden poziom — np. jedna grupa na turę agenta albo na wywołanie narzędzia, w środku zwykłe linie.
- Log jest strumieniowany na żywo w trakcie kroku (wpis inżynierski niżej); każdą linię można podlinkować (permalink do linii)
  ([Using workflow run logs](https://docs.github.com/en/actions/how-tos/monitor-workflows/use-workflow-run-logs)).
  Wyszukiwarka logu przeszukuje tylko rozwinięte kroki. Do oglądania logów, także w publicznym repo, trzeba być
  zalogowanym.
- **Długość linii i rozmiar logu**: GitHub **nie publikuje** limitu. Wpis inżynierski GitHub
  ([How GitHub Actions renders large-scale logs](https://github.blog/engineering/architecture-optimization/how-github-actions-renders-large-scale-logs/), 2021)
  mówi, że przeglądarka łamie długie linie, celem było płynne pokazanie ≥50 tys. linii, a bardzo duże logi są
  przycinane w widoku z linkiem do pobrania. Wniosek: skracać długie wyniki narzędzi w timeline (pełny strumień i tak
  jest w artefakcie `stream.ndjson`), limit sprawdzić prototypem.
- **Maskowanie sekretów** ([Secrets](https://docs.github.com/en/actions/concepts/security/secrets),
  [Secrets reference](https://docs.github.com/en/actions/reference/security/secrets)):
  - GitHub maskuje w logu wartości wszystkich sekretów użytych w tym jobie oraz rozpoznawalne tokeny (`ghp_`, `gho_`,
    `ghu_`, `ghs_`, `ghr_`, PAT-y v2, JWT).
  - Maskowanie „is not guaranteed”: sekret przekształcony (np. zakodowany base64, pocięty) nie zostanie zamaskowany;
    dokumentacja radzi nie trzymać danych strukturalnych (JSON) jako wartości sekretu.
  - Własne wartości maskuje się `::add-mask::wartość` — raz na wartość na job, **przed** wypisaniem. Wieloliniowe
    wartości: escape `%0A`; maskowana jest całość i każda linia osobno
    ([toolkit commands.md](https://github.com/actions/toolkit/blob/main/docs/commands.md)). Nie maskować krótkich
    wartości — log robi się nieczytelny.
- **Adnotacje** (`::notice::`, `::warning::`, `::error::`) pokazują się na górze runu: limit 10 każdego rodzaju na krok,
  50 na job ([toolkit problem-matchers.md](https://github.com/actions/toolkit/blob/main/docs/problem-matchers.md)).
  Nadają się na jedną-dwie najważniejsze wiadomości, nie na timeline.

## 3. Link do diffu jednego pliku

- Strona commita: `https://github.com/<owner>/<repo>/commit/<sha>#diff-<sha256(ścieżka)>`, gdzie kotwica to
  `diff-` + szesnastkowy SHA-256 ścieżki pliku względem korzenia repo. **Nieudokumentowane**, ale sprawdzone na tym
  repo 2026-11-14: commit `1c94780`, plik `CONTEXT.md` →
  `#diff-86fee53c33308768d49a4ac0ff5720fd7f5411a8c8bd62ab9b80bbffa2977a14`; kotwica jest w HTML strony commita.
- Porównanie `https://github.com/<owner>/<repo>/compare/<base>...<head>`
  ([Comparing commits](https://docs.github.com/en/pull-requests/committing-changes-to-your-project/viewing-and-comparing-commits/comparing-commits));
  te same kotwice są tam ładowane dynamicznie (nie było ich w surowym HTML), więc link do pliku w porównaniu trzeba
  sprawdzić w przeglądarce. Skoro zapisy jednej sesji mają być jednym commitem (mapa #331), wystarczy link do commita.
- Link do pliku (nie diffu): `https://github.com/<owner>/<repo>/blob/<sha>/<ścieżka>`.

## 4. Nazwy runów, jobów i kroków

Z [Workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax) i tabeli
dostępności kontekstów ([Contexts](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability)):

| Klucz | Gdzie widać | Konteksty w wyrażeniu | Domyślnie |
|---|---|---|---|
| `name` (workflow) | lista workflowów, lista runów | — | ścieżka pliku workflow |
| `run-name` | lista runów (zakładka Actions) | `github`, `inputs`, `vars` | treść zdarzenia (np. tytuł commita/PR) |
| `jobs.<id>.name` | strona runu, checki | `github`, `needs`, `strategy`, `matrix`, `vars`, `inputs` | id jobu |
| `steps[*].name` | log jobu | jak wyżej + `job`, `runner`, `env`, `secrets`, `steps` | dla `run:` — treść komendy; dla `uses:` — „Run <akcja>” (obserwacja, nie z docs) |

- Dla kroku `run:` bez `name` GitHub pokazuje treść komendy („the step name will default to the text specified in the
  `run` command”) — stąd `Run pip install -q pyyaml` z przykładu właściciela. Każdy krok potrzebuje jawnego `name`.
- **Nazwy runu nie da się zmienić po starcie**: REST API runów nie ma endpointu do zmiany `name`/`display_title`
  ([Workflow runs API](https://docs.github.com/en/rest/actions/workflow-runs)). Wszystko, co ma być w nazwie runu,
  musi być znane z `inputs`/zdarzenia w chwili startu.
- Nazwa kroku może używać `steps.*.outputs` poprzednich kroków (kontekst `steps` jest dozwolony) — da się np. pokazać
  rolę policzoną wcześniej. Kiedy UI odświeża taką nazwę, sprawdzić prototypem.

## 5. Loop status: przypięte issue nadpisywane przez `github.token`

- Przypiąć można **najwyżej 3 issue** na repo; pokazują się nad listą issue
  ([Pinning an issue](https://docs.github.com/en/issues/tracking-your-work-with-issues/administering-issues/pinning-an-issue-to-your-repository)).
  Pętla zajmie jedno z trzech miejsc właściciela.
- Przypinanie jest tylko w GraphQL: mutacje `pinIssue(issueId)` / `unpinIssue` (sprawdzone introspekcją schematu;
  REST tego nie ma). `gh issue pin` z niej korzysta. **Nieudokumentowane**, czy `GITHUB_TOKEN` z `issues: write` może
  przypiąć issue — sprawdzić w prototypie; w najgorszym razie właściciel przypina raz ręcznie, a silnik tylko nadpisuje
  treść.
- Nadpisanie treści (`PATCH /repos/{owner}/{repo}/issues/{n}` albo `gh issue edit --body-file`) wymaga
  `issues: write`, które workflowy silnika już mają.
- Zdarzenia wywołane przez `GITHUB_TOKEN` **nie tworzą nowych runów** (poza `workflow_dispatch` i
  `repository_dispatch`) ([Events that trigger workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)).
  Edycja Loop status przez silnik nie wygeneruje więc pustych runów.
- Limit treści issue: ok. **65 536 znaków** — GitHub go nie dokumentuje, ale API zwraca `body is too long (maximum is
  65536 characters)` (np. [mshick/add-pr-comment#93](https://github.com/mshick/add-pr-comment/issues/93); niejasność
  jednostki „znak” opisuje [github/rest-api-description#5399](https://github.com/github/rest-api-description/issues/5399)).
  Loop status musi być zwięzły (linki zamiast treści).
- Każde nadpisanie zostaje w historii edycji issue (widoczne „edited”) — nie przeszkadza, ale nie jest ukryte.

## 6. Skąd się biorą runy Skipped (diagnoza na tym repo)

Lista runów `Kucze3205/Perpetua` (ostatnie 300, stan 2026-11-14): `owner-request` — 6 runów, wszystkie `skipped`;
`tokenless` — 9 `skipped` obok 75 udanych. Innych Skipped nie ma.

Mechanizm (docs: [Control jobs with conditions](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-jobs-with-conditions)):
zdarzenie z `on:` **zawsze** tworzy run; `if:` na poziomie jobu tylko pomija job („will report its status as
Success”, w UI „This check was skipped”). Gdy wszystkie joby są pominięte, run kończy się jako **Skipped** i zostaje
na liście runów. Nie ma `if:` na poziomie całego workflow.

Workflowy silnika (`engine/.github/workflows/`):

| Workflow | `on:` | `if:` jobu | Kiedy powstaje pusty run |
|---|---|---|---|
| `owner-request.yml` | `issues: types: [closed]` | `vars.AUTOPILOT == 'on' && contains(labels, 'owner-request')` | **każde** zamknięcie issue bez etykiety `owner-request` przez człowieka/PAT, oraz każde zamknięcie przy `AUTOPILOT` off |
| `session.yml` | `workflow_dispatch` | `prep`: `vars.AUTOPILOT == 'on'` | dispatch przy `AUTOPILOT` off |
| `tokenless.yml` | `workflow_dispatch` | `stage`: `vars.AUTOPILOT == 'on'` | dispatch przy `AUTOPILOT` off (9 przypadków: 2026-10-31…11-02, czasy testów e2e) |
| `resume.yml` | `workflow_dispatch` | `wait`: `vars.AUTOPILOT == 'on'` | dispatch przy `AUTOPILOT` off |
| `watchdog.yml` | `schedule` co 30 min, `workflow_dispatch` | brak | nigdy Skipped (run zawsze coś robi) |
| `tokenless-job.yml` | `workflow_call` | brak | — (wywoływany z `tokenless.yml`) |

Przykład z repo: issue #246–#248 i #255–#257 (np. #255 „Policymaker at the start of iteration 0011”, etykieta
`role:policymaker`) zamknął użytkownik `Kucze3205` — każde dało run `owner request #N closed` ze statusem Skipped,
bo nie miało etykiety `owner-request`.

Dlaczego tego nie da się odfiltrować w `on:`: dla `issues` jedynym filtrem jest `types` (np. `closed`, `labeled`).
Nie ma filtra po etykiecie — `labeled` odpala się dla każdej etykiety
([Events that trigger workflows → issues / label](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#issues)).
Zamknięcia przez `GITHUB_TOKEN` (silnik) runów nie tworzą; tworzą je zamknięcia przez człowieka lub PAT (np. skrypt
e2e, `gh` właściciela).

Możliwości zapobiegania (do decyzji w tickecie wykonawczym, nie ukrywanie):

1. **Usunąć wyzwalacz `issues`** z `owner-request.yml` i zostawić tylko sprzątanie przez watchdog (już dziś zbiera
   odpowiedzi, których zdarzenie zginęło). Koszt: odpowiedź właściciela czeka do 30 min.
2. **Dać runowi pracę przy każdym zamknięciu issue** — np. odświeżenie Loop status — wtedy run nie jest pusty.
   Run nadal powstaje przy każdym zamknięciu, ale coś robi.
3. Dla `AUTOPILOT` off: **wyłączać workflowy** (`gh workflow disable`/`enable`, REST
   `PUT /repos/{owner}/{repo}/actions/workflows/{id}/disable`) zamiast polegać na `if:` — wyłączony workflow nie
   tworzy runów. Druga strona: kto dispatchuje `tokenless`/`session` przy wyłączonej pętli (u nas — testy e2e) i
   czy dispatch ma się wtedy w ogóle wydarzyć.
4. Kasowanie runów Skipped przez API (`DELETE .../actions/runs/{id}`) jest możliwe, ale to ukrywanie — właściciel
   tego nie chce.

## Czego nie udało się potwierdzić w źródłach

- Limit długości linii i rozmiaru logu jobu (nieopublikowany).
- Czy `GITHUB_TOKEN` może wykonać `pinIssue`.
- Renderowanie Mermaid w Summary runu (działa w praktyce, brak w dokumentacji).
- Kotwica `#diff-…` na stronie porównania (na stronie commita potwierdzona).
- Kiedy UI odświeża nazwę kroku zbudowaną z `steps.*.outputs`.

Wszystkie pięć da się rozstrzygnąć jednym prototypowym workflowem w tym repo.
