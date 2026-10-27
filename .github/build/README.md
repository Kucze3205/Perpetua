# Unattended spec build

`build-spec.yml` builds the sub-issues of a spec issue overnight without the owner. Each wave works like this:

1. **Plan.** Take every open sub-issue whose native blockers are all closed. Skip those labelled `build:failed` or
   `needs-owner`.
2. **Work.** Build each ticket on its own runner, in one Claude session (`claude-sonnet-5-5`, effort `medium`). The
   session never holds the GitHub token.
   - Engine tests must pass. Fix rounds resume the same session.
   - The integration branch is merged in; conflicts are resolved in the same session.
   - The result is pushed to the integration branch.
3. **Report.** The ticket gets a comment and is closed.
   - A failure is retried once in a later wave, from the pushed `build/issue-N` branch; a second failure labels it
     `build:failed`.
   - A usage limit makes the next wave wait for the reset.
4. **Next.** The workflow dispatches the next wave itself.

When nothing is ready, it posts a summary on the spec and opens a pull request from the integration branch to `main`.

Start a build:

    gh workflow run build-spec.yml -f parent=211 -f branch=engine-v2 -f wave=1

Start with `-f dry_run=true` to see the first wave without building it.

Stop a build: cancel the running `build-spec` run in Actions. Nothing else dispatches it.

Every run uploads one `build-N` artifact per ticket, holding the status and the session transcripts.
