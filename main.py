import random
import io
import re
import shutil
import sys
from datetime import datetime
from enum import Enum
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import milp, LinearConstraint, Bounds

# Configuration
MEMBERSHIPS_DIR = Path("data")
MEMBERSHIPS_GLOB = "Club-society membership list - DO NOT SHARE - *.csv"   # newest timestamp in the name wins
SIGNUPS_DIR = Path("data")
SIGNUPS_GLOB = "UCL Badminton Social Session Signups (Week *).xlsx"   # highest week number wins
OUTPUTS_PATH = Path("outputs/allocations.xlsx")
HISTORY_PATH = Path("data/history.csv")
BACKUP_DIR = Path("data/backups")   # each --commit first copies the files it will overwrite here
HISTORY_COLS = ["played", "missed_out", "recency"]
CHECK_MEMBERSHIP = True
SEED = 69

# Priority score (lower = placed first). Each term is per person:
#   score = W_PLAYED*played - W_MISSED*missed_out - W_RECENCY*recency - W_FLEX*(sessions ticked - 1)
# Random tie-break noise is U(0,1), so keep every weight comfortably above 1.
W_PLAYED = 20          # each past allocation moves you back
W_MISSED = 10          # each signup you were turned away from moves you forward
W_RECENCY = 15         # each committed run since you last played moves you forward...
RECENCY_CAP = 8        # ...up to this many runs (newcomers / never played = the cap)
W_FLEX = 2             # each extra session ticked (pairs: in common) moves you forward;
                       # 4 extra sessions = 8 < W_MISSED, so flexibility only decides near-ties
DEFAULT_CAPACITY = 18
CAPACITIES = {
	"Tue 6:00pm": 15,
	"Sat 1:30pm": 15,
	"Sat 3:30pm": 15,
	"Sun 10:30am": 21,
	"Sun 11:45am": 21,
}

# Verbose diagnostics (scoring, history, matching decisions, checks) are on by default; --quiet turns them off.
# Nothing is written to disk unless --commit is given (workbook and history).
DEBUG = "--quiet" not in sys.argv
COMMIT = "--commit" in sys.argv

# Set during a --commit: where this run's backup and log go. USED_FILES records the input files picked.
RUN_DIR: Path | None = None
USED_FILES: list[str] = []


class Tee(io.TextIOBase):
	"""Writes everything printed to the console into a buffer as well, so a commit can keep its log."""
	def __init__(self, console, buffer):
		self.console, self.buffer = console, buffer

	def write(self, s: str) -> int:
		self.console.write(s)
		self.buffer.write(s)
		return len(s)

	def flush(self) -> None:
		self.console.flush()


def debug(*args) -> None:
	if DEBUG:
		print("[debug]", *args)


class Col(str, Enum):
	# The values MUST BE THE EXACT QUESTION TEXT. Corresponds to columns in the sign-ups sheet
	NAME = "Name"
	EMAIL = "Email"
	FULL_NAME = "Full name"
	UCL_EMAIL_ADDR = "UCL email address"
	SESSIONS = "Please select the sessions you wish to sign up for:"
	PARTNER_EMAIL = "Partner's UCL email"
	BEGINNER = "Do you identify as a beginner, if so, would you prefer to play with other beginners?"

# ---------- Session short names ----------
SESSION_RE = re.compile(r"^\s*(\w{3}) (\d{1,2}:\d{2}[ap]m)")   # "Tue 6:00pm - 8:00pm @ ..." -> "Tue 6:00pm"
SESSION_FULL: dict[str, str] = {}   # short name -> full form text


def shorten_name(full: str) -> str:
	# Takes a full session name and shortens it.

	m = SESSION_RE.match(full)
	shortened = " ".join(m.groups()) if m else full
	
	SESSION_FULL[shortened] = full
	return shortened


