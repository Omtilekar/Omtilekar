"""Generate Om Tilekar's GitHub profile SVG cards."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape


USER_NAME = os.environ.get("USER_NAME", "Omtilekar")
TOKEN = os.environ.get("ACCESS_TOKEN") or os.environ.get("GITHUB_TOKEN")
API_ROOT = "https://api.github.com"
GRAPHQL_URL = "https://api.github.com/graphql"
USER_AGENT = "Omtilekar-profile-readme"
FONT_STACK = "Consolas, Monaco, 'Courier New', monospace"

ASCII_PORTRAIT = r"""
                       .=+*#$$$$#*+=.
                    .=#%$########$%#=.
                  .+#$###&&&&&&###$#+.
                 :$##&&********&&##$:
                :$#&**++======++**&#$:
               .%#&*+=-::::::::-=+*&#%.
               $#&*=-:.        .:-=*&#$
              .#&*=-:.          .:-+*&#.
              =#*+-:.            .:-+*#=
              +&*=-:.            .:-=*#+
              *&*=-==++=----=++==-+*&*
              *&+=+*####*++*####*+=+&*
              *&+=|:::::|++|:::::|=+&*
              +&+=+*##*+=--=+*##*+=+&+
              :#*=-:---:  ::---:-=*#:
               *#*=-:.    ||    .:=*#
               =#&+=-:.  /||\  .:-+&=
                *#&+=-. .:++:. .-=&#*
                :#&&+=-..----..-=&&#:
                 =#&&*+=+****+=*&&#=
                  =#&&&########&&#=
                   :*#&&&&&&&&#*:
                     .=+******+=.
                        .::::.
                  .::/==\    /==\::.
               .:/+***+=\  /=+***+\:.
            .:=*&&&&&&&*|  |*&&&&&&&*=:.
          .-+#%%%%####&*|  |*####%%%%#+-.
         :=#%$###&&&***|    |***&&&###$#=:
        -#%$##&&***+++=|    |==+++***&&##%#-
       :#%$#&**++==--:/      \:--==++**&#$%#:
       +%$#&*+=-::.  /   ||   \  .::-=+*&#$%+
      .#%$#*+=-:.   /   .||.   \   .:-=+*#$%#.
      -%$#&+=-:.   /   .+##+.   \   .:-=+&#$%-
      =%$#*+=-.   /   .=#%%#=.   \   .-=+*#$%=
      =%$#*+-:.  /   :=#%%%%#=:   \  .:-+*#$%=
      -%$#&+=-. /   :+#%%$$%%#+:   \ .-=+&#$%-
      .#%$#*+=-/   :+#%$$$$%%#+:   \-=+*#$%#.
       +%$##&*/   .=#%$$$$$$%#=.    \*&#$%+
       :#%$##&+==+#%$########$%#+==+&##$%#:
        -#%$###&&&###&&****&&###&&&###$%#-
         :=#%%$######&&++++&&######$%%#=:
           .-+#%$$####&&&&&&####$$%#+-.
              .:=*#%%$$$$$$$$%%#*=:.
                   .:-=++++=-:.
