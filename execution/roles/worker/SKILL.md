---
name: worker
description: Writes the one small text file its task asks for, then reports.
---

# Worker

Do exactly what `.session/issue.md` asks.

## Steps

1. Read `.session/issue.md` and `.session/protocol.md`; note every acceptance line.
2. Write the requested file under `src/`.
3. Read the file back and confirm its content matches the acceptance lines.
4. Write `.session/report.md` with status `done`.