def parse_sessions(cell) -> list[str]:
	# Used to parse chosen sessions for each entry

	if pd.isna(cell):
		return []
	return [shorten_name(s.strip()) for s in str(cell).split(";") if s.strip()]

TIMESTAMP_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2}[+-]\d{4})")


def latest_membership_file() -> Path:
	"""Newest membership export, by the timestamp in its filename (e.g. 2026-10-19T00_44_39+0100).
	The timezone offset is parsed, so a +0000 and a +0100 file still compare correctly."""
	stamped = []
	for p in MEMBERSHIPS_DIR.glob(MEMBERSHIPS_GLOB):
		m = TIMESTAMP_RE.search(p.name)
		if m:
			stamped.append((datetime.strptime(m.group(1), "%Y-%m-%dT%H_%M_%S%z"), p))
		else:
			print(f"WARNING: ignoring membership file with no timestamp in its name: {p.name}")
	if not stamped:
		raise FileNotFoundError(f"No membership file matching '{MEMBERSHIPS_GLOB}' in {MEMBERSHIPS_DIR.resolve()}")
	latest = max(stamped)[1]
	USED_FILES.append(f"membership: {latest.name}")
	debug(f"membership: using {latest.name} (newest of {len(stamped)} files)")
	return latest


SIGNUP_WEEK_RE = re.compile(r"Signups \(Week (\d+)\)")


def latest_signups_file() -> Path:
	"""Sign-up export with the highest week number in its filename, e.g. '(Week 2)'.
	Files without a '(Week N)' label (such as old dated ones) are ignored."""
	weekly = []
	for p in SIGNUPS_DIR.glob(SIGNUPS_GLOB):
		m = SIGNUP_WEEK_RE.search(p.name)
		if m:
			weekly.append((int(m.group(1)), p))
	if not weekly:
		raise FileNotFoundError(f"No sign-ups file with a '(Week N)' label matching '{SIGNUPS_GLOB}' in {SIGNUPS_DIR.resolve()}")
	latest = max(weekly)[1]
	USED_FILES.append(f"signups: {latest.name}")
	debug(f"signups: using {latest.name} (highest week of {len(weekly)} files)")
	return latest


def load_file(path: Path) -> pd.DataFrame:
	# Dynamically load file into pandas dataframe based on extension.
	
	if not path.exists():
		raise FileNotFoundError(f"Missing file: {path.resolve()}")
	if path.suffix.lower() in {".xlsx", ".xls"}:
		df = pd.read_excel(path)
	elif path.suffix.lower() == ".csv":
		df = pd.read_csv(path, encoding="utf-8-sig")
	else:
		raise ValueError(f"Unsupported file type: {path.suffix}")
	df.columns = df.columns.str.strip()
	return df

def preview(df: pd.DataFrame, num_rows: int, label: str) -> None:
	cols = [Col.NAME.value, Col.UCL_EMAIL_ADDR.value]
	n = min(num_rows, len(df))
	print(f"\n{label} - showing {n} of {len(df)} rows")
	print(df[cols].head(n).to_string(index=False))

def check_columns(df: pd.DataFrame, cols: list[str], label: str) -> None:
	missing = [c for c in cols if c not in df.columns]
	if missing:
		raise KeyError(f"{label} is missing columns: {', '.join(missing)}")

def tag(df: pd.DataFrame, reason: str) -> pd.DataFrame:
	"""Reduce rows to full name, UCL email and the reason, for the Rejected sheet."""
	out = df.copy()
	out["Reason"] = reason
	cols = [Col.FULL_NAME.value, Col.UCL_EMAIL_ADDR.value, "Reason"]
	return out[[c for c in cols if c in out.columns]]

