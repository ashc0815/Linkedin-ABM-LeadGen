"""Tests for pipeline import-existing command."""

import csv
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from src.cli import _is_finance_position

runner = CliRunner()


# ---------------------------------------------------------------------------
# _is_finance_position filter
# ---------------------------------------------------------------------------


class TestIsFinancePosition:
    @pytest.mark.parametrize("position", [
        "CFO",
        "Chief Financial Officer",
        "Finance Director - APAC",
        "Financial Controller",
        "Head of Finance",
        "Finance Manager",
        "VP Finance",
        "Director of Finance, Operations",
        "Digital Transformation Lead",
        "Chief Digital Officer",
        "Head of Digital",
    ])
    def test_matches_finance_keywords(self, position):
        assert _is_finance_position(position) is True

    @pytest.mark.parametrize("position", [
        "Software Engineer",
        "CEO",
        "Marketing Manager",
        "HR Director",
        "Sales VP",
        "",
    ])
    def test_rejects_non_finance(self, position):
        assert _is_finance_position(position) is False

    def test_case_insensitive(self):
        assert _is_finance_position("cfo") is True
        assert _is_finance_position("FINANCE DIRECTOR") is True
        assert _is_finance_position("Head Of Finance") is True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_csv(rows: list[dict], path: Path) -> None:
    """Write a LinkedIn Connections.csv to the given path."""
    fieldnames = ["First Name", "Last Name", "Email Address", "Company", "Position", "Connected On", "URL"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


SAMPLE_ROWS = [
    {
        "First Name": "Alice",
        "Last Name": "Chen",
        "Email Address": "alice@example.com",
        "Company": "BHP Group",
        "Position": "CFO",
        "Connected On": "01 Jan 2024",
        "URL": "https://www.linkedin.com/in/alicechen/",
    },
    {
        "First Name": "Bob",
        "Last Name": "Smith",
        "Email Address": "bob@example.com",
        "Company": "Rio Tinto",
        "Position": "Finance Director",
        "Connected On": "15 Mar 2024",
        "URL": "https://www.linkedin.com/in/bobsmith/",
    },
    {
        "First Name": "Carol",
        "Last Name": "Wu",
        "Email Address": "",
        "Company": "Acme Corp",
        "Position": "Head of Digital",
        "Connected On": "20 Jun 2024",
        "URL": "https://www.linkedin.com/in/carolwu/",
    },
    {
        "First Name": "Dave",
        "Last Name": "Brown",
        "Email Address": "dave@example.com",
        "Company": "Telstra",
        "Position": "Software Engineer",
        "Connected On": "01 Jul 2024",
        "URL": "https://www.linkedin.com/in/davebrown/",
    },
    {
        "First Name": "Eve",
        "Last Name": "Jones",
        "Email Address": "",
        "Company": "Woolworths",
        "Position": "Financial Controller",
        "Connected On": "10 Aug 2024",
        "URL": "",  # No URL → should be skipped
    },
    {
        "First Name": "Frank",
        "Last Name": "Lee",
        "Email Address": "",
        "Company": "BHP Group",
        "Position": "VP Finance",
        "Connected On": "12 Sep 2024",
        "URL": "https://www.linkedin.com/in/franklee/",
    },
]


def _make_settings(**overrides):
    s = MagicMock()
    s.feishu_app_id = "fid"
    s.feishu_app_secret = "fsec"
    s.feishu_bitable_app_token = "fatok"
    s.feishu_companies_table_id = "tbl_co"
    s.feishu_contacts_table_id = "tbl_ct"
    s.unipile_api_key = ""
    s.unipile_dsn = ""
    s.unipile_account_id = ""
    s.apify_api_token = ""
    s.anthropic_api_key = ""
    s.brave_search_api_key = ""
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestImportExistingCLI:
    def test_file_not_found(self):
        with patch("src.cli.load_settings") as mock:
            mock.return_value = _make_settings()
            from src.cli import app
            result = runner.invoke(app, [
                "pipeline", "import-existing",
                "--file", "/nonexistent/path.csv",
            ])
        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "File not found" in result.output

    def test_aborts_without_feishu(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as tmp:
            tmp.write("First Name,Last Name,Email Address,Company,Position,Connected On,URL\n")
            tmp_path = tmp.name

        with patch("src.cli.load_settings") as mock:
            mock.return_value = _make_settings(feishu_app_id="")
            from src.cli import app
            result = runner.invoke(app, [
                "pipeline", "import-existing",
                "--file", tmp_path,
            ])
        Path(tmp_path).unlink(missing_ok=True)
        assert result.exit_code == 1

    def test_dry_run_filters_and_reports(self):
        """Dry-run: no Bitable writes, should show filtered contacts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "Connections.csv"
            _write_csv(SAMPLE_ROWS, csv_path)

            with patch("src.cli.load_settings") as mock_settings:
                mock_settings.return_value = _make_settings()

                # Mock Bitable to avoid real API calls
                from src.bitable_client import BitableClient
                with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                     patch.object(BitableClient, "list_records", return_value=[]):

                    from src.cli import app
                    result = runner.invoke(app, [
                        "pipeline", "import-existing",
                        "--file", str(csv_path),
                        "--dry-run",
                    ])

        assert result.exit_code == 0
        # 6 total rows, 4 finance (Alice CFO, Bob FD, Carol Digital, Frank VP Finance)
        # 1 non-finance (Dave SE), 1 finance but no URL (Eve FC)
        assert "6" in result.output  # total connections
        assert "Alice" in result.output
        assert "Bob" in result.output
        assert "Carol" in result.output
        assert "Frank" in result.output
        assert "Dave" not in result.output  # filtered out
        assert "dry-run" in result.output.lower()

    def test_dry_run_counts_skipped_no_url(self):
        """Eve (Financial Controller) has no URL → skipped with warning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "Connections.csv"
            _write_csv(SAMPLE_ROWS, csv_path)

            with patch("src.cli.load_settings") as mock_settings:
                mock_settings.return_value = _make_settings()

                from src.bitable_client import BitableClient
                with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                     patch.object(BitableClient, "list_records", return_value=[]):

                    from src.cli import app
                    result = runner.invoke(app, [
                        "pipeline", "import-existing",
                        "--file", str(csv_path),
                        "--dry-run",
                    ])

        assert result.exit_code == 0
        # Skipped no URL count should appear somewhere
        assert "Skipped (no URL): 1" in result.output

    def test_type_breakdown_in_summary(self):
        """Check that type breakdown appears in output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "Connections.csv"
            _write_csv(SAMPLE_ROWS, csv_path)

            with patch("src.cli.load_settings") as mock_settings:
                mock_settings.return_value = _make_settings()

                from src.bitable_client import BitableClient
                with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                     patch.object(BitableClient, "list_records", return_value=[]):

                    from src.cli import app
                    result = runner.invoke(app, [
                        "pipeline", "import-existing",
                        "--file", str(csv_path),
                        "--dry-run",
                    ])

        assert result.exit_code == 0
        # Should show CFO count
        assert "CFO:" in result.output

    def test_empty_csv_no_finance(self):
        """CSV with only non-finance contacts → graceful exit."""
        rows = [
            {
                "First Name": "Dave", "Last Name": "Brown", "Email Address": "",
                "Company": "Telstra", "Position": "Software Engineer",
                "Connected On": "01 Jul 2024", "URL": "https://linkedin.com/in/dave/",
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "Connections.csv"
            _write_csv(rows, csv_path)

            with patch("src.cli.load_settings") as mock_settings:
                mock_settings.return_value = _make_settings()

                from src.bitable_client import BitableClient
                with patch.object(BitableClient, "_refresh_token", return_value="fake"):
                    from src.cli import app
                    result = runner.invoke(app, [
                        "pipeline", "import-existing",
                        "--file", str(csv_path),
                        "--dry-run",
                    ])

        assert result.exit_code == 0
        assert "No finance-relevant" in result.output

    def test_dedup_against_bitable(self):
        """Contacts already in Bitable are counted as 'already in system'."""
        rows = [
            {
                "First Name": "Alice", "Last Name": "Chen", "Email Address": "",
                "Company": "BHP", "Position": "CFO",
                "Connected On": "01 Jan 2024", "URL": "https://linkedin.com/in/alice/",
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "Connections.csv"
            _write_csv(rows, csv_path)

            with patch("src.cli.load_settings") as mock_settings:
                mock_settings.return_value = _make_settings()

                from src.bitable_client import BitableClient
                from src.models import Contact

                existing_contact = Contact(
                    name="Alice Chen", title="CFO", company_name="BHP",
                    linkedin_url="https://linkedin.com/in/alice/",
                    contact_type="CFO", flow_type="re_activation",
                )

                with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                     patch.object(BitableClient, "list_records", return_value=[]), \
                     patch.object(BitableClient, "find_contact_by_linkedin_url", return_value=existing_contact):

                    from src.cli import app
                    result = runner.invoke(app, [
                        "pipeline", "import-existing",
                        "--file", str(csv_path),
                    ])

        assert result.exit_code == 0
        assert "Already in system: 1" in result.output
        assert "Imported: 0" in result.output

    def test_new_company_created(self):
        """Company not in Bitable → auto-created with source=Existing_329."""
        rows = [
            {
                "First Name": "Bob", "Last Name": "Smith", "Email Address": "",
                "Company": "NewCorp", "Position": "Finance Director",
                "Connected On": "01 Jan 2024", "URL": "https://linkedin.com/in/bob/",
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "Connections.csv"
            _write_csv(rows, csv_path)

            create_calls: list[dict] = []

            def mock_create_record(table_id, fields):
                create_calls.append({"table_id": table_id, "fields": fields})
                return {"record_id": "r_new"}

            with patch("src.cli.load_settings") as mock_settings:
                mock_settings.return_value = _make_settings()

                from src.bitable_client import BitableClient
                with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                     patch.object(BitableClient, "list_records", return_value=[]), \
                     patch.object(BitableClient, "find_contact_by_linkedin_url", return_value=None), \
                     patch.object(BitableClient, "create_record", side_effect=mock_create_record), \
                     patch.object(BitableClient, "batch_create_records", return_value=[]):

                    from src.cli import app
                    result = runner.invoke(app, [
                        "pipeline", "import-existing",
                        "--file", str(csv_path),
                    ])

        assert result.exit_code == 0
        assert "New companies added: 1" in result.output
        # The first create_record call should be for the company
        assert any("tbl_co" in str(c["table_id"]) for c in create_calls)

    def test_utf8_bom_csv(self):
        """Verify utf-8-sig (BOM) CSV files are handled correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "Connections.csv"
            with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, ["First Name", "Last Name", "Email Address",
                                            "Company", "Position", "Connected On", "URL"])
                writer.writeheader()
                writer.writerow({
                    "First Name": "Test", "Last Name": "User", "Email Address": "",
                    "Company": "TestCo", "Position": "CFO",
                    "Connected On": "01 Jan 2024", "URL": "https://linkedin.com/in/test/",
                })

            with patch("src.cli.load_settings") as mock_settings:
                mock_settings.return_value = _make_settings()

                from src.bitable_client import BitableClient
                with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                     patch.object(BitableClient, "list_records", return_value=[]):

                    from src.cli import app
                    result = runner.invoke(app, [
                        "pipeline", "import-existing",
                        "--file", str(csv_path),
                        "--dry-run",
                    ])

        assert result.exit_code == 0
        assert "Test User" in result.output
