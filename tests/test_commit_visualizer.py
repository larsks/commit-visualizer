import argparse
import subprocess
import textwrap
from datetime import datetime
from pathlib import Path

import pytest

from commit_visualizer import (
    build_heatmap_data,
    clone_repo,
    collect_repos,
    get_commit_timestamps,
    is_remote,
    main,
    parse_args,
)


class TestParseArgs:
    def test_days_required(self):
        with pytest.raises(SystemExit):
            parse_args([])

    def test_days_and_repos(self):
        args = parse_args(["--days", "30", "/some/repo", "https://example.com/repo"])
        assert args.days == 30
        assert args.repos == ["/some/repo", "https://example.com/repo"]
        assert args.max_depth is None
        assert args.file is None
        assert args.output is None

    def test_max_depth(self):
        args = parse_args(["--days", "7", "--max-depth", "100", "/repo"])
        assert args.max_depth == 100

    def test_file_option(self):
        args = parse_args(["--days", "7", "--file", "repos.txt"])
        assert args.file == Path("repos.txt")

    def test_output_option(self):
        args = parse_args(["--days", "7", "--output", "chart.png", "/repo"])
        assert args.output == Path("chart.png")

    def test_author_single(self):
        args = parse_args(["--days", "7", "--author", "Alice", "/repo"])
        assert args.author == ["Alice"]

    def test_author_multiple(self):
        args = parse_args(["--days", "7", "-a", "Alice", "-a", "Bob", "/repo"])
        assert args.author == ["Alice", "Bob"]

    def test_author_default_empty(self):
        args = parse_args(["--days", "7", "/repo"])
        assert args.author == []

    def test_all_refs_default_false(self):
        args = parse_args(["--days", "7", "/repo"])
        assert args.all_refs is False

    def test_all_refs_flag(self):
        args = parse_args(["--days", "7", "--all-refs", "/repo"])
        assert args.all_refs is True


class TestCollectRepos:
    def test_from_positional_args(self):
        args = argparse.Namespace(repos=["/a", "/b"], file=None)
        assert collect_repos(args) == ["/a", "/b"]

    def test_from_file(self, tmp_path):
        repo_file = tmp_path / "repos.txt"
        repo_file.write_text(
            textwrap.dedent("""\
            /repo/one
            # comment
            https://example.com/repo

            /repo/two
            """)
        )
        args = argparse.Namespace(repos=[], file=repo_file)
        assert collect_repos(args) == [
            "/repo/one",
            "https://example.com/repo",
            "/repo/two",
        ]

    def test_combined_args_and_file(self, tmp_path):
        repo_file = tmp_path / "repos.txt"
        repo_file.write_text("/from/file\n")
        args = argparse.Namespace(repos=["/from/cli"], file=repo_file)
        assert collect_repos(args) == ["/from/cli", "/from/file"]

    def test_stdin_fallback(self, monkeypatch):
        monkeypatch.setattr(
            "sys.stdin",
            type(
                "FakeStdin",
                (),
                {
                    "isatty": lambda self: False,
                    "read": lambda self: "/from/stdin\nhttps://example.com/repo\n",
                },
            )(),
        )
        args = argparse.Namespace(repos=[], file=None)
        assert collect_repos(args) == ["/from/stdin", "https://example.com/repo"]

    def test_stdin_ignored_when_tty(self, monkeypatch):
        monkeypatch.setattr(
            "sys.stdin",
            type(
                "FakeStdin",
                (),
                {
                    "isatty": lambda self: True,
                },
            )(),
        )
        args = argparse.Namespace(repos=[], file=None)
        assert collect_repos(args) == []

    def test_stdin_ignored_when_repos_provided(self, monkeypatch):
        monkeypatch.setattr(
            "sys.stdin",
            type(
                "FakeStdin",
                (),
                {
                    "isatty": lambda self: False,
                    "read": lambda self: "/from/stdin\n",
                },
            )(),
        )
        args = argparse.Namespace(repos=["/from/cli"], file=None)
        assert collect_repos(args) == ["/from/cli"]