def clean_signups(df: pd.DataFrame):
	"""Cleans signup entries, rejects invalid formatting. Returns (valid sign-ups, list of rejected DataFrames)."""
	check_columns(df, [Col.NAME.value, Col.UCL_EMAIL_ADDR.value,
					   Col.SESSIONS.value], "Sign-ups")
	df = df.copy()
	rejected: list[pd.DataFrame] = []

	# Construct cleaned fields for each entry
	df["_email"] = df[Col.UCL_EMAIL_ADDR.value].fillna("").astype(str).str.strip().str.lower()
	df["_sessions"] = df[Col.SESSIONS.value].apply(parse_sessions)
	df["_partner"] = (
		df[Col.PARTNER_EMAIL.value].fillna("").astype(str).str.strip().str.lower()
		if Col.PARTNER_EMAIL.value in df.columns else ""
	)
	if Col.BEGINNER.value in df.columns:
		df["_beginner"] = df[Col.BEGINNER.value].fillna("").astype(str).str.strip().str.lower().str.startswith("y")
	else:
		df["_beginner"] = False
		print(f"WARNING: no beginner question column found; counting nobody as a beginner")

	# Remove entries with blank emails
	blank = df["_email"] == ""
	if blank.any():
		rejected.append(tag(df[blank], "No UCL email given"))
	df = df[~blank]

	# Keep only each person's latest submission. Earlier ones are dropped silently: the latest
	# one is what gets allocated or rejected, so listing the old one would hide the real reason.
	if "Completion time" in df.columns:
		df = df.sort_values("Completion time")
	dup = df.duplicated(subset="_email", keep="last")
	if dup.any():
		debug(f"{int(dup.sum())} earlier duplicate submission(s) ignored (latest per email is used)")
	df = df[~dup]

	# Remove entries with no selected sessions
	none_ticked = df["_sessions"].apply(len) == 0
	if none_ticked.any():
		rejected.append(tag(df[none_ticked], "No sessions selected"))
	df = df[~none_ticked]

	return df.reset_index(drop=True), rejected

def split_members(signups: pd.DataFrame, members: pd.DataFrame):
	"""Checks if people in signups are club members. Filters signups to retain only members. Returns df of nonmembers too"""
	
	check_columns(members, ["Email"], "Membership list")

	# Create set of member emails from df
	member_emails = set(members["Email"].astype(str).str.strip().str.lower())
	
	# Use boolean mask to filter member rows
	is_member = signups["_email"].isin(member_emails)
	return signups[is_member].reset_index(drop=True), signups[~is_member]


def build_groups(df: pd.DataFrame) -> list[dict]:
	"""Each group is one player, or two partners who must share a session."""
	# STEP 1 of the algorithm: turn people into "groups". The matcher only ever sees groups,
	# so a pair is a single unit that needs 2 seats in ONE session.
	idx_by_email = {e: i for i, e in df["_email"].items()}
	used, groups = set(), []

	for i, row in df.iterrows():
		if i in used:
			continue
		# Look up the partner the person named (if that partner also signed up).
		j = idx_by_email.get(row["_partner"])
		if row["_partner"] and j is None:
			print(f"WARNING: {row['_email']} named partner {row['_partner']} who has no valid sign-up; treated as single.")
		elif j == i:
			print(f"WARNING: {row['_email']} named themselves as partner; treated as single.")
		elif j is not None and df.at[j, "_partner"] != row["_email"]:
			# One-sided declaration: both people must name each other, otherwise no pair.
			print(f"WARNING: {row['_email']} named {row['_partner']}, who did not name them back; treated as single.")
		elif j is not None and j not in used:
			# Mutual declaration. A pair can only play in sessions BOTH of them ticked.
			common = [s for s in row["_sessions"] if s in df.at[j, "_sessions"]]
			if common:
				groups.append({"members": [i, j], "sessions": common,
							   "label": f"{row['_email']} + {df.at[j, '_email']}"})
				used.update({i, j})
				continue
			# No overlap: they cannot be seated together, so fall back to individuals.
			print(f"WARNING: {row['_email']} and partner have no common session; treated separately.")
		groups.append({"members": [i], "sessions": row["_sessions"], "label": row["_email"]})
		used.add(i)
	n_pairs = sum(len(g["members"]) == 2 for g in groups)
	debug(f"groups: {len(groups)} total = {n_pairs} pairs + {len(groups) - n_pairs} singles ({len(df)} people)")
	return groups

