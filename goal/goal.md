# Goal

Create two files, and nothing else:

1. `src/greeting.txt` containing the single line `hello from perpetua`.
2. `src/farewell.txt` containing the single line `goodbye after iteration N`, where N is the number of the iteration in
   which `src/greeting.txt` was merged. N must be a closed iteration with a record `iterations/NNNN.json`; an iteration
   has no record until it closes, so this file can only be written in a later iteration than the first one.

Each file is checked by the change gate (`policy/evaluator/gate.py`).
