import hashlib
import json
from pathlib import Path


def test_allocations():
    json_path = Path("docs/allocations.json")
    assert json_path.exists(), "docs/allocations.json does not exist"

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    allocations = data["allocations"]

    def get_user(email: str):
        h = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()
        return allocations.get(h)

    # 1. Test Allocated
    allocated_user = get_user("demo.allocated@ucl.ac.uk")
    assert allocated_user is not None, "Allocated user should be found"
    assert allocated_user["status"] == "allocated"
    assert allocated_user["first_name"] == "Alex"
    assert "Sun 11:45am" in allocated_user["session_short"]
    assert "Maria Fidelis" in allocated_user["venue"]
    assert "priority" in allocated_user, "Priority data should be present"
    assert "score" in allocated_user["priority"]
    print("✅ Test Allocated User with Priority Stats: PASSED")

    # 2. Test Waitlisted
    waitlisted_user = get_user("demo.waitlisted@ucl.ac.uk")
    assert waitlisted_user is not None, "Waitlisted user should be found"
    assert waitlisted_user["status"] == "waitlisted"
    assert "full" in waitlisted_user["reason"].lower()
    assert "+10 priority points" in waitlisted_user["tip"].lower()
    assert "priority" in waitlisted_user, "Priority data should be present"
    print("✅ Test Waitlisted User / Priority Bonus: PASSED")

    # 3. Test Membership Issue
    nomember_user = get_user("demo.nomember@ucl.ac.uk")
    assert nomember_user is not None, "Non-member user should be found"
    assert nomember_user["status"] == "membership_issue"
    print("✅ Test Membership Issue: PASSED")

    # 4. Test Not Found
    random_user = get_user("nonexistent.user.00@ucl.ac.uk")
    assert random_user is None, "Nonexistent user should be None"
    print("✅ Test Nonexistent User (Not Found): PASSED")

    print("\n🎉 ALL TESTS PASSED! Web export and hashing are 100% verified.")


if __name__ == "__main__":
    test_allocations()