def load_history() -> pd.DataFrame:
	"""One row per person: times played, times turned away, runs since they last played."""
	if not HISTORY_PATH.exists():
		debug(f"history: no file at {HISTORY_PATH}; everyone is treated as a newcomer")
		return pd.DataFrame(columns=HISTORY_COLS).astype(int).rename_axis("ucl_email")

	h = pd.read_csv(HISTORY_PATH)
	h["ucl_email"] = h["ucl_email"].str.strip().str.lower()
	h = h.set_index("ucl_email")
	if "recency" not in h.columns:           # older history files: treat as played long ago
		debug(f"history: {HISTORY_PATH} has no 'recency' column; everyone set to {RECENCY_CAP}")
		h["recency"] = RECENCY_CAP
	h["recency"] = h["recency"].fillna(RECENCY_CAP)
	h[HISTORY_COLS] = h[HISTORY_COLS].astype(int)
	debug(f"history: loaded {len(h)} people from {HISTORY_PATH} "
		  f"(total played={h['played'].sum()}, missed={h['missed_out'].sum()}, "
		  f"avg recency={h['recency'].mean():.1f})")
	return h[HISTORY_COLS]


def history_row(email: str, history: pd.DataFrame) -> tuple[int, int, int, bool]:
	"""(played, missed_out, raw recency, known). Newcomers: no plays, no misses, recency at the cap."""
	if email in history.index:
		r = history.loc[email]
		return int(r["played"]), int(r["missed_out"]), int(r["recency"]), True
	return 0, 0, RECENCY_CAP, False


def priority(email: str, history: pd.DataFrame) -> float:
	"""Base score without flexibility. Lower = placed first. Unknown people count as never played."""
	# STEP 2a: score one person from history. Lower score = more deserving of a seat.
	played, missed, recency, _ = history_row(email, history)
	# Recency = number of allocation runs since they last played (0 = played last run).
	# Clamped so one very long absence can't outweigh everything else.
	recency = min(recency, RECENCY_CAP)
	# Plays push you back; misses and time since last play pull you forward.
	return W_PLAYED * played - W_MISSED * missed - W_RECENCY * recency


def debug_history_update(before: pd.DataFrame, after: pd.DataFrame, played: list[str],
						 missed: list[str], write: bool) -> None:
	if not DEBUG:
		return
	touched = set(played) | set(missed)
	new = [e for e in after.index if e not in before.index]
	only_aged = len(before) - len(touched & set(before.index))
	debug(f"history update ({'saved' if write else 'DRY RUN, not saved'}): {len(played)} played, "
		  f"{len(missed)} missed out, {len(new)} new people, {only_aged} others only aged (+1 recency)")
	for e in sorted(touched):
		b = before.loc[e] if e in before.index else None
		a = after.loc[e]
		bs = "NEW" if b is None else f"p={b['played']} m={b['missed_out']} r={b['recency']}"
		debug(f"  {e:36} {bs:>20}  ->  p={a['played']} m={a['missed_out']} r={a['recency']}")


def update_history(played_emails, missed_emails, write: bool = True) -> None:
	"""Record one run: played/missed counts, and age everyone's recency by one run."""
	h = load_history()
	before = h.copy()
	played_emails, missed_emails = list(played_emails), list(missed_emails)
	for e in played_emails + missed_emails:
		if e not in h.index:
			h.loc[e] = [0, 0, RECENCY_CAP]       # newcomer: never played
	h["recency"] += 1                            # every run ages everyone in the history,
	h.loc[played_emails, "recency"] = 0          # ...except those who just played, who reset
	h.loc[played_emails, "played"] += 1
	h.loc[missed_emails, "missed_out"] += 1
	h[HISTORY_COLS] = h[HISTORY_COLS].astype(int)

	debug_history_update(before, h, played_emails, missed_emails, write)
	if write:
		HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
		h.to_csv(HISTORY_PATH)

