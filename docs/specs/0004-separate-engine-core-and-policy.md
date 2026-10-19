# Rozdzielenie engine, warstwy wykonawczej i polityki

## Problem Statement

Setup instaluje skille wykonawcze i role polityki we wspólnym zbiorze zasobów,
a orkiestrator jest aktywnym elementem szablonu jeszcze przed wyborem shapera.
Układ opisuje własność Policymakera, ale zaciera miejsce wykonywania ról.
Warstwa wykonawcza ma dostęp do plików polityki i historii repozytorium, chociaż
zgodnie z zamysłem właściciela ma otrzymywać jedynie własne instrukcje oraz
możliwość składania sugestii zmian.

ADR-0004 przyjmuje rozdzielenie zasobów i egzekwowaną granicę widoczności.
ADR-0001–0003 są już zaimplementowane; ta zmiana musi zachować ich gwarancje.

## Solution

Nowa pętla ma trzy odrębne obszary: engine, zasoby warstwy wykonawczej i zasoby
warstwy polityki. Każdy aktywny skill należy do jednej warstwy. Engine udostępnia
sesji właściwe instrukcje i narzędzia oraz egzekwuje granice dostępu.

Warstwa wykonawcza nie odczytuje ani nie wykonuje wewnętrznych zasobów polityki.
Przekazuje sugestie przez istniejący interfejs. Policymaker może tworzyć i zmieniać
skille obu warstw, również własny, zachowując chronione kontrakty engine oraz
zaakceptowane limity właściciela.

Setup wybiera i instaluje potrzebne role dopiero podczas przygotowania pętli.
Korzysta z nieaktywnych szablonów oraz istniejących skilli globalnych i projektowych,
w tym skilli Matta Pococka. Zarządzane definicje obu warstw tworzą spójną rewizję
aktywowaną i przywracaną razem.

## User Stories

1. Jako właściciel chcę rozpoznawać engine i zasoby każdej warstwy, aby ich przeznaczenie było jednoznaczne.
2. Jako właściciel chcę oddzielić miejsce używania skilla od prawa do jego zmiany, aby Policymaker mógł zarządzać rolami wykonawczymi.
3. Jako właściciel chcę wybierać początkowego shapera podczas setupu, aby nie aktywować przypadkowego orkiestratora.
4. Jako właściciel chcę otrzymywać szablony poza aktywnymi zasobami, aby ich dostarczenie nie oznaczało ich uruchomienia.
5. Jako właściciel chcę, aby setup korzystał z istniejących skilli globalnych, w tym Matta Pococka, aby wykorzystać dostępne praktyki.
6. Jako właściciel chcę adaptacji skilli projektowych, aby role odpowiadały środowisku celu.
7. Jako właściciel chcę lokalnych definicji skilli pętli, aby ich rozwój nie zmieniał globalnych instalacji.
8. Jako sesja wykonawcza chcę otrzymać własny skill i potrzebne instrukcje, aby wykonać zadanie bez poznawania wnętrza polityki.
9. Jako właściciel chcę blokady odczytu zasobów polityki przez wykonawców, aby rozdział był egzekwowany technicznie.
10. Jako właściciel chcę tej samej granicy przy odczycie przez shell i historię Git, aby zmiana narzędzia nie omijała izolacji.
11. Jako właściciel chcę filtrowania wejść sesji, aby raporty, eksporty i stan nie ujawniały wykonawcom wnętrza polityki.
12. Jako sesja wykonawcza chcę przekazywać sugestie zmian, aby zgłaszać braki bez dostępu do polityki.
13. Jako Policymaker chcę odbierać sugestie i zapisywać ich rozstrzygnięcia, aby zachować istniejący obieg informacji.
14. Jako orkiestrator chcę znać dostępne role wykonawcze i ich kontrakty, aby planować poprawne zadania.
15. Jako właściciel chcę niezależnych uprawnień narzędzi dla ról obu warstw, aby wspólne narzędzie nie oznaczało wspólnego dostępu.
16. Jako Policymaker chcę tworzyć i zmieniać skille wykonawcze, aby rozwijać sposób realizacji celu.
17. Jako Policymaker chcę zmieniać skille polityki, w tym własny, aby rozwijać sposób zarządzania pętlą.
18. Jako właściciel chcę chronionych kontraktów engine, aby samodzielna zmiana skilla nie rozszerzała władzy Policymakera.
19. Jako właściciel chcę przypisania audytu i utrzymania evaluatora do polityki, aby odróżnić je od pracy nad celem.
20. Jako evaluator verifier chcę niezależnie sprawdzić zmianę evaluatora, aby nie aktywować niesprawdzonego pomiaru.
21. Jako właściciel chcę wspólnej aktywacji zależnych definicji, aby konfiguracja odpowiadała dostępnym skillom.
22. Jako właściciel chcę wspólnego rollbacku definicji obu warstw, aby odzyskiwać działający zestaw.
23. Jako właściciel chcę zachowania produktów pracy i raportów podczas rollbacku, aby nie tracić wyników ani dowodów.
24. Jako właściciel chcę identyfikowalnej rewizji i pochodzenia zmian, aby móc wyjaśnić zachowanie iteracji.
25. Jako właściciel chcę zachowania braku tokenu GitHub w sesjach, aby rozdzielenie katalogów nie osłabiło istniejącej granicy.
26. Jako właściciel chcę bramki uruchamianej z zaakceptowanego drzewa, aby kandydat nie oceniał sam siebie.
27. Jako właściciel chcę nowych setupów z pełnym rozdziałem, aby nie wymagały ręcznych poprawek przed uzbrojeniem.

