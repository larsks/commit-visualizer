#!/usr/bin/env python3
"""Visualize git commit activity by hour of day across multiple repositories."""

import argparse
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib.pyplot as plt


def parse_geometry(value: str) -> tuple[int, int]:
    try:
        w, h = value.lower().split("x")
        width, height = int(w), int(h)
        if width <= 0 or height <= 0:
            raise ValueError
        return (width, height)
    except (ValueError, AttributeError):
        raise argparse.ArgumentTypeError(
            f"Invalid geometry '{value}': expected WxH (e.g., 1200x800)"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Visualize git commit activity by hour of day.",
    )
    parser.add_argument(
        "repos",
        nargs="*",
        help="Git repository URLs or local paths",
    )
    parser.add_argument(
        "--days",
        type=int,
        required=True,
        help="Number of days of history to analyze",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=None,
        help="Maximum clone depth for remote repositories",
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="Read repository URLs from a file (one per line)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Save chart to file instead of displaying interactively",
    )
    parser.add_argument(
        "-a",
        "--author",
        action="append",
        default=[],
        help="Limit to commits by this author (can be specified multiple times)",
    )
    parser.add_argument(
        "--all-refs",
        action="store_true",
        default=False,
        help="Include commits from all refs (default: only local branches)",
    )
    parser.add_argument(
        "--max-commits",
        type=int,
        default=None,
        help="Maximum commits to count per hour block",
    )
    parser.add_argument(
        "-g",
        "--geometry",
        type=parse_geometry,
        default=None,
        help="Image dimensions as WxH in pixels (e.g., 1200x800)",
    )
    return parser.parse_args(argv)


def collect_repos(args: argparse.Namespace) -> list[str]:
    repos: list[str] = list(args.repos)
    if args.file:
        repos.extend(
            line
            for line in args.file.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    if not repos and not sys.stdin.isatty():
        repos.extend(
            line
            for line in sys.stdin.read().splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    return [r.strip() for r in repos if r.strip()]


def is_remote(repo: str) -> bool:
    return "://" in repo or repo.startswith("git@")


def clone_repo(repo: str, dest: Path, max_depth: int | None) -> Path:
    cmd = ["git", "clone", "--bare", "--single-branch"]
    if max_depth is not None:
        cmd.extend(["--depth", str(max_depth)])
    clone_dir = dest / repo.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    cmd.extend([repo, str(clone_dir)])
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return clone_dir


def get_commit_timestamps(
    repo_path: Path,
    days: int,
    authors: list[str] | None = None,
    all_refs: bool = False,
) -> list[tuple[datetime, int]]:
    after = datetime.now(timezone.utc) - timedelta(days=days)
    after_str = after.strftime("%Y-%m-%dT%H:%M:%S%z")
    cmd = [
        "git",
        "-C",
        str(repo_path),
        "log",
        "--format=%aI",
        f"--after={after_str}",
    ]
    if all_refs:
        cmd.append("--all")
    else:
        cmd.append("--glob=refs/heads/*")
    for author in authors or []:
        cmd.extend(["--author", author])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(
            f"Warning: git log failed for {repo_path}: {result.stderr.strip()}",
            file=sys.stderr,
        )
        return []
    timestamps = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            dt = datetime.fromisoformat(line)
            timestamps.append((dt, dt.hour))
        except ValueError:
            continue
    return timestamps


def build_heatmap_data(
    all_timestamps: list[tuple[datetime, int]],
    days: int,
    max_commits: int | None = None,
) -> tuple[list[list[int]], list[str]]:
    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=days - 1)
    date_list = [start_date + timedelta(days=i) for i in range(days)]
    date_index = {d: i for i, d in enumerate(date_list)}

    grid = [[0] * days for _ in range(24)]
    for dt, hour in all_timestamps:
        d = dt.date()
        if d in date_index:
            grid[hour][date_index[d]] += 1
            if max_commits is not None and grid[hour][date_index[d]] > max_commits:
                grid[hour][date_index[d]] = max_commits

    date_labels = [d.strftime("%Y-%m-%d") for d in date_list]
    return grid, date_labels


def render_chart(
    grid: list[list[int]],
    date_labels: list[str],
    days: int,
    output: Path | None,
    geometry: tuple[int, int] | None = None,
) -> None:
    dpi = 150
    if geometry:
        fig_width = geometry[0] / dpi
        fig_height = geometry[1] / dpi
    else:
        fig_width = max(10, days * 0.3)
        fig_height = 8
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))

    im = ax.imshow(grid, aspect="auto", cmap="YlOrRd", interpolation="nearest")
    fig.colorbar(im, ax=ax, label="Commits", shrink=0.8)

    ax.set_ylabel("Hour of Day")
    ax.set_xlabel("Date")
    ax.set_title(f"Commit Activity Heatmap (last {days} day{'s' if days != 1 else ''})")

    ax.set_yticks(range(24))
    ax.set_yticklabels([f"{h:02d}:00" for h in range(24)], fontsize=8)

    if days <= 60:
        step = max(1, days // 20)
        tick_positions = list(range(0, days, step))
        ax.set_xticks(tick_positions)
        ax.set_xticklabels(
            [date_labels[i] for i in tick_positions],
            rotation=45,
            ha="right",
            fontsize=7,
        )
    else:
        step = max(1, days // 15)
        tick_positions = list(range(0, days, step))
        ax.set_xticks(tick_positions)
        ax.set_xticklabels(
            [date_labels[i] for i in tick_positions],
            rotation=45,
            ha="right",
            fontsize=7,
        )

    fig.tight_layout()

    if output:
        fig.savefig(output, dpi=dpi)
        print(f"Chart saved to {output}")
    else:
        plt.show()
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repos = collect_repos(args)
    if not repos:
        print("Error: no repositories provided.", file=sys.stderr)
        print(
            "Provide repos as arguments, via --file, or pipe to stdin.",
            file=sys.stderr,
        )
        return 1

    all_timestamps: list[tuple[datetime, int]] = []

    with tempfile.TemporaryDirectory(prefix="commit-viz-") as tmpdir:
        tmp = Path(tmpdir)
        for repo in repos:
            if is_remote(repo):
                try:
                    print(f"Cloning {repo}...")
                    repo_path = clone_repo(repo, tmp, args.max_depth)
                except subprocess.CalledProcessError as e:
                    print(
                        f"Warning: failed to clone {repo}: {e.stderr.strip()}",
                        file=sys.stderr,
                    )
                    continue
            else:
                repo_path = Path(repo)
                if not repo_path.exists():
                    print(f"Warning: {repo} does not exist, skipping.", file=sys.stderr)
                    continue

            timestamps = get_commit_timestamps(
                repo_path, args.days, args.author, args.all_refs
            )
            all_timestamps.extend(timestamps)

    if not all_timestamps:
        print("No commits found in the given time range.")
        return 0

    grid, date_labels = build_heatmap_data(all_timestamps, args.days, args.max_commits)
    render_chart(grid, date_labels, args.days, args.output, args.geometry)
    return 0


if __name__ == "__main__":
    sys.exit(main())
