import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd


def hash_email(email: str) -> str:
    """Normalize and hash an email with SHA-256 for privacy-preserving lookup."""
    normalized = str(email).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def extract_first_name(full_name: str) -> str:
    """Extract a friendly first name or fallback gracefully."""
    if not full_name or pd.isna(full_name):
        return "Member"
    cleaned = str(full_name).strip()
    m = re.match(r"^([^(\uff08]+)", cleaned)
    if m:
        cleaned = m.group(1).strip()
    parts = cleaned.split()
    return parts[0].title() if parts else "Member"


def parse_session_details(session_full: str) -> dict:
    """
    Parse strings like:
    'Sun 11:45am - 1:00pm @ Maria Fidelis Catholic School'
    or 'Sat 1:30pm - 3:30pm @ Somers Town Community Sports Centre'
    """
    if not session_full or pd.isna(session_full):
        return {"time": "", "venue": ""}
    s = str(session_full).strip()
    if "@" in s:
        parts = s.split("@", 1)
        time_part = parts[0].strip()
        venue_part = parts[1].strip()
        return {"time": time_part, "venue": venue_part}
    return {"time": s, "venue": ""}


def compute_priority_info(
    email: str,
    history_df: Optional[pd.DataFrame] = None,
) -> dict:
    """
    Computes transparent priority metrics according to UCL Badminton's algorithm:
    score = 20*played - 10*missed_out - 15*min(recency, 8)
    (Lower score = placed earlier)
    """
    W_PLAYED = 20
    W_MISSED = 10
    W_RECENCY = 15
    RECENCY_CAP = 8

    played, missed, recency = 0, 0, RECENCY_CAP
    clean_email = str(email).strip().lower()

    if history_df is not None and not history_df.empty:
        matched = None
        if "ucl_email" in history_df.columns:
            matches = history_df[history_df["ucl_email"].astype(str).str.strip().str.lower() == clean_email]
            if not matches.empty:
                matched = matches.iloc[0]
        elif clean_email in [str(idx).strip().lower() for idx in history_df.index]:
            for idx in history_df.index:
                if str(idx).strip().lower() == clean_email:
                    matched = history_df.loc[idx]
                    break

        if matched is not None:
            try:
                played = int(matched.get("played", 0))
                missed = int(matched.get("missed_out", 0))
                recency = int(matched.get("recency", RECENCY_CAP))
            except Exception:
                pass

    score = W_PLAYED * played - W_MISSED * missed - W_RECENCY * min(recency, RECENCY_CAP)

    if score <= -100:
        tier = "🌟 Top Priority"
        tier_class = "tier-vhigh"
        desc = "Newcomer / haven't played in recent weeks"
    elif score <= -40:
        tier = "✨ High Priority"
        tier_class = "tier-high"
        desc = "Missed out previously or returning player"
    elif score <= 0:
        tier = "⚖️ Medium Priority"
        tier_class = "tier-med"
        desc = "Balanced recent attendance"
    else:
        tier = "🏸 Active Player"
        tier_class = "tier-low"
        desc = "Played very recently or multiple times"

    return {
        "score": score,
        "played": played,
        "missed_out": missed,
        "recency": recency,
        "tier": tier,
        "tier_class": tier_class,
        "desc": desc,
    }


def load_optional_history() -> Optional[pd.DataFrame]:
    """Attempt to load data/history.csv if present."""
    p = Path("data/history.csv")
    if p.exists():
        try:
            return pd.read_csv(p, index_col=0)
        except Exception:
            return None
    return None


