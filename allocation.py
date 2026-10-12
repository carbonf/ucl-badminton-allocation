from pathlib import Path
import os
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from enum import Enum

# Configuration
MEMBERSHIP_PATH = Path("data/Club-society membership list - DO NOT SHARE - 2025-12-08T00_05_29+0000.csv")
SIGNUP_PATH = Path("data/UCL Badminton Social Session Signups (5 Jun 2026)(1-16).xlsx")
OUTPUT_PATH = Path("outputs/allocations.xlsx")
CHECK_MEMBERSHIP = True
AVAILABLE_SLOTS = 18
MEMBERSHIP_EMAIL_COL = "Email"

class Col(str, Enum):
	# The values MUST BE THE EXACT QUESTION TEXT
	NAME = "Name"
	EMAIL = "Email"
	FULL_NAME = "Full name"
	UCL_EMAIL_ADDR = "UCL email address"
	SESSIONS = "Which sessions are you available for?"
	PAIRING = "Do you have a partner you would like to play together with?"
	PARTNER_NAME = "Partner's full name"
	PARTNER_EMAIL = "Partner's UCL email"



def load_file(path: Path) -> pd.DataFrame:
	# Dynamically load file into pandas dataframe based on extension.
	
	if not path.exists():
		raise FileNotFoundError(f"Missing file: {path.resolve()}")

	if path.suffix.lower() in {".xlsx", ".xls"}:
		return pd.read_excel(path)
	elif path.suffix.lower() == ".csv":
		return pd.read_csv(path)

	raise ValueError(f"Unsupported file type: {path.suffix}")

def preview(df: pd.DataFrame, num_rows: int, label: str) -> None:
	# Preview specified columns of the file defined in class Col. 

	total = len(df)
	num_rows = min(num_rows, total)
	print(f"\n{label} - Showing {num_rows} rows out of {total}")
	
	# Check that queried columns actually exist
	missing = [col.value for col in Col if col.value not in df.columns]
	if missing:
		raise KeyError(f"{label} is missing columns: {', '.join(missing)}")

	preview_cols = [col.value for col in Col]
	print(df[preview_cols].head(num_rows).to_string(index=False))

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

def clean_signups(df: pd.DataFrame) -> pd.DataFrame:
	check_columns(df, [Col.NAME, Col.EMAIL, Col.SESSIONS], "Sign-ups")
	df = df.copy()
	df["_email"] = df[Col.EMAIL.value].astype(str).str.strip().str.lower()
	if "Completion time" in df.columns:          # Forms adds this; keep latest submission
		df = df.sort_values("Completion time")
	before = len(df)
	df = df.drop_duplicates(subset="_email", keep="last").reset_index(drop=True)
	print(f"Removed {before - len(df)} duplicate entries.")

	df["_sessions"] = df[Col.SESSIONS.value].apply(
		lambda c: [] if pd.isna(c) else [s.strip() for s in str(c).split(";") if s.strip()]
	)
	df["_partner"] = (
		df[Col.PARTNER_EMAIL.value].fillna("").astype(str).str.strip().str.lower()
		if Col.PARTNER_EMAIL.value in df.columns else ""
	)
	return df

def split_members(signups: pd.DataFrame, members: pd.DataFrame):
	"""Checks if people in signups are club members. Filters signups to retain only members. Returns df of nonmembers too"""
	
	check_columns(members, [MEMBERSHIP_EMAIL_COL], "Membership list")
	# Create set of member emails from df
	member_emails = set(members[MEMBERSHIP_EMAIL_COL].astype(str).str.strip().str.lower())
	# Use boolean mask to filter member rows
	is_member = signups["_email"].isin(member_emails)
	return signups[is_member].reset_index(drop=True), signups[~is_member]



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
	# Load signups as df and sanity check preview
	signups = clean_signups(load_file(SIGNUP_PATH))
	preview(signups, 10, "Sign-ups")

	# Create empty df and check memberships for all
	not_members = signups.iloc[0:0]
	if CHECK_MEMBERSHIP:
		signups, not_members = split_members(signups, load_file(MEMBERSHIP_PATH))
		print(f"{len(not_members)} sign-ups not found on membership list.")

	result, sessions = random_allocation(signups)

	print("\n" + result["Allocated session"].value_counts().to_string())
	save_results(result, sessions, not_members)



if __name__ == "__main__":
	main()
