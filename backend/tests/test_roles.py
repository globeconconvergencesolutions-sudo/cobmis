import psycopg
import pytest

from app.db import roles
from app.db.roles import DatabaseSetupError, RoleSpec, connect_owner, ensure_roles, explain_connect_error


@pytest.mark.parametrize(
    "role,message,expected",
    [
        ("cob_app", 'FATAL:  role "cob_app" does not exist', "Run `python -m scripts.migrate`"),
        ("cob_ingest", 'FATAL:  password authentication failed for user "cob_ingest"', "COB_INGEST_PASSWORD"),
        ("cob_owner", 'FATAL:  role "cob_owner" does not exist', "POSTGRES_SUPERUSER"),
        ("cob_app", "connection failed: Connection refused", "docker compose up -d"),
    ],
)
def test_connect_errors_are_explained(role, message, expected):
    text = explain_connect_error(role, psycopg.OperationalError(message))
    assert expected in text and "Traceback" not in text


def test_missing_password_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("COB_INGEST_PASSWORD", "")
    with pytest.raises(DatabaseSetupError, match="COB_INGEST_PASSWORD is not set"):
        roles.role_conninfo("cob_ingest")


def test_env_reads_dotenv_with_crlf(tmp_path, monkeypatch):
    (tmp_path / ".env").write_bytes(b"COB_APP_PASSWORD=s3cret\r\nOTHER=x\r\n")
    monkeypatch.setattr(roles, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("COB_APP_PASSWORD", raising=False)
    assert roles.env("COB_APP_PASSWORD") == "s3cret"


@pytest.fixture
def owner():
    try:
        conn = connect_owner(autocommit=True)
    except DatabaseSetupError as exc:
        pytest.skip(f"Postgres not reachable: {exc.code}")
    yield conn
    conn.close()


def test_ensure_roles_creates_then_updates(owner, monkeypatch):
    """The same code path as the real roles, on a throwaway role name."""
    spec = RoleSpec("cob_test_tmp_ro", "COB_TEST_TMP_PASSWORD", "test", (("statement_timeout", "10s"),
                    ("default_transaction_read_only", "on")))
    monkeypatch.setattr(roles, "ROLES", (spec,))
    monkeypatch.setitem(roles.BY_NAME, spec.name, spec)
    owner.execute("DROP ROLE IF EXISTS cob_test_tmp_ro")
    try:
        monkeypatch.setenv("COB_TEST_TMP_PASSWORD", "first-pw")
        assert ensure_roles(owner) == ["cob_test_tmp_ro"]
        with roles.connect("cob_test_tmp_ro", autocommit=True) as c:
            assert c.execute("SHOW statement_timeout").fetchone()[0] == "10s"
            assert c.execute("SHOW default_transaction_read_only").fetchone()[0] == "on"

        monkeypatch.setenv("COB_TEST_TMP_PASSWORD", "second-pw")
        assert ensure_roles(owner) == [], "existing role: updated, not created"
        roles.connect("cob_test_tmp_ro").close()  # new password works
        monkeypatch.setenv("COB_TEST_TMP_PASSWORD", "first-pw")
        with pytest.raises(DatabaseSetupError, match="was rejected"):
            roles.connect("cob_test_tmp_ro")
        attrs = owner.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin FROM pg_roles WHERE rolname = 'cob_test_tmp_ro'"
        ).fetchone()
        assert attrs == (False, False, False, True)
    finally:
        owner.execute("DROP ROLE IF EXISTS cob_test_tmp_ro")


def test_missing_role_gives_clear_message(owner):
    owner.execute("DROP ROLE IF EXISTS cob_test_absent")
    spec = RoleSpec("cob_test_absent", "COB_APP_PASSWORD", "test")
    roles.BY_NAME[spec.name] = spec
    try:
        with pytest.raises(DatabaseSetupError, match="does not exist. Run `python -m scripts.migrate`"):
            roles.connect("cob_test_absent")
    finally:
        del roles.BY_NAME[spec.name]
