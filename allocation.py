from pathlib import Path
import os
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from enum import Enum

# Configuration
MEMBERSHIP_PATH = Path("data/Club-society membership list - DO NOT SHARE - 2025-12-08T00_05_29+0000.csv")
SIGNUP_PATH = Path("data/UCL Badminton Social Session Signups (5 Jun 2026)(1-16).xlsx")
AVAILABLE_SLOTS = 18

class Col(str, Enum):
	NAME = "Name"
	EMAIL = "Email"
	FULL_NAME = "Full name"
	UCL_EMAIL_ADDR = "UCL email address"




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

def random_allocation(df: pd.DataFrame, capacity: int) -> list:
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
	signups = load_file(SIGNUP_PATH)
	preview(signups, 10, "def.csv")
	
	signups = remove_duplicates(signups)
	
	allocated = random_allocation(signups, AVAILABLE_SLOTS)
	print_results(allocated)



if __name__ == "__main__":
	main()