def fit(groups: list[dict], members: list[int], capacity: dict[str, int], rng: random.Random):
	"""Can these groups all be seated at once (a pair needs 2 seats in one session)?
	Returns {group index: session}, or None if not possible. Session choice is randomised."""
	# STEP 3: feasibility check as a small integer linear program (ILP).
	# One 0/1 variable x[k] per (group, ticked session) option: x[k] = 1 means "put this
	# group in this session". Only options the group actually ticked exist, so nobody is
	# ever placed in a session they did not choose.
	sessions = list(capacity)
	sess_idx = {s: i for i, s in enumerate(sessions)}
	choices = [(gi, s) for gi in members for s in groups[gi]["sessions"] if s in sess_idx]
	pos = {gi: i for i, gi in enumerate(members)}

	# Constraint matrix A: first rows = one per group, remaining rows = one per session.
	A = np.zeros((len(members) + len(sessions), len(choices)))
	for k, (gi, s) in enumerate(choices):
		A[pos[gi], k] = 1                                             # exactly one session per group
		A[len(members) + sess_idx[s], k] = len(groups[gi]["members"])   # seats used in that session
	# Group rows must sum to exactly 1 (placed once). Session rows must be <= capacity
	# (a pair counts 2), so a pair can never be split or overflow a session.
	lower = np.array([1] * len(members) + [0] * len(sessions), dtype=float)
	upper = np.array([1] * len(members) + [capacity[s] for s in sessions], dtype=float)

	# The objective is just random numbers: every feasible answer is valid, and random costs
	# make the solver pick a random one, so flexible people land in a random ticked session.
	res = milp(np.array([rng.random() for _ in choices]),
			   constraints=LinearConstraint(A, lower, upper),
			   integrality=np.ones(len(choices)), bounds=Bounds(0, 1))
	if not res.success:          # infeasible: this set of groups cannot all be seated
		return None
	return {gi: s for (gi, s), x in zip(choices, res.x) if x > 0.5}


def match(groups: list[dict], capacity: dict[str, int], rng: random.Random) -> dict[int, str]:
	"""Places singles and pairs together. Returns {group index: session}.

	Groups are taken in priority order (lowest score first, random noise breaks ties). Each is
	accepted if everyone accepted so far can still be seated, so nobody is bumped by someone
	with a worse score, and groups that don't fit are skipped in favour of ones that do."""
	# STEP 4: priority-ordered acceptance (a greedy pass).
	# Random noise in [0,1) is added to each score. Scores of different history differ by far
	# more than 1, so noise only reorders groups whose scores are effectively tied.
	noise = [rng.random() for _ in groups]
	order = sorted(range(len(groups)), key=lambda gi: groups[gi]["prio"] + noise[gi])
	accepted: list[int] = []
	placed: dict[int, str] = {}
	for rank, gi in enumerate(order, 1):
		# Tentatively add the next-best group and ask whether EVERYONE accepted so far (plus
		# this group) can still be seated. Earlier acceptances are never revoked, so a
		# better-ranked group is never bumped by a worse-ranked one; but the check re-solves
		# the whole assignment, so earlier groups may be moved between their ticked sessions
		# to make room.
		trial = fit(groups, accepted + [gi], capacity, rng)
		g = groups[gi]
		if trial is not None:
			accepted.append(gi)
			placed = trial       # latest feasible full assignment
			debug(f"match #{rank:<3} ACCEPT prio={g['prio']:+7.1f} noise={noise[gi]:.2f} "
				  f"{g['label']} -> {trial[gi]} (options: {', '.join(g['sessions'])})")
		else:
			debug(f"match #{rank:<3} SKIP   prio={g['prio']:+7.1f} noise={noise[gi]:.2f} "
				  f"{g['label']}: no room in any of {', '.join(g['sessions'])} alongside higher-ranked groups")
		# A skipped group (sessions full, or a pair needs 2 seats) doesn't stop the loop: lower-ranked
		# groups are still tried and may fit in leftover seats.
	return placed


