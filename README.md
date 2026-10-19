# UCL Badminton session allocation

This is the program we use to decide who gets a place in our social sessions when more people sign up than there are spaces. It is public so that any member can see exactly how places are decided. You don't need to read any code to understand it: the first half of this page explains it in plain English.

This repository contains no member data. Sign-up lists, membership lists and results are kept private.

## What members need to know

**The aim:** everyone gets a fair turn. If you have played a lot recently, people who haven't played much go ahead of you. If you were turned away last time, you move up.

### How places are decided

Everyone who signs up gets a priority score. **A lower score means you are placed earlier.** Your score depends on three things from past allocations:

| What | Effect on your score |
| --- | --- |
| Each time you have been given a place | Moves you back (+20) |
| Each time you signed up and were turned away | Moves you forward (-10) |
| Each week since you last played | Moves you forward (-15 per week, up to 8 weeks) |

New members count as having never played, so they start with the full 8-week bonus.

Then there is one small extra: each additional session you tick (beyond the first) moves you forward by 2 points. This only matters when two people are otherwise level, so ticking more sessions helps a little but never beats playing less often.

### Example

| Person | History | Score |
| --- | --- | --- |
| A | New member | -120 |
| B | Never played, turned away twice | -140 |
| C | Played once, four weeks ago | -25 |
| D | Played once, last week | +20 |
| E | Played three times, last two weeks ago | +45 |

B is placed first, then A, C, D and E. If there are not enough seats, E (who has played most recently and often) is the one left out.

### Placing people in sessions

Going down the list from the lowest score, each person is given a place if the sessions they ticked still have room. Someone with a worse score can never take a place from someone with a better score. If a session is full, the program moves on to the next person, who may still fit in a different session. When someone ticked several sessions, the program picks one of them at random.

### Partners

If you sign up with a partner, you will both be placed in the same session or not placed at all. Each of you must enter the other's UCL email, and you can only be placed in sessions you both ticked. A pair's score is the score of whichever partner has the worse (higher) score, so pairing up can never move you ahead of where you would be on your own. The cost is that the partner with the better score is held back to match. If partner details don't match (for example only one of you named the other), you are treated as individual sign-ups.

### Where chance comes in

Chance is used in two places only: to break ties between people with the same score, and to choose between several sessions you ticked. Everything else follows the rules above. The random number generator uses a fixed number (`SEED` in `main.py`), so the same sign-ups always give the same result.

### Questions or concerns

If you think you were allocated unfairly, contact the committee.

---

# For developers

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

- `SIGNUPS_DIR` / `SIGNUPS_GLOB`: the sign-up form exports (`.xlsx`). The newest is used automatically: the highest `(Week N)` in the filename. Column headers must match the question text in the `Col` enum. The partner column is optional.
- `MEMBERSHIPS_DIR` / `MEMBERSHIPS_GLOB`: the club membership lists, which need an `Email` column. The newest is used automatically, by the timestamp in the filename. Set `CHECK_MEMBERSHIP = False` to skip the membership check.
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
3. **Scoring.** Lower scores are placed first. Per person: `20*played - 10*missed_out - 15*min(recency, 8)`. A group's score is the worse (higher) of its members' scores, minus `2` per extra session ticked, so flexibility only decides near-ties. Newcomers count as never having played, with recency at the cap of 8.
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
