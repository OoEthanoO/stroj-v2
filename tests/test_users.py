"""The public user directory includes accounts regardless of submission history."""

from stroj import db, rating, scoring


def test_new_account_is_listed_publicly_without_private_fields(client):
    response = client.post("/api/auth/register", json={
        "username": "new-user", "password": "password123",
        "email": "new-user@example.test",
    })
    response.raise_for_status()
    user_id = response.json()["user"]["id"]
    client.cookies.clear()

    response = client.get("/api/users")
    response.raise_for_status()
    body = response.json()
    assert body["decay"] == scoring.DECAY
    users = {user["username"]: user for user in body["users"]}
    assert set(users) == {"admin", "new-user"}
    assert users["admin"]["role"] == "admin"
    # An exact comparison also guards against exposing email or password data.
    assert users["new-user"] == {
        "user_id": user_id, "username": "new-user", "role": "user",
        "rank": None, "score": 0, "solved": 0, "hardest": 0,
        "rating": rating.START_RATING, "rating_rank": None,
    }


def test_all_submission_histories_appear_with_only_public_credit(client):
    users = {
        name: db.insert(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, 'x', ?)",
            (name, db.utcnow()),
        )
        for name in ("wrong", "hidden", "partial", "solver", "tied")
    }
    problems = {
        name: db.insert(
            "INSERT INTO problems (slug, title, points, visible, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (name, name, points, visible, db.utcnow()),
        )
        for name, points, visible in (("public", 100, 1), ("secret", 900, 0))
    }
    for name, problem, earned in (
        ("wrong", "public", 0), ("hidden", "secret", 100),
        ("partial", "public", 25), ("solver", "public", 100),
        ("tied", "public", 100),
    ):
        db.insert(
            "INSERT INTO submissions"
            " (user_id, problem_id, language, source, verdict, earned_percent, created_at)"
            " VALUES (?, ?, 'cpp', '', ?, ?, ?)",
            (users[name], problems[problem], "AC" if earned == 100 else "WA",
             earned, db.utcnow()),
        )
    db.execute("UPDATE users SET rating = 1375, rated_contests = 2 WHERE id = ?",
               (users["hidden"],))

    response = client.get("/api/users")
    response.raise_for_status()
    listed = {user["username"]: user for user in response.json()["users"]}
    assert set(listed) == {"admin", *users}
    for name in ("admin", "wrong", "hidden"):
        assert listed[name]["rank"] is None
        assert [listed[name][key] for key in ("score", "solved", "hardest")] == [0, 0, 0]
    assert listed["hidden"]["rating_rank"] == rating.rank_dict(1375, 2)
    assert listed["hidden"]["rating"] == 1375
    assert listed["partial"]["score"] == 25
    assert listed["partial"]["solved"] == 0
    assert listed["partial"]["rank"] == 3
    assert listed["solver"]["rank"] == listed["tied"]["rank"] == 1
    assert listed["solver"]["solved"] == 1
    for standing in client.get("/api/leaderboard").json()["standings"]:
        assert listed[standing["username"]] == standing
    assert client.get("/api/users/hidden").json()["rank"] is None


def test_directory_is_not_limited_to_the_top_100_or_500(client):
    names = {f"member-{i:03}" for i in range(501)}
    with db.transaction() as conn:
        conn.executemany(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, 'x', ?)",
            [(name, db.utcnow()) for name in names],
        )
    response = client.get("/api/users")
    response.raise_for_status()
    users = response.json()["users"]
    assert len(users) == 502
    assert {user["username"] for user in users} == names | {"admin"}
