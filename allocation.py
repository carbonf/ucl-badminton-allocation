from pathlib import Path
import os
import random
import re
import pandas as pd
from dotenv import load_dotenv
import sys
import numpy as np
from scipy.optimize import linear_sum_assignment

load_dotenv()

from enum import Enum

# Configuration
MEMBERSHIPS_PATH = Path("data/Club-society membership list - DO NOT SHARE - 2026-10-12T17_44_07+0100.csv")
SIGNUPS_PATH = Path("data/UCL Badminton Social Session Signups (Week 1)(1-114).xlsx")
OUTPUTS_PATH = Path("outputs/allocations.xlsx")
HISTORY_PATH = Path("data/history.csv")
BIG = 1e6   # cost of a seat the person didn't tick; must dwarf every real cost
CHECK_MEMBERSHIP = True
SEED = 69
DEFAULT_CAPACITY = 18
CAPACITIES = {
	"Tue 13 Oct 6:00pm": 15,
	"Sat 17 Oct 1:30pm": 15,
	"Sat 17 Oct 3:30pm": 15,
	"Sun 18 Oct 10:30am": 21,
	"Sun 18 Oct 11:45am": 21,
}

class Col(str, Enum):
	# The values MUST BE THE EXACT QUESTION TEXT. Corresponds to columns in the sheet SIGNUPS_PATH
	NAME = "Name"
	EMAIL = "Email"
	FULL_NAME = "Full name"
	UCL_EMAIL_ADDR = "UCL email address"
	SESSIONS = "Please select the sessions you wish to sign up for:"
	PAIRING = "Do you have a partner you would like to play together with?"
	PARTNER_NAME = "Partner's full name"
	PARTNER_EMAIL = "Partner's UCL email"

# ---------- Session short names ----------
SESSION_RE = re.compile(r"(\d{1,2} \w{3}) \((\w{3})\) (\d{1,2}:\d{2}[ap]m)")
SESSION_FULL: dict[str, str] = {}   # short name -> full form text


def shorten_name(full: str) -> str:
	# Takes a full session name and shortens it.

	m = SESSION_RE.search(full)
	if not m:
		shortened = full
	else:
		date, day, start = m.groups()
		shortened = f"{day} {date} {start}"
	
	SESSION_FULL[shortened] = full
	return shortened


def parse_sessions(cell) -> list[str]:
	# Used to parse chosen sessions for each entry
	
	if pd.isna(cell):
		return []
	return [shorten_name(s.strip()) for s in str(cell).split(";") if s.strip()]

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
	cols = [c.value for c in Col if c.value in df.columns]
	n = min(num_rows, len(df))
	print(f"\n{label} - showing {n} of {len(df)} rows")
	print(df[cols].head(n).to_string(index=False))

def remove_duplicates(df: pd.DataFrame) -> pd.DataFrame:
	# Removes duplicates.
	email_col = None
	if Col.EMAIL.value in df.columns:
		email_col = Col.EMAIL.value
	else:
		raise KeyError("Missing email column!")
		
	initial_count = len(df)
	df = df.drop_duplicates(subset=[email_col], keep="last")
	removed_count = initial_count - len(df)
	print(f"Removed {removed_count} duplicate entries based on {email_col}.")
	
	return df

def check_columns(df: pd.DataFrame, cols: list[str], label: str) -> None:
	missing = [c for c in cols if c not in df.columns]
	if missing:
		raise KeyError(f"{label} is missing columns: {', '.join(missing)}")

def tag(df: pd.DataFrame, reason: str) -> pd.DataFrame:
	"""Reduce rows to the columns worth showing in the Rejected sheet, plus a reason."""
	out = df.copy()
	out["Sessions requested"] = out["_sessions"].apply(lambda lst: "; ".join(lst))
	out["Reason"] = reason
	cols = [Col.NAME.value, Col.EMAIL.value, Col.FULL_NAME.value,
			Col.UCL_EMAIL_ADDR.value, "Sessions requested", "Reason"]
	return out[[c for c in cols if c in out.columns]]