## Implementation Decisions

- **Klasyfikacja zasobów:** engine obejmuje mechanikę uruchamiania sesji, dostęp,
  bramki, merge, rollback i chronione kontrakty. Warstwa wykonawcza obejmuje
  orkiestratora oraz role pracujące nad celem. Polityka obejmuje Policymakera,
  audit, utrzymanie evaluatora i evaluator verifiera. Sam evaluator jest pomiarem
  wywoływanym przez engine, odrębnym od roli, która go utrzymuje.
- **Własność skilli:** aktywny skill ma jedną warstwę. Osobne skille mogą pochodzić
  z tego samego źródła. Narzędzia mogą występować w obu warstwach, z niezależnymi
  profilami dostępu i zachowaniem zaakceptowanych limitów.
- **Moduły objęte zmianą:** setup i jego materiały, konfiguracja ról, przygotowanie
  środowiska sesji, bezpieczeństwo wykonania, eksporty i raporty, aktywacja polityki,
  identyfikacja rewizji, pochodzenie zmian, rollback oraz dokumentacja.
- **Przygotowanie sesji:** engine wyznacza widoczny zbiór wejść i dostępne narzędzia.
  Wykonawca otrzymuje własne instrukcje, zadanie i potrzebne kontrakty. Orkiestrator
  otrzymuje katalog możliwości ról wykonawczych wystarczający do budowania grafu,
  zamiast pełnego wewnętrznego katalogu polityki.
- **Granica odczytu:** ukrycie obejmuje pliki robocze, historię i obiekty Git,
  zasoby dostępne poza katalogiem pracy oraz wejścia przekazywane przez engine.
  Usunięcie katalogu z widoku lub wyłączenie odkrywania skilli samo w sobie nie
  spełnia wymagania. Granica obowiązuje również role skryptowe oraz sesje online;
  dostęp do internetu nie przyznaje dostępu do polityki.
- **Minimalne interfejsy:** wykonawcy mogą otrzymać instrukcje dotyczące ich pracy
  oraz wyniki potrzebne do jej kontynuacji. Wewnętrzne prompty, skille, raporty
  sesji polityki i jej konfiguracja nie mogą trafiać do nich przez eksporty,
  stan pętli, raporty lub historię. Sugestie i ich rozstrzygnięcia zachowują
  dotychczasowy kontrakt bez otwierania dostępu do wewnętrznych materiałów.
- **Zapis i przyjęcie zmian:** Policymaker zachowuje prawo zmiany skilli obu warstw
  i własnego skilla; wykonawcy proponują takie zmiany przez sugestie. Nowe położenie
  definicji nie zmienia ich w zwykłe produkty pracy podlegające change gate.
  Zachowane pozostają kontrola zakresu zapisu, chronione zasoby engine i wymaganie
  niezależnej weryfikacji zmian evaluatora.
- **Rewizja i aktywacja:** identyfikacja zarządzanych definicji obejmuje oba obszary.
  Zależny zestaw jest aktywowany razem na granicy iteracji; samo zakończenie sesji
  Policymakera nie oznacza aktywacji. Niespełnione warunki przyjęcia nie mogą
  pozostawić aktywnej połowy tego zestawu. Rejestry i pochodzenie zmian obejmują
  wszystkie zarządzane definicje, niezależnie od ich położenia.
- **Rollback:** engine przywraca spójny zestaw definicji z obu warstw z ostatniego
  dobrego stanu, a przed pierwszą zamkniętą iteracją z setup baseline. Zachowuje
  produkty pracy, raporty i historię. Wyzwalacze pozostają obecne: przekroczenie
  skonfigurowanego quiet time albo dwie kolejne nieudane sesje Policymakera.
  Dotychczasowe zasady liczenia czasu oczekiwania na właściciela pozostają w mocy.
- **Setup:** dostarczone szablony są nieaktywne. Setup wybiera i adaptuje role do
  celu i podejścia, wykorzystując dostępne skille globalne, projektowe i szablony,
  a nowe tworzy, gdy brak odpowiedniego źródła. Aktywne lokalne definicje trafiają
  do właściwej warstwy; źródłowe instalacje globalne pozostają niezmienione.
  Wybrany shaper oraz wymagane role polityki muszą być gotowe przed uzbrojeniem.