def verify_allocation(df: pd.DataFrame, groups: list[dict], assignment: dict[int, str],
					  capacity: dict[str, int]) -> None:
	"""Sanity checks on the matcher's output. Raises if any rule is broken."""
	used = {s: 0 for s in capacity}
	for gi, s in assignment.items():
		for m in groups[gi]["members"]:
			if s not in df.at[m, "_sessions"]:
				raise RuntimeError(f"CHECK FAILED: {df.at[m, '_email']} placed in {s}, which they did not tick")
			used[s] += 1
	for s, n in used.items():
		if n > capacity[s]:
			raise RuntimeError(f"CHECK FAILED: {s} has {n} people but capacity {capacity[s]}")
	placed_people = sum(used.values())
	if placed_people != sum(len(groups[gi]["members"]) for gi in assignment):
		raise RuntimeError("CHECK FAILED: placed-people count mismatch")
	debug(f"checks passed: everyone is in a ticked session, pairs share a session, no session over capacity "
		  f"({placed_people} people placed)")
	for s in capacity:
		debug(f"  {s:24} {used[s]:>3}/{capacity[s]} seats used")


def score_groups(df: pd.DataFrame, groups: list[dict], history: pd.DataFrame) -> None:
	"""STEP 2: set g["prio"] on every group (lower = placed first)."""
	# Group score = the WORST (highest) history score among its members, so a pair is never placed
	# ahead of where its lower-priority partner would be alone (no queue-jumping by pairing up).
	# Then minus a flexibility reward: W_FLEX per extra ticked session (for pairs, sessions both ticked).
	for g in groups:
		members = [priority(df.at[m, "_email"], history) for m in g["members"]]
		g["prio"] = max(members) - W_FLEX * (len(g["sessions"]) - 1)


def build_capacity(sessions: list[str]) -> dict[str, int]:
	"""Seats per session: from CAPACITIES, else DEFAULT_CAPACITY (with a warning)."""
	for s in sessions:
		if s not in CAPACITIES:
			print(f"WARNING: no capacity configured for '{s}'; using DEFAULT_CAPACITY={DEFAULT_CAPACITY}")
	unknown = set(CAPACITIES) - set(sessions)
	if unknown:
		print(f"WARNING: CAPACITY has no matching session for: {', '.join(sorted(unknown))}")
	return {s: CAPACITIES.get(s, DEFAULT_CAPACITY) for s in sessions}


def debug_scores(df: pd.DataFrame, groups: list[dict], history: pd.DataFrame) -> None:
	if not DEBUG:
		return
	debug(f"scoring: score = {W_PLAYED}*played - {W_MISSED}*missed - {W_RECENCY}*min(recency,{RECENCY_CAP}); "
		  f"pair = worst (highest) member score; then - {W_FLEX}*(sessions - 1). Lower is placed first.")
	for rank, g in enumerate(sorted(groups, key=lambda g: g["prio"]), 1):
		parts = []
		for m in g["members"]:
			e = df.at[m, "_email"]
			p, mi, rec, known = history_row(e, history)
			clamp = f"->{RECENCY_CAP}" if rec > RECENCY_CAP else ""
			parts.append(f"{e} [{'known' if known else 'NEW'} played={p} missed={mi} recency={rec}{clamp}] "
						 f"base={priority(e, history):+.0f}")
		debug(f"  #{rank:<3} prio={g['prio']:+7.1f} (flex: {len(g['sessions'])} sessions) | " + " ; ".join(parts))