def clean_signups(df: pd.DataFrame):
	"""Returns (valid sign-ups, list of rejected DataFrames)."""
	check_columns(df, [Col.NAME.value, Col.EMAIL.value, Col.UCL_EMAIL_ADDR.value,
					   Col.SESSIONS.value], "Sign-ups")
	df = df.copy()
	rejected: list[pd.DataFrame] = []

	df["_email"] = df[Col.UCL_EMAIL_ADDR.value].fillna("").astype(str).str.strip().str.lower()
	df["_sessions"] = df[Col.SESSIONS.value].apply(parse_sessions)
	df["_partner"] = (
		df[Col.PARTNER_EMAIL.value].fillna("").astype(str).str.strip().str.lower()
		if Col.PARTNER_EMAIL.value in df.columns else ""
	)

	blank = df["_email"] == ""
	if blank.any():
		rejected.append(tag(df[blank], "No UCL email given"))
	df = df[~blank]

	if "Completion time" in df.columns:
		df = df.sort_values("Completion time")
	dup = df.duplicated(subset="_email", keep="last")
	if dup.any():
		rejected.append(tag(df[dup], "Superseded by a later submission"))
	df = df[~dup]

	none_ticked = df["_sessions"].apply(len) == 0
	if none_ticked.any():
		rejected.append(tag(df[none_ticked], "No sessions selected"))
	df = df[~none_ticked]

	return df.reset_index(drop=True), rejected

def split_members(signups: pd.DataFrame, members: pd.DataFrame):
	"""Checks if people in signups are club members. Filters signups to retain only members. Returns df of nonmembers too"""
	
	check_columns(members, [MEMBERSHIP_EMAIL_COL], "Membership list")
	# Create set of member emails from df
	member_emails = set(members[MEMBERSHIP_EMAIL_COL].astype(str).str.strip().str.lower())
	# Use boolean mask to filter member rows
	is_member = signups["_email"].isin(member_emails)
	return signups[is_member].reset_index(drop=True), signups[~is_member]


def build_groups(df: pd.DataFrame) -> list[dict]:
	"""Each group is one player, or two partners who must share a session."""
	idx_by_email = {e: i for i, e in df["_email"].items()}
	used, groups = set(), []

	for i, row in df.iterrows():
		if i in used:
			continue
		j = idx_by_email.get(row["_partner"])
		if j is not None and j != i and j not in used:
			common = [s for s in row["_sessions"] if s in df.at[j, "_sessions"]]
			if common:
				groups.append({"members": [i, j], "sessions": common})
				used.update({i, j})
				continue
			print(f"WARNING: {row['_email']} and partner have no common session; treated separately.")
		groups.append({"members": [i], "sessions": row["_sessions"]})
		used.add(i)
	return groups

def load_history() -> pd.DataFrame:
	"""One row per person: how often they've played / been turned away."""
	if HISTORY_PATH.exists():
		h = pd.read_csv(HISTORY_PATH)
		h["ucl_email"] = h["ucl_email"].str.strip().str.lower()
		return h.set_index("ucl_email")[["played", "missed_out"]].astype(int)
	return pd.DataFrame(columns=["played", "missed_out"]).astype(int).rename_axis("ucl_email")


def priority(email: str, history: pd.DataFrame) -> float:
	"""Lower = placed first. Unknown people count as zero."""
	if email in history.index:
		r = history.loc[email]
		return r["played"] * 100 - r["missed_out"] * 10
	return 0.0


def update_history(played_emails, missed_emails) -> None:
	h = load_history()
	for e in list(played_emails) + list(missed_emails):
		if e not in h.index:
			h.loc[e] = [0, 0]
	h.loc[list(played_emails), "played"] += 1
	h.loc[list(missed_emails), "missed_out"] += 1
	HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
	h.to_csv(HISTORY_PATH)

def match(units: list[dict], seats: dict[str, int], rng: random.Random) -> dict[int, str]:
	"""Optimal assignment of units to seats. Returns {unit index: session}."""
	columns = [s for s, n in seats.items() for _ in range(n)]   # one column per seat
	if not units or not columns:
		return {}
	cost = np.full((len(units), len(columns)), BIG)
	for r, u in enumerate(units):
		base = u["prio"] + rng.random()            # per-person noise = random tie-break
		for c, s in enumerate(columns):
			if s in u["sessions"]:
				cost[r, c] = base + rng.random() * 1e-4   # tiny per-seat noise spreads flexible people
	rows, cols = linear_sum_assignment(cost)
	return {r: columns[c] for r, c in zip(rows, cols) if cost[r, c] < BIG}