- **Zachowanie ADR-0001:** engine nadal jest kopią należącą do repozytorium pętli,
  oznaczoną wersją; zmiana pluginu nie aktualizuje istniejących pętli automatycznie.
- **Zachowanie ADR-0002:** żadna sesja agenta nie dostaje tokenu GitHub. Engine
  obsługuje GitHub, przyjmuje wynik pracy przez zaufaną granicę i publikuje zmiany.
  Mechanizm izolacji musi umożliwiać ten obieg bez ujawniania historii polityki.
- **Zachowanie ADR-0003:** komenda change gate pochodzi z zaakceptowanego drzewa,
  otrzymuje kandydata i baseline; engine nie wprowadza własnego zaakceptowanego
  wyniku pomiaru. Zachowana zostaje semantyka odmowy i braku rozstrzygnięcia.
- **Dokumentacja:** słownik, instrukcje setupu i dokumentacja engine opisują ten sam
  podział. Nazwy katalogów i szczegółowy mechanizm izolacji są wyborami
  implementacyjnymi; nie zastępują powyższych kontraktów zachowania.

## Testing Decisions

Właściciel zaakceptował następujące granice testów w rozmowie poprzedzającej publikację.

- Główną granicą jest istniejący przepływ przygotowania sesji, wykonania pracy
  i przyjęcia wyniku. Preferowane są obecne testy z rzeczywistymi tymczasowymi
  repozytoriami Git i zastępowaną integracją GitHub.
- Testy opisują obserwowalne zachowanie: co sesja może odczytać, jakie instrukcje
  otrzymuje, jakie zmiany przyjmuje engine i jaki zestaw staje się aktywny.
  Same asercje nazw katalogów albo obecności flag nie dowodzą izolacji.
- Testy wykonawcze na Linuxie korzystają z przygotowanego środowiska sesji:
  kontrolowany odczyt produktu pracy udaje się, a odczyt zasobów polityki przez
  narzędzia plikowe, shell, historię i obiekty Git nie ujawnia ich treści.
  Scenariusze obejmują role modelowe i skryptowe oraz profile online i offline.
  Próby przez narzędzia modelu wymagają odpowiedniego testu integracyjnego;
  zastąpienie modelu nie dowodzi egzekwowania jego narzędzi.
- Testy stagingu, raportów i eksportów sprawdzają brak ujawnienia materiałów
  polityki wykonawcom, poprawny katalog ról dla orkiestratora oraz zachowany obieg
  sugestii. Policymaker zachowuje potrzebny dostęp do obu warstw.
- Testy aktywacji i rollbacku używają zmiany shapera połączonej ze zmianą jego
  definicji. Sprawdzają aktywację i przywrócenie całego zestawu, zgodność rewizji
  oraz zachowanie produktów pracy i raportów. Osobno obejmują setup baseline
  i niezakończoną lub odrzuconą weryfikację evaluatora.
- Testy setupu obejmują wybór ról z szablonu, skilla globalnego i projektowego,
  nieaktywność niewybranych szablonów oraz brak modyfikacji instalacji źródłowych.
  Wykorzystują kontrolowane źródła zamiast zależności od prywatnych skilli autora.
- Istniejące testy sesji, przepływu iteracji, bezpieczeństwa, merge, provenance,
  eksportów i watchdog stanowią punkt wyjścia. Testy regresji ADR-0001–0003
  pozostają wymagane. Raport walidacji rozróżnia testy lokalne, testy Linux
  i testy wymagające dostępu do providera; pominięcie nie jest wynikiem pozytywnym.

## Out of Scope

- Migracja i automatyczne aktualizowanie istniejących pętli.
- Tryb ponownego setupu służący review, uzupełnieniu i rekonfiguracji instalacji.
  Jest zachowany jako osobne przyszłe wymaganie właściciela.
- Zmiana providera, substratu wykonania, kanału owner requests lub zaakceptowanych
  limitów oraz zmiana warunków uruchamiania rollbacku.
- Ponowna implementacja ADR-0001–0003 albo rozszerzanie ich o nowe mechanizmy.
- Praca nad celem konkretnej pętli, publikowanie wdrożeń i uzbrajanie pętli.

## Further Notes

Źródłem decyzji jest zaakceptowany ADR-0004 i rozmowa z właścicielem. ADR-0001–0003
są ograniczeniami regresji. Sprawdzenie obecnego kodu potwierdziło istniejący
kanał sugestii oraz brak pełnej izolacji odczytu polityki. Przed przygotowaniem
specyfikacji przeszło 20 testów statycznych i 22 testy merge; nie stanowi to
walidacji przyszłego podziału.

Specyfikacja jest wejściem do podziału na tickety implementacyjne. Nie jest
potwierdzeniem wdrożenia ADR-0004 ani planem migracji działających pętli.