def debug_demand(groups: list[dict], capacity: dict[str, int]) -> None:
	if not DEBUG:
		return
	for s in capacity:
		wanting = [g for g in groups if s in g["sessions"]]
		debug(f"demand {s:24} capacity={capacity[s]:>3}  people ticking={sum(len(g['members']) for g in wanting):>3} "
			  f"({len(wanting)} groups)")


def debug_beginners(df: pd.DataFrame, groups: list[dict], assignment: dict[int, str],
					capacity: dict[str, int]) -> None:
	"""Per session: how many beginners ticked it, and how many ended up allocated to it."""
	if not DEBUG:
		return
	placed = {m: s for gi, s in assignment.items() for m in groups[gi]["members"]}
	debug(f"beginners: {int(df['_beginner'].sum())} of {len(df)} valid members")
	for s in capacity:
		ticked = df[df["_sessions"].apply(lambda l: s in l)]
		allocated = [m for m, ps in placed.items() if ps == s]
		b_alloc = int(df.loc[allocated, "_beginner"].sum())
		debug(f"beginners {s:24} ticked={int(ticked['_beginner'].sum()):>3}/{len(ticked):<3} "
			  f"allocated={b_alloc:>3}/{len(allocated):<3} (capacity {capacity[s]})")
	b_none = int(df.loc[[i for i in df.index if i not in placed], "_beginner"].sum())
	debug(f"beginners UNALLOCATED: {b_none}")