def random_allocation(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
	rng = random.Random(SEED)
	history = load_history()
	groups = build_groups(df)
	for g in groups:                               # a pair takes its worse member's priority
		g["prio"] = max(priority(df.at[m, "_email"], history) for m in g["members"])

	sessions = list(dict.fromkeys(s for g in groups for s in g["sessions"]))
	capacity = {s: CAPACITIES.get(s, DEFAULT_CAPACITY) for s in sessions}
	unknown = set(CAPACITIES) - set(sessions)
	if unknown:
		print(f"WARNING: CAPACITY has no matching session for: {', '.join(sorted(unknown))}")

	placed: dict[int, str] = {}

	# Pass 1: pairs. Each session offers capacity//2 "double seats".
	pairs = [g for g in groups if len(g["members"]) == 2]
	for r, s in match(pairs, {s: capacity[s] // 2 for s in sessions}, rng).items():
		for m in pairs[r]["members"]:
			placed[m] = s
		capacity[s] -= 2

	# Pass 2: singles fill whatever seats are left.
	singles = [g for g in groups if len(g["members"]) == 1]
	for r, s in match(singles, capacity, rng).items():
		placed[singles[r]["members"][0]] = s

	out = df.copy()
	out["Allocated session"] = out.index.map(placed.get)
	out["Allocated session"] = out["Allocated session"].fillna("UNALLOCATED")
	return out, sessions


def sheet_name(s: str) -> str:
	return s[:31].translate(str.maketrans("/:\\?*[]", "-.-----"))


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

def random_allocation_simple(df: pd.DataFrame, capacity: int) -> list:
	# Randomly picks n players based on pandas sample() method.
	n = min(capacity, len(df))
	sampled_df = df.sample(n=n)
	
	# Keep only the columns specified in the Col Enum (that exist in the df)
	keep_cols = [col.value for col in Col if col.value in df.columns]
	sampled_df = sampled_df[keep_cols]
	
	# Convert the randomly sampled rows to a list of dictionaries
	return sampled_df.to_dict(orient="records")

def print_results(allocated_list: list) -> None:
	print(f"\n--- Allocated Players ({len(allocated_list)}) ---")
	for i, player in enumerate(allocated_list, start=1):
		# Try to find the name key (could be Name or Full name depending on file)
		name = player.get(Col.NAME.value) or player.get(Col.FULL_NAME.value) or "No Name Found"
		print(f"{i}. {name}")


def main() -> None:
	raw = load_file(SIGNUPS_PATH)
	signups, rejected = clean_signups(raw)
	preview(signups, 10, "Sign-ups")

	print("\nSessions found:")
	for short, full in SESSION_FULL.items():
		print(f"  {short:32} <- {full}")

	if CHECK_MEMBERSHIP:
		signups, not_members = split_members(signups, load_file(MEMBERSHIPS_PATH))
		if len(not_members):
			rejected.append(tag(not_members, "Not on membership list"))

	result, sessions = random_allocation(signups)
	is_unallocated = result["Allocated session"] == "UNALLOCATED"
	if is_unallocated.any():
		rejected.append(tag(result[is_unallocated], "All selected sessions full"))
	allocated = result[~is_unallocated]

	rejected_df = pd.concat(rejected, ignore_index=True) if rejected else pd.DataFrame()

	print(f"\n{len(raw)} rows in file -> {len(allocated)} allocated, {len(rejected_df)} rejected")
	print("\nAllocated per session:")
	print(allocated["Allocated session"].value_counts().to_string())
	if len(rejected_df):
		print("\nRejected by reason:")
		print(rejected_df["Reason"].value_counts().to_string())

	save_results(allocated, sessions, rejected_df)
	if "--commit" in sys.argv:
		update_history(allocated["_email"], result.loc[is_unallocated, "_email"])
		print("History updated.")
	else:
		print("Dry run: history not updated. Re-run with --commit to record this week.")


if __name__ == "__main__":
	main()