def build_web_payload(
    allocated_df: pd.DataFrame,
    rejected_df: Optional[pd.DataFrame] = None,
    history_df: Optional[pd.DataFrame] = None,
    week_label: str = "This Week",
) -> dict:
    """
    Builds the JSON payload with hashed email keys, anonymized status, and priority points.
    """
    if history_df is None:
        history_df = load_optional_history()

    records = {}
    sessions_found = {}

    # Process allocated
    if not allocated_df.empty:
        for _, row in allocated_df.iterrows():
            email = row.get("UCL email address") or row.get("Email")
            if not email or pd.isna(email):
                continue

            clean_email = str(email).strip().lower()
            h = hash_email(clean_email)
            full_name = row.get("Full name") or row.get("Name") or ""
            first_name = extract_first_name(full_name)
            short_session = str(row.get("Allocated session", "")).strip()
            full_session = str(row.get("Session (full)", short_session)).strip()
            details = parse_session_details(full_session)

            if short_session and short_session not in sessions_found:
                sessions_found[short_session] = {
                    "short": short_session,
                    "full": full_session,
                    "time": details["time"],
                    "venue": details["venue"],
                }

            priority_data = compute_priority_info(clean_email, history_df)

            records[h] = {
                "status": "allocated",
                "first_name": first_name,
                "session_short": short_session,
                "session_full": full_session,
                "time": details["time"] or short_session,
                "venue": details["venue"] or "See society schedule",
                "priority": priority_data,
                "reason": None,
                "message": f"You are confirmed for {short_session}!",
            }

    # Process rejected / waitlisted
    if rejected_df is not None and not rejected_df.empty:
        for _, row in rejected_df.iterrows():
            email = row.get("UCL email address") or row.get("Email")
            if not email or pd.isna(email):
                continue

            clean_email = str(email).strip().lower()
            h = hash_email(clean_email)
            if h in records:
                continue

            full_name = row.get("Full name") or row.get("Name") or ""
            first_name = extract_first_name(full_name)
            reason = str(row.get("Reason", "Waitlisted")).strip()
            priority_data = compute_priority_info(clean_email, history_df)

            if "membership" in reason.lower():
                status = "membership_issue"
                message = "Not found on the active Students' Union membership list."
                tip = "Ensure you have purchased a UCL Badminton membership and used the exact email on your Students' Union account."
            else:
                status = "waitlisted"
                message = "All selected sessions were full."
                tip = "You have received +10 priority points towards next week's allocation!"

            records[h] = {
                "status": status,
                "first_name": first_name,
                "priority": priority_data,
                "reason": reason,
                "message": message,
                "tip": tip,
            }

    payload = {
        "week": week_label,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_allocated": len([r for r in records.values() if r["status"] == "allocated"]),
        "total_entries": len(records),
        "sessions": list(sessions_found.values()),
        "allocations": records,
    }
    return payload


def export_allocations_to_json(
    excel_path: Path,
    output_json_path: Path = Path("docs/allocations.json"),
    history_df: Optional[pd.DataFrame] = None,
    week_label: Optional[str] = None,
) -> Path:
    """Reads an Allocations workbook and exports docs/allocations.json."""
    if not excel_path.exists():
        raise FileNotFoundError(f"Workbook not found: {excel_path}")

    if week_label is None:
        m = re.search(r"Week\s*(\d+)", excel_path.name, re.IGNORECASE)
        week_label = f"Week {m.group(1)}" if m else "Current Social"

    allocated_df = pd.read_excel(excel_path, sheet_name="All allocated")
    rejected_df = None
    try:
        rejected_df = pd.read_excel(excel_path, sheet_name="Rejected")
    except Exception:
        pass

    payload = build_web_payload(
        allocated_df,
        rejected_df,
        history_df=history_df,
        week_label=week_label,
    )

    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"Exported web allocations to {output_json_path} ({payload['total_entries']} members hashed)")
    return output_json_path


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    target_excel = None
    target_out = Path("docs/allocations.json")

    if args:
        target_excel = Path(args[0])
    else:
        candidates = sorted(Path("outputs").glob("Allocations*.xlsx"))
        if candidates:
            target_excel = candidates[-1]

    if not target_excel or not target_excel.exists():
        print("Usage: python web_export.py <path-to-Allocations.xlsx> [output-json-path]")
        sys.exit(1)

    if len(args) > 1:
        target_out = Path(args[1])

    export_allocations_to_json(target_excel, target_out)