def allocate(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
	"""Gives every sign-up a session or "UNALLOCATED". Returns (df + "Allocated session", session names)."""
	rng = random.Random(SEED)
	history = load_history()

	groups = build_groups(df)                      # STEP 1
	score_groups(df, groups, history)              # STEP 2
	debug_scores(df, groups, history)

	# Only sessions someone ticked get a seat pool.
	sessions = list(dict.fromkeys(s for g in groups for s in g["sessions"]))
	capacity = build_capacity(sessions)
	debug_demand(groups, capacity)

	assignment = match(groups, capacity, rng)      # STEPS 3-4
	verify_allocation(df, groups, assignment, capacity)
	debug_beginners(df, groups, assignment, capacity)

	# Expand each group's session back to every member (both partners of a pair get the same
	# session). Anyone missing from `placed` becomes UNALLOCATED.
	placed: dict[int, str] = {}
	for gi, s in assignment.items():
		for m in groups[gi]["members"]:
			placed[m] = s

	out = df.copy()
	out["Allocated session"] = out.index.map(placed.get)
	out["Allocated session"] = out["Allocated session"].fillna("UNALLOCATED")
	return out, sessions


def sheet_name(s: str) -> str:
	return s[:31].translate(str.maketrans("/:\\?*[]", "-.-----"))


def backup_state() -> Path:
	"""Create BACKUP_DIR/<timestamp>/ and copy into it the files a commit overwrites (history and workbook).
	To undo a commit, copy them back. The same folder later receives run.log. Returns the folder."""
	existing = [p for p in (HISTORY_PATH, OUTPUTS_PATH) if p.exists()]
	dest = BACKUP_DIR / datetime.now().strftime("%Y-%m-%d_%H%M%S")
	dest.mkdir(parents=True, exist_ok=False)
	if not existing:
		print("Backup: nothing to back up yet (first run).")
		return dest
	for p in existing:
		shutil.copy2(p, dest / p.name)
	print(f"Backup: saved {', '.join(p.name for p in existing)} to {dest}")
	return dest


def write_run_log(run_dir: Path, console_output: str) -> None:
	"""Save the console output of this commit, with a header saying exactly what was run."""
	header = (f"Run at {datetime.now().isoformat(timespec='seconds')}\n"
			  f"Command: {' '.join(sys.argv)}\n"
			  f"Seed: {SEED}\n"
			  + "".join(f"Input {f}\n" for f in USED_FILES) + "\n")
	(run_dir / "run.log").write_text(header + console_output, encoding="utf-8")


def save_results(allocated: pd.DataFrame, sessions: list[str], rejected: pd.DataFrame) -> None:
	OUTPUTS_PATH.parent.mkdir(parents=True, exist_ok=True)
	allocated = allocated.copy()
	allocated["Session (full)"] = allocated["Allocated session"].map(SESSION_FULL)
	cols = [Col.FULL_NAME.value, Col.UCL_EMAIL_ADDR.value, "Allocated session", "Session (full)"]

	with pd.ExcelWriter(OUTPUTS_PATH) as writer:
		allocated[cols].to_excel(writer, sheet_name="All allocated", index=False)
		for s in sessions:
			allocated.loc[allocated["Allocated session"] == s, cols[:2]].to_excel(
				writer, sheet_name=sheet_name(s), index=False)
		rejected.to_excel(writer, sheet_name="Rejected", index=False)
	print(f"\nWrote {OUTPUTS_PATH}")


def print_rejected(rejected: pd.DataFrame) -> None:
	"""List every rejected sign-up (email and name), grouped by reason."""
	print("\nRejected sign-ups by reason:")
	for reason, group in rejected.groupby("Reason", sort=False):
		print(f"\n  {reason} ({len(group)}):")
		for _, r in group.iterrows():
			text = lambda col: "" if pd.isna(r.get(col)) else str(r.get(col)).strip()
			email = text(Col.UCL_EMAIL_ADDR.value) or "(no UCL email)"
			name = text(Col.FULL_NAME.value)
			print(f"    {email:42} {name}")


def main() -> None:
	global RUN_DIR
	raw = load_file(latest_signups_file())
	signups, rejected = clean_signups(raw)
	debug(f"signups: {len(raw)} rows -> {len(signups)} valid after cleaning "
		  f"({sum(len(r) for r in rejected)} rejected so far); seed={SEED}")
	preview(signups, 10, "Sign-ups")

	print("\nSessions found:")
	for short, full in SESSION_FULL.items():
		print(f"  {short:32} <- {full}")

	if CHECK_MEMBERSHIP:
		signups, not_members = split_members(signups, load_file(latest_membership_file()))
		debug(f"membership: {len(signups)} members, {len(not_members)} not on the list")
		if len(not_members):
			rejected.append(tag(not_members, "Not on membership list"))

	result, sessions = allocate(signups)
	is_unallocated = result["Allocated session"] == "UNALLOCATED"
	if is_unallocated.any():
		rejected.append(tag(result[is_unallocated], "All selected sessions full"))
	allocated = result[~is_unallocated]

	rejected_df = pd.concat(rejected, ignore_index=True) if rejected else pd.DataFrame()

	print(f"\n{len(raw)} rows in file -> {len(allocated)} allocated, {len(rejected_df)} rejected")
	print("\nAllocated per session:")
	print(allocated["Allocated session"].value_counts().to_string())
	if len(rejected_df):
		print_rejected(rejected_df)

	if COMMIT:
		RUN_DIR = backup_state()   # before anything is overwritten; an error here aborts the commit
		save_results(allocated, sessions, rejected_df)
		update_history(allocated["_email"], result.loc[is_unallocated, "_email"])
		print("History updated.")
	else:
		if DEBUG:   # show what a commit would change, without saving
			update_history(allocated["_email"], result.loc[is_unallocated, "_email"], write=False)
		print(f"\nDRY RUN: nothing saved (no {OUTPUTS_PATH}, history unchanged). Re-run with --commit to save.")


if __name__ == "__main__":
	console, log = sys.stdout, io.StringIO()
	sys.stdout = Tee(console, log)
	try:
		main()
	except Exception as e:
		log.write(f"\nRUN FAILED: {e!r}\n")
		raise
	finally:
		sys.stdout = console
		if RUN_DIR is not None:   # only commits keep a log; dry runs write nothing
			write_run_log(RUN_DIR, log.getvalue())
			print(f"Log saved to {RUN_DIR / 'run.log'}")