class TestIsRemote:
    @pytest.mark.parametrize(
        "repo",
        [
            "https://github.com/user/repo",
            "http://example.com/repo.git",
            "git://example.com/repo",
            "git@github.com:user/repo.git",
            "ssh://git@example.com/repo",
        ],
    )
    def test_remote_urls(self, repo):
        assert is_remote(repo) is True

    @pytest.mark.parametrize(
        "repo",
        [
            "/home/user/repo",
            "./relative/repo",
            "../other/repo",
            "repo",
        ],
    )
    def test_local_paths(self, repo):
        assert is_remote(repo) is False


class TestBuildHeatmapData:
    def test_empty(self):
        grid, labels = build_heatmap_data([], days=3)
        assert len(grid) == 24
        assert all(len(row) == 3 for row in grid)
        assert all(cell == 0 for row in grid for cell in row)
        assert len(labels) == 3

    def test_single_commit(self):
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).date()
        dt = datetime(today.year, today.month, today.day, 14, 30, tzinfo=timezone.utc)
        grid, labels = build_heatmap_data([(dt, 14)], days=3)
        assert grid[14][-1] == 1
        assert sum(cell for row in grid for cell in row) == 1

    def test_multiple_commits_same_cell(self):
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).date()
        dt1 = datetime(today.year, today.month, today.day, 10, 0, tzinfo=timezone.utc)
        dt2 = datetime(today.year, today.month, today.day, 10, 45, tzinfo=timezone.utc)
        grid, labels = build_heatmap_data([(dt1, 10), (dt2, 10)], days=3)
        assert grid[10][-1] == 2

    def test_commits_on_different_days(self):
        from datetime import datetime, timedelta, timezone

        today = datetime.now(timezone.utc).date()
        yesterday = today - timedelta(days=1)
        dt_today = datetime(
            today.year, today.month, today.day, 9, 0, tzinfo=timezone.utc
        )
        dt_yesterday = datetime(
            yesterday.year, yesterday.month, yesterday.day, 9, 0, tzinfo=timezone.utc
        )
        grid, labels = build_heatmap_data([(dt_today, 9), (dt_yesterday, 9)], days=3)
        assert grid[9][-1] == 1
        assert grid[9][-2] == 1

    def test_date_labels_format(self):
        _, labels = build_heatmap_data([], days=5)
        assert len(labels) == 5
        for label in labels:
            datetime.strptime(label, "%Y-%m-%d")


