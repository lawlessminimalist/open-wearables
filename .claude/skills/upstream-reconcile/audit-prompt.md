# Shadow-audit subagent prompt (template)

Fill the `{{…}}` slots and send one agent per drifted patch. Use only for patches
`check_upstream.py` reports as **CHANGED symbol**; a "methods unchanged" row needs
no agent. Expect ~90k tokens per audit; the template exists so that budget is
spent on the diff, not on the agent rediscovering the repo.

```
You are auditing one monkey-patch in a fork, at {{repo_path}} (branch {{branch}},
which has just merged upstream/main). Read-only: do NOT edit any file. Report only.

Patch file: ow-patches/local/{{patch_id}}.py
Kind: {{replacement_kind}} of {{symbols}} in {{upstream_file}}.
Intended fork change (the ONLY intended difference): {{what_we_changed}}

check_upstream.py says these symbols changed upstream since the baseline:
{{changed_symbols}}
Start from its output:  python3 ow-patches/check_upstream.py --explain {{patch_id}}
It prints the exact `git diff <baseline> upstream/main -- <file>` to read.

Do this:
1. Read the patch file. Identify exactly which symbols install() replaces and any
   `app.*` imports it relies on.
2. Read upstream's CURRENT body of each changed symbol on the working tree (the
   file is upstream's; confirm with `git diff upstream/main HEAD -- {{upstream_file}}`
   and say so if it is NOT empty).
3. Diff patch body vs upstream body per symbol (write both to the scratchpad and run
   `diff`). Answer:
   a. Is the intended fork change the ONLY difference?
   b. What did upstream add that the patch lacks? Look for: new kwargs / response
      fields, new columns in SELECT / GROUP BY / result dict, de-dup flags, new
      joins or LATERALs, constant lookups replacing inline mappings, changed
      signatures.
   c. What did the FORK add that upstream lacks, beyond the intended change? Any
      fork-only kwarg that would vanish if upstream's body were copied (source=,
      device_model=, is_daily_total=, zone_offset=)?
4. Does upstream now satisfy retire_when? ({{retire_when}})
5. Verdict: KEEP AS-IS / NEEDS REBASE (give the exact replacement code) / SHOULD
   RETIRE (cite the upstream code).

End with a "Consider also:" section. Cite file:line. Under 700 words.
```

After the agent reports: act on every "Consider also" item or write down in
PATCHES.md why not. The `vo2_max` identity split was flagged in exactly such a note
and left unactioned.