""".strip("\n").splitlines()


@dataclass
class ProfileStats:
    repositories_owned: int | None = None
    repositories_contributed: int | None = None
    stars_received: int | None = None
    followers: int | None = None
    commits: int | None = None
    lines_added: int | None = None
    lines_deleted: int | None = None
    generated_at: str = ""
    status: str = "live"

    @property
    def net_lines(self) -> int | None:
        if self.lines_added is None or self.lines_deleted is None:
            return None
        return self.lines_added - self.lines_deleted


class GitHubClient:
    def __init__(self, token: str) -> None:
        self.token = token

    def request_json(
        self,
        url: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        accept: str = "application/vnd.github+json",
    ) -> tuple[int, Any]:
        data = None
        headers = {
            "Accept": accept,
            "Authorization": f"Bearer {self.token}",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status = response.status
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            message = raw
            try:
                parsed = json.loads(raw)
                message = parsed.get("message", raw)
            except json.JSONDecodeError:
                pass
            raise RuntimeError(f"GitHub API error {exc.code} for {url}: {message}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Unable to reach GitHub API: {exc.reason}") from exc

        if not raw:
            return status, None
        return status, json.loads(raw)

    def rest(self, path: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
        query = ""
        if params:
            query = "?" + urllib.parse.urlencode(params)
        return self.request_json(f"{API_ROOT}{path}{query}")

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        _, data = self.request_json(
            GRAPHQL_URL,
            method="POST",
            payload={"query": query, "variables": variables},
            accept="application/vnd.github+json",
        )
        if data.get("errors"):
            errors = "; ".join(error.get("message", "unknown error") for error in data["errors"])
            raise RuntimeError(f"GitHub GraphQL error: {errors}")
        return data["data"]


def require_token() -> str:
    if TOKEN:
        return TOKEN
    raise SystemExit(
        "Missing GitHub token. Set ACCESS_TOKEN or GITHUB_TOKEN, then run "
        "`python generate_profile.py`. For layout-only rendering, use "
        "`python generate_profile.py --offline`."
    )


def paged_repos(client: GitHubClient, user_name: str) -> list[dict[str, Any]]:
    repos: list[dict[str, Any]] = []
    page = 1
    while True:
        _, batch = client.rest(
            f"/users/{urllib.parse.quote(user_name)}/repos",
            {
                "type": "owner",
                "sort": "updated",
                "direction": "desc",
                "per_page": 100,
                "page": page,
            },
        )
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return repos


def contributed_repo_count(client: GitHubClient, user_name: str) -> int | None:
    query = """
    query($login: String!) {
      user(login: $login) {
        repositoriesContributedTo(
          first: 1,
          includeUserRepositories: false,
          contributionTypes: [COMMIT, ISSUE, PULL_REQUEST, REPOSITORY]
        ) {
          totalCount
        }
      }
    }
    """
    try:
        data = client.graphql(query, {"login": user_name})
    except RuntimeError:
        return None
    user = data.get("user")
    if not user:
        return None
    return int(user["repositoriesContributedTo"]["totalCount"])


def repo_contribution_totals(client: GitHubClient, repos: list[dict[str, Any]], user_name: str) -> tuple[int, int, int]:
    commits = 0
    additions = 0
    deletions = 0
    max_repos = int(os.environ.get("MAX_REPO_STATS", "100"))
    stat_repos = [repo for repo in repos if not repo.get("fork") and not repo.get("archived")]

    for repo in stat_repos[:max_repos]:
        full_name = repo["full_name"]
        path = f"/repos/{urllib.parse.quote(full_name, safe='/')}/stats/contributors"
        data = None
        for attempt in range(5):
            status, payload = client.rest(path)
            if status == 202:
                time.sleep(2 + attempt)
                continue
            data = payload
            break
        if not isinstance(data, list):
            continue

        for contributor in data:
            author = contributor.get("author") or {}
            if (author.get("login") or "").lower() != user_name.lower():
                continue
            commits += int(contributor.get("total") or 0)
            for week in contributor.get("weeks", []):
                additions += int(week.get("a") or 0)
                deletions += int(week.get("d") or 0)
            break

    return commits, additions, deletions


def fetch_stats(user_name: str) -> ProfileStats:
    client = GitHubClient(require_token())
    _, user = client.rest(f"/users/{urllib.parse.quote(user_name)}")
    repos = paged_repos(client, user_name)
    commits, additions, deletions = repo_contribution_totals(client, repos, user_name)

    return ProfileStats(
        repositories_owned=len(repos),
        repositories_contributed=contributed_repo_count(client, user_name),
        stars_received=sum(int(repo.get("stargazers_count") or 0) for repo in repos),
        followers=int(user.get("followers") or 0),
        commits=commits,
        lines_added=additions,
        lines_deleted=deletions,
        generated_at=dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC"),
    )


def offline_stats() -> ProfileStats:
    return ProfileStats(
        generated_at=dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC"),
        status="offline preview",
    )


def fmt(value: int | None) -> str:
    if value is None:
        return "--"
    return f"{value:,}"


def dotted(label: str, value: str, width: int = 23) -> str:
    dots = "." * max(2, width - len(label))
    return f"{label} {dots} {value}"


def profile_lines(stats: ProfileStats, user_name: str) -> list[str]:
    lines = [
        "--------------------------------",
        dotted("Name", "Om Tilekar"),
        dotted("GitHub", user_name),
        dotted("OS", "Windows 11, Linux"),
        dotted("IDE", "VS Code, Jupyter"),
        dotted("Role", "AI / NLP / Data Science"),
        "",
        "Languages.Programming",
        "Python, SQL, C++, JavaScript",
        "",
        "AI.ML",
        "NLP, LLMs, SLMs, RAG, ML",
        "",
        "Research",
        "NLP, AI Safety, Evaluation",
        "Retrieval Systems",
        "",
        "Tools",
        "PyTorch, scikit-learn, XGBoost",
        "FastAPI, PostgreSQL",
        "",
        "GitHub.Stats",
        dotted("Repos owned", fmt(stats.repositories_owned)),
        dotted("Repos contributed", fmt(stats.repositories_contributed)),
        dotted("Stars received", fmt(stats.stars_received)),
        dotted("Followers", fmt(stats.followers)),
        dotted("Commits", fmt(stats.commits)),
        dotted("Lines added", fmt(stats.lines_added)),
        dotted("Lines deleted", fmt(stats.lines_deleted)),
        dotted("Net LOC", fmt(stats.net_lines)),
        "",
        dotted("Updated", stats.generated_at, width=16),
    ]
    if stats.status != "live":
        lines.append(dotted("Status", stats.status, width=16))
    return lines


def tspans(lines: list[str], x: int, y: int, line_height: float) -> str:
    output = []
    for index, line in enumerate(lines):
        escaped = escape(line)
        output.append(f'<tspan x="{x}" y="{y + index * line_height:.1f}">{escaped}</tspan>')
    return "\n".join(output)


THEMES = {
    "dark": {
        "background": "#0d1117",
        "panel": "#161b22",
        "border": "#30363d",
        "portrait": "#c9d1d9",
        "primary": "#e6edf3",
        "secondary": "#8b949e",
        "accent": "#58a6ff",
        "glow": "#1f6feb",
    },
    "light": {
        "background": "#f6f8fa",
        "panel": "#ffffff",
        "border": "#d0d7de",
        "portrait": "#24292f",
        "primary": "#1f2328",
        "secondary": "#57606a",
        "accent": "#0969da",
        "glow": "#54aeff",
    },
}


def render_svg(theme_name: str, stats: ProfileStats, user_name: str) -> str:
    theme = THEMES[theme_name]
    right_lines = profile_lines(stats, user_name)
    portrait = tspans(ASCII_PORTRAIT, 48, 68, 11.0)
    info = tspans(right_lines, 600, 110, 15.4)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="660" viewBox="0 0 1100 660" role="img" aria-labelledby="title desc">
  <title id="title">Om Tilekar GitHub profile card</title>
  <desc id="desc">Terminal-style profile card with a text-symbol portrait and dynamic GitHub statistics.</desc>
  <rect width="1100" height="660" rx="0" fill="{theme['background']}"/>
  <rect x="24" y="24" width="1052" height="612" rx="8" fill="{theme['panel']}" stroke="{theme['border']}" stroke-width="1.5"/>
  <path d="M552 52V608" stroke="{theme['border']}" stroke-width="1"/>
  <circle cx="54" cy="50" r="5" fill="{theme['accent']}" opacity="0.95"/>
  <circle cx="74" cy="50" r="5" fill="{theme['secondary']}" opacity="0.55"/>
  <circle cx="94" cy="50" r="5" fill="{theme['secondary']}" opacity="0.35"/>
  <text x="600" y="52" fill="{theme['secondary']}" font-family="{FONT_STACK}" font-size="13">~/profile/readme</text>
  <text xml:space="preserve" fill="{theme['portrait']}" font-family="{FONT_STACK}" font-size="10.4" font-weight="600" letter-spacing="0">
{portrait}
  </text>
  <text xml:space="preserve" fill="{theme['primary']}" font-family="{FONT_STACK}" font-size="13.5" letter-spacing="0">
{info}
  </text>
  <text x="600" y="84" fill="{theme['accent']}" font-family="{FONT_STACK}" font-size="16" font-weight="700">{escape(user_name.lower())}</text>
  <rect x="596" y="94" width="268" height="2" fill="{theme['glow']}" opacity="0.35"/>
</svg>
"""


def write_svg_files(output_dir: Path, stats: ProfileStats, user_name: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for theme_name in ("dark", "light"):
        target = output_dir / f"{theme_name}_mode.svg"
        target.write_text(render_svg(theme_name, stats, user_name), encoding="utf-8", newline="\n")
        print(f"wrote {target}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="render layout with placeholder stats")
    parser.add_argument("--output-dir", default=".", help="directory for SVG outputs")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    stats = offline_stats() if args.offline else fetch_stats(USER_NAME)
    write_svg_files(Path(args.output_dir), stats, USER_NAME)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
