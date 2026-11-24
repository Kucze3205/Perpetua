# Czy odczyty z cache obciążają limit 5h subskrypcji Claude?

Ticket: #418. Sprawdzono 2026-11-24. Języki przeszukane: angielski (źródła pierwotne Anthropic; dokumentacja po polsku nie jest tam publikowana w innej treści niż oryginał).

## Odpowiedź w skrócie

Źródła pierwotne mówią wprost tylko tyle, że **cache obniża zużycie limitu subskrypcji** („cached portions count less”) i że odczyt z cache w Claude Code nadal „draws usage”. **Nie podają żadnej wagi** (mnożnika) dla `cache_read`, `cache_write` ani tokenów wyjściowych w limicie 5h/tygodniowym Pro/Max. Wniosek „cache read = zużycie limitu, ale tańsze niż świeże wejście” jest poparty źródłami; wniosek „o ile tańsze” nie jest.

## Co źródła mówią wprost

1. Limit subskrypcji zależy od wielu czynników, bez wzoru. Help Center, „How do usage and length limits work?” (https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work): „Your usage is affected by several factors, including the length and complexity of your conversations”, „the features you use, which Claude model you're chatting with, and the effort level you've selected”. Artykuł nie wspomina o cache ani o wagach tokenów.
2. Cache liczy się mniej. Help Center, „Usage limit best practices” (https://support.claude.com/en/articles/9797557-usage-limit-best-practices): „Content in projects is cached and counts less against your limits when reused.”; „cached portions count less against your limits”; „If you come back to a project after a long break, your first message counts that content in full again.” Dotyczy treści projektów w claude.ai; brak liczby.
3. Claude Code, odczyt z cache nadal zużywa limit. Claude Code docs, „Manage costs effectively” (https://code.claude.com/docs/en/costs), sekcja „Why usage climbs in a long session”: „Claude Code re-reads that history at the cached token rate, so a one-line question in a session that has been open all day still draws usage for the whole conversation.” Ta sama strona: „Cache misses: your first message after a break longer than the cache lifetime misses the cache and reprocesses your full context.” Oraz o podpowiedziach promptu: „mostly cache reads plus a few output tokens” i Claude Code „skips it while your account is close to or at its usage limit” (czyli takie żądania są traktowane jako zużywające limit).
4. Sprawozdawczość: `/usage` pokazuje liczniki „input, output, cache read, cache write”, ale dla subskrybentów „the session cost figure isn't relevant for billing purposes”; zużycie planu to osobne paski (https://code.claude.com/docs/en/costs).
5. Claude Code, TTL cache na subskrypcji: 1 godzina dla głównej rozmowy w ramach planu, 5 minut dla subagentów i reszty (https://code.claude.com/docs/en/prompt-caching.md, „Which TTL each request gets”).
6. Cennik API (nie subskrypcja): odczyt cache 0,1x ceny wejścia na większości modeli, 0,05x na Opus 5.5 i Sonnet 5.5, 0,025x na Fable 5.1/5.2; zapis 5m 1,25x, zapis 1h 2x; wyjście kilka razy droższe niż wejście, np. Sonnet 5.5 $2 wejście / $10 wyjście (https://platform.claude.com/docs/en/about-claude/pricing).
7. Rate limity API (nie subskrypcja): `cache_read_input_tokens` NIE liczą się do ITPM, `cache_creation_input_tokens` i `input_tokens` tak (https://platform.claude.com/docs/en/api/rate-limits, „Cache-aware ITPM”). Ta strona dotyczy organizacji API, nie okna 5h Pro/Max.

## Czego źródła nie mówią

- Brak publikowanej wagi `cache_read` względem `input` w limicie 5h i tygodniowym Pro/Max. Brak informacji o wadze `cache_write` i `output` w tym limicie.
- Brak stwierdzenia, że odczyt z cache jest wolny od limitu subskrypcji. Reguła ITPM z API (cache read nie liczy się) jest innym systemem; źródła nie mówią, że przenosi się na subskrypcję.
- Brak informacji, czy limit subskrypcji liczy się w tokenach, czy w ekwiwalencie kosztu.

## Wnioski (nasze, nie źródeł)

- Rozsądne założenie robocze: waga cache read jest dodatnia i mniejsza niż świeżego wejścia. Dowód: pkt 2 i 3 (zużywa, ale „less”). Jeśli limit subskrypcji śledzi ceny API, cache read to 0,025–0,1x wejścia, a output kilka razy więcej niż wejście; to hipoteza, bez potwierdzenia w źródłach.
- Dla #402: stwierdzenie „Token counters are not quota” jest nietrafne w kierunku „liczniki nic nie mówią o limicie”. Bezpieczne sformułowanie dla Policymakera: „odczyty z cache zużywają limit, ale mniej niż świeże wejście; dokładna waga nie jest publiczna; długi kontekst odczytywany z cache przy każdym żądaniu nadal kosztuje, a po przerwie dłuższej niż TTL kontekst jest liczony w całości”. Nie wolno podawać konkretnego mnożnika.
- Praktycznie: liczba żądań x długość kontekstu zużywa limit nawet przy 100% trafień cache. Pętle z wieloma turami w długim kontekście i subagenci (TTL 5m, własny cache) są kosztowne.
- Jedyny sposób poznania wagi: pomiar empiryczny (paski `/usage` lub Settings > Usage przed i po znanym przebiegu, przy znanych licznikach tokenów).