class TestGetCommitTimestamps:
    def _make_repo_with_commits(self, tmp_path, commit_dates: list[str]) -> Path:
        repo = tmp_path / "test-repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        for i, date in enumerate(commit_dates):
            (repo / f"file{i}.txt").write_text(f"content {i}")
            subprocess.run(
                ["git", "add", "."], cwd=repo, capture_output=True, check=True
            )
            subprocess.run(
                ["git", "commit", "-m", f"commit {i}", "--date", date],
                cwd=repo,
                capture_output=True,
                check=True,
                env={
                    **subprocess.os.environ,
                    "GIT_AUTHOR_DATE": date,
                    "GIT_COMMITTER_DATE": date,
                },
            )
        return repo

    def test_collects_recent_commits(self, tmp_path):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        old = (now - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        repo = self._make_repo_with_commits(tmp_path, [old, recent])
        timestamps = get_commit_timestamps(repo, days=7)
        assert len(timestamps) == 1
        assert timestamps[0][1] == (now - timedelta(hours=2)).hour

    def test_filters_by_author(self, tmp_path):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S+00:00")

        repo = tmp_path / "author-repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "alice@test.com"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Alice"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        (repo / "a.txt").write_text("a")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "alice commit", "--date", recent],
            cwd=repo,
            capture_output=True,
            check=True,
            env={
                **subprocess.os.environ,
                "GIT_AUTHOR_DATE": recent,
                "GIT_COMMITTER_DATE": recent,
                "GIT_AUTHOR_NAME": "Alice",
                "GIT_AUTHOR_EMAIL": "alice@test.com",
            },
        )
        (repo / "b.txt").write_text("b")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "bob commit", "--date", recent],
            cwd=repo,
            capture_output=True,
            check=True,
            env={
                **subprocess.os.environ,
                "GIT_AUTHOR_DATE": recent,
                "GIT_COMMITTER_DATE": recent,
                "GIT_AUTHOR_NAME": "Bob",
                "GIT_AUTHOR_EMAIL": "bob@test.com",
            },
        )

        all_timestamps = get_commit_timestamps(repo, days=7)
        assert len(all_timestamps) == 2

        alice_timestamps = get_commit_timestamps(repo, days=7, authors=["Alice"])
        assert len(alice_timestamps) == 1

        bob_timestamps = get_commit_timestamps(repo, days=7, authors=["Bob"])
        assert len(bob_timestamps) == 1

        both_timestamps = get_commit_timestamps(repo, days=7, authors=["Alice", "Bob"])
        assert len(both_timestamps) == 2

        nobody_timestamps = get_commit_timestamps(repo, days=7, authors=["Nobody"])
        assert len(nobody_timestamps) == 0

    def test_excludes_non_branch_refs_by_default(self, tmp_path):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        repo = self._make_repo_with_commits(tmp_path, [recent])

        subprocess.run(
            ["git", "update-ref", "refs/stacks/main", "HEAD"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        (repo / "extra.txt").write_text("extra")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "stack-only commit", "--date", recent],
            cwd=repo,
            capture_output=True,
            check=True,
            env={
                **subprocess.os.environ,
                "GIT_AUTHOR_DATE": recent,
                "GIT_COMMITTER_DATE": recent,
            },
        )
        subprocess.run(
            ["git", "update-ref", "refs/stacks/main", "HEAD"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "reset", "--hard", "HEAD~1"],
            cwd=repo,
            capture_output=True,
            check=True,
        )

        default_timestamps = get_commit_timestamps(repo, days=7)
        assert len(default_timestamps) == 1

        all_timestamps = get_commit_timestamps(repo, days=7, all_refs=True)
        assert len(all_timestamps) == 2

    def test_nonexistent_repo(self, tmp_path):
        timestamps = get_commit_timestamps(tmp_path / "nonexistent", days=7)
        assert timestamps == []


class TestCloneRepo:
    def test_clone_local_bare(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        subprocess.run(["git", "init"], cwd=src, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=src,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=src,
            capture_output=True,
            check=True,
        )
        (src / "file.txt").write_text("hello")
        subprocess.run(["git", "add", "."], cwd=src, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=src,
            capture_output=True,
            check=True,
        )

        dest = tmp_path / "clones"
        dest.mkdir()
        clone_dir = clone_repo(str(src), dest, max_depth=None)
        assert clone_dir.exists()

    def test_clone_with_depth(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        subprocess.run(["git", "init"], cwd=src, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=src,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=src,
            capture_output=True,
            check=True,
        )
        (src / "file.txt").write_text("hello")
        subprocess.run(["git", "add", "."], cwd=src, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=src,
            capture_output=True,
            check=True,
        )

        dest = tmp_path / "clones"
        dest.mkdir()
        clone_dir = clone_repo(str(src), dest, max_depth=1)
        assert clone_dir.exists()


class TestMainIntegration:
    def test_no_repos_returns_error(self, monkeypatch):
        monkeypatch.setattr(
            "sys.stdin",
            type(
                "FakeStdin",
                (),
                {
                    "isatty": lambda self: True,
                },
            )(),
        )
        assert main(["--days", "7"]) == 1

    def test_nonexistent_local_repo(self, capsys):
        result = main(["--days", "7", "/nonexistent/repo"])
        assert result == 0
        captured = capsys.readouterr()
        assert "No commits found" in captured.out

    def test_with_local_repo(self, tmp_path, monkeypatch):
        from datetime import datetime, timedelta, timezone

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        now = datetime.now(timezone.utc)
        date = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        (repo / "f.txt").write_text("x")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "test", "--date", date],
            cwd=repo,
            capture_output=True,
            check=True,
            env={
                **subprocess.os.environ,
                "GIT_AUTHOR_DATE": date,
                "GIT_COMMITTER_DATE": date,
            },
        )

        output_file = tmp_path / "chart.png"
        result = main(["--days", "7", "--output", str(output_file), str(repo)])
        assert result == 0
        assert output_file.exists()
