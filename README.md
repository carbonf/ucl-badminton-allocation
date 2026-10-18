# ucl-badminton-allocation

Allocates UCL Badminton social-session sign-ups to sessions. It favours people who have played less, been turned away more, or not played for a while. It handles partner sign-ups and keeps every session within capacity.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Files

| Path | Purpose |
| --- | --- |
| `main.py` | The allocator. |
| `generate_history.py` | Builds an initial `data/history.csv` from an existing allocation workbook. |
| `data/` | Inputs and history. Gitignored, because it contains personal data. |
| `outputs/allocations.xlsx` | Results workbook, written only with `--commit`. |
| `allocate_week v2.py` | Legacy script, kept for reference. |

## Inputs

Paths and settings are constants at the top of `main.py`:

- `SIGNUPS_PATH`: the sign-up form export (`.xlsx` or `.csv`). Column headers must match the question text in the `Col` enum. The partner column is optional.
- `MEMBERSHIPS_PATH`: the club membership list, which needs an `Email` column. Set `CHECK_MEMBERSHIP = False` to skip the membership check.
- `HISTORY_PATH` (`data/history.csv`): columns `ucl_email, played, missed_out, recency`. If it is missing, everyone is treated as a newcomer.
- `CAPACITIES`: seats per session, keyed by the shortened session name (e.g. `Sat 17 Oct 1:30pm`). Sessions not listed use `DEFAULT_CAPACITY` and print a warning. Update these every week.
- `SEED`: random seed. Runs with the same inputs and seed give the same result.

## Usage

```bash
python main.py              # dry run with full debug output; nothing is saved
python main.py --quiet      # dry run with minimal output
python main.py --commit     # save the workbook and update the history
```

A run without `--commit` writes nothing, not even the workbook. Use it to check the result first. Run with `--commit` once, when you are happy with the allocation. Committing twice ages everyone's recency twice.

### Initial history

If you have a past allocation workbook but no history file:

```bash
python generate_history.py [allocation.xlsx] [--output data/history.csv] [--overwrite]
```

Everyone on a session sheet gets `played=1`.

## How it works

1. **Cleaning.** Rows are rejected if they have no UCL email, were superseded by a later submission from the same email, selected no sessions, or are not on the membership list.
2. **Groups.** Each person is a single. Two people form a pair only if they name each other as partner. A pair needs 2 seats in one session, and the sessions it can use are those both ticked. One-sided, self-naming, unknown-partner or no-common-session declarations fall back to singles, with a warning.
3. **Scoring.** Lower scores are placed first. Per person: `20*played - 10*missed_out - 15*min(recency, 8)`. A group's score is the mean of its members' scores, minus `2` per extra session ticked, so flexibility only decides near-ties. Newcomers count as never having played, with recency at the cap of 8.
4. **Matching.** Groups are taken in score order, with small random noise to break ties. Each group is accepted only if everyone accepted so far can still be seated. This is checked by solving a small integer linear program (`scipy.optimize.milp`). Groups that don't fit are skipped, and later ones are still tried. Higher-priority groups are never bumped by lower-priority ones.
5. **Checks.** `verify_allocation` always runs. It fails if anyone is placed in a session they didn't tick, if a pair is split, or if a session is over capacity.

Weights are `W_PLAYED`, `W_MISSED`, `W_RECENCY`, `RECENCY_CAP` and `W_FLEX` in `main.py`.

## History updates (`--commit`)

- Everyone's `recency` goes up by 1 (one more run since they played).
- People allocated: `played += 1`, `recency = 0`.
- People not allocated: `missed_out += 1`.
- New people are added first, starting at `recency = 8`.

## Output

`outputs/allocations.xlsx` has an `All allocated` sheet, one sheet per session, and a `Rejected` sheet. The `Rejected` sheet gives a reason for each rejection: no email, superseded, no sessions, not a member, or all selected sessions full.

## Known limitations

- A seat can stay empty if the next group in line is a pair and only one seat is free.
- `SEED` is fixed, so change it between weeks if you don't want identical tie-breaking.
- There is no waitlist.