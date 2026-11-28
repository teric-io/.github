"""Resolve Angular PR descriptions to an immutable service candidate."""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publisher
import release

# Maintenance PRs also produce candidates. Unrecognized headers need correction.
HEADER = re.compile(r"(build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test)(?:\([\w./-]+\))?(!)?: \S.*")
SHA = re.compile(r"[0-9a-f]{40}")


def bump(header, body):
    match = HEADER.fullmatch(header.strip())
    release.require(match, "Expected an Angular PR header such as feat(scope): summary")
    # Ignore quoted examples and fenced snippets in Markdown PR descriptions.
    lines = re.sub(r"<!--.*?-->", "", body, flags=re.S).splitlines()
    fence = ""
    for line in lines:
        stripped = line.lstrip()
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if marker:
            if not fence:
                fence = marker[1]
            elif stripped.rstrip().startswith(fence) and set(stripped.strip()) == {fence[0]}:
                fence = ""
        elif not fence and re.match(r"^BREAKING CHANGE: \S", line):
            return 3
    if match[2]:
        return 3
    return 2 if match[1] == "feat" else 1


def next_version(base, changes):
    numbers = [int(n) for n in release.VERSION.fullmatch(base).groups()]
    index = 3 - max(changes)
    numbers[index] += 1
    numbers[index + 1:] = [0] * (2 - index)
    return "v%d.%d.%d" % tuple(numbers)


def merged_change(repo, sha):
    commit = release.api(f"repos/{repo}/git/commits/{sha}")
    matches = [pr for pr in release.api(f"repos/{repo}/commits/{sha}/pulls?per_page=100")
               if pr["merged_at"] and pr["merge_commit_sha"] == sha and pr["base"]["ref"] == "main"
               and pr["base"]["repo"]["full_name"] == repo]
    release.require(len(matches) == 1 and commit["sha"] == sha and len(commit["parents"]) == 1,
                    "Expected one protected squash merge with a source PR")
    pr = matches[0]
    number = pr["number"]
    release.require(type(number) is int and number > 0, "Invalid source PR number")
    body = (pr["body"] or "").strip()
    first, _, rest = body.partition("\n")
    header, details = (first, rest) if HEADER.fullmatch(first.strip()) else (pr["title"], body)
    change = bump(header, details)
    subject, _, message = commit["message"].partition("\n")
    subject = subject.removesuffix(f" (#{number})")
    release.require(bump(subject, message) == change,
                    "PR version classification disagrees with the immutable squash message")
    return number, change, commit["parents"][0]["sha"]


def versions():
    env = os.environ
    repo, sha = env["GITHUB_REPOSITORY"], env["GITHUB_SHA"]
    release.require(re.fullmatch(r"teric-io/[\w.-]+", repo) and SHA.fullmatch(sha)
                    and env["GITHUB_REF"] == "refs/heads/main" and env["GITHUB_REF_PROTECTED"] == "true"
                    and env["GITHUB_EVENT_NAME"] == "workflow_dispatch"
                    and env["GITHUB_WORKFLOW_REF"] == env["TRUSTED_CUT_WORKFLOW"]
                    and env["GITHUB_WORKFLOW_REF"].startswith(repo + "/.github/workflows/")
                    and env["GITHUB_WORKFLOW_REF"].endswith("@refs/heads/main"), "Unexpected candidate caller")
    inputs = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_bytes())["inputs"]
    release.require(inputs.get("publish_to_ecr") in ("true", True) and inputs.get("image_publication_attempt")
                    and not inputs.get("release_request") and not inputs.get("release_version"),
                    "Expected an evidence build with an automatically assigned version")
    tip = release.api(f"repos/{repo}/git/ref/heads/main")["object"]["sha"]
    release.require(tip == sha, "Candidate must be built from current protected main")
    pages = json.loads(release.gh("api", "--paginate", "--slurp", f"repos/{repo}/tags?per_page=100"))
    stable = [(tuple(map(int, match.groups())), tag) for page in pages for tag in page
              if (match := release.VERSION.fullmatch(tag["name"]))]
    pr, change, parent = merged_change(repo, sha)
    if stable:
        _, tag = max(stable, key=lambda entry: entry[0])
        baseline = release.api(f"repos/{repo}/releases/tags/{tag['name']}")
        release.require(baseline["immutable"] and not baseline["draft"] and not baseline["prerelease"]
                        and baseline["tag_name"] == tag["name"] and tag["commit"]["sha"] != sha,
                        "Expected a prior immutable stable release")
        ancestry = release.api(f"repos/{repo}/compare/{tag['commit']['sha']}...{sha}?per_page=1")
        release.require(ancestry["status"] == "ahead" and ancestry["merge_base_commit"]["sha"] == tag["commit"]["sha"],
                        "Stable release is outside the source history")
        changes = [change]
        while parent != tag["commit"]["sha"]:
            _, previous, parent = merged_change(repo, parent)
            changes.append(previous)
        version = next_version(tag["name"], changes)
    else:
        version = env["INITIAL_RELEASE_VERSION"]
        release.require(release.VERSION.fullmatch(version), "Expected an initial stable version")
    build = env["GITHUB_RUN_ID"]
    release.require(re.fullmatch(r"[1-9][0-9]*", build), "Invalid build ID")
    return {"version": version, "tag": f"{version}-rc.{pr}.{build}", "pr": pr}


def checked_build(repo, commit):
    workflow = os.environ["RELEASE_CHECK_WORKFLOW"]
    release.require(re.fullmatch(r"[a-zA-Z0-9_-]+\.ya?ml", workflow), "Invalid build workflow")
    run_list = release.api(f"repos/{repo}/actions/workflows/{workflow}/runs?event=push&branch=main&head_sha={commit}&per_page=100")
    runs = run_list["workflow_runs"]
    release.require(len(runs) == run_list["total_count"] == 1, "Expected one protected merge CI run")
    run = runs[0]
    release.require(run["repository"]["full_name"] == run["head_repository"]["full_name"] == repo
                    and run["path"] == ".github/workflows/" + workflow and run["event"] == "push"
                    and run["head_branch"] == "main" and run["head_sha"] == commit
                    and run["status"] == "completed" and run["conclusion"] == "success"
                    and type(run["id"]) is int and type(run["run_attempt"]) is int, "Protected merge CI has not passed")
    return f"https://github.com/{repo}/actions/runs/{run['id']}/attempts/{run['run_attempt']}"


def write_tag(identity, metadata, receipt, build_check):
    repo, commit, tag = metadata["repository"], metadata["commit"], identity["tag"]
    release.require(identity["version"] == metadata["version"] and SHA.fullmatch(receipt["image_source_commit"])
                    and not receipt.get("release_request"), "Candidate evidence mismatch")
    expected_tag = f"{metadata['version']}-rc.{identity['pr']}.{os.environ['GITHUB_RUN_ID']}"
    release.require(tag == expected_tag and release.VERSION.fullmatch(metadata["version"]), "Expected derived candidate tag")
    record = {**metadata, **identity, "image_source": receipt["image_source_commit"],
              "build_check": build_check, "run_id": os.environ["GITHUB_RUN_ID"], "provenance": receipt["provenance_ref"],
              "package_artifact_id": os.environ["PACKAGE_ARTIFACT_ID"],
              "evidence_artifact_id": os.environ["EVIDENCE_ARTIFACT_ID"]}
    prefix = f"/repos/{repo}/git"
    def existing(token):
        refs = publisher.github("GET", f"{prefix}/matching-refs/tags/{tag}", token)
        return next((r["object"] for r in refs if r["ref"] == f"refs/tags/{tag}"), None)
    with publisher.installation_token() as token:
        ref = existing(token)
        if ref is None:
            obj = publisher.github("POST", prefix + "/tags", token,
                                   {"tag": tag, "message": json.dumps(record, sort_keys=True),
                                    "object": commit, "type": "commit"})
            try:
                publisher.github("POST", prefix + "/refs", token,
                                 {"ref": "refs/tags/" + tag, "sha": obj["sha"]})
            except (ValueError, OSError):
                if existing(token) is None:
                    raise
            ref = existing(token)
        release.require(ref and ref["type"] == "tag", "Candidate ref must be an annotated tag")
        saved = publisher.github("GET", f"{prefix}/tags/{ref['sha']}", token)
        release.require(saved["tag"] == tag and saved["object"]["type"] == "commit"
                        and saved["object"]["sha"] == commit and json.loads(saved["message"]) == record,
                        "Existing candidate has different source or evidence")


def main():
    identity = versions()
    if sys.argv[1] in ("verify", "tag"):
        env = os.environ
        release.require(env.get("CANDIDATE_ENABLED") == "true" and env["RELEASE_VERSION"] == identity["version"],
                        "Candidate signer disabled or version changed")
        metadata = release.cut_metadata()
        # Validate protected same-run artifacts, OCI bytes, source and evidence before opening the signer.
        release.verify_run_artifacts()
        build_check = checked_build(metadata["repository"], metadata["commit"])
        if sys.argv[1] == "verify":
            release.verify_cut_inputs(Path("evidence"), Path("package/oci-layout.tar"))
        receipt = json.loads(Path("evidence/registry-proof.json").read_bytes())
        if sys.argv[1] == "tag":
            write_tag(identity, metadata, receipt, build_check)
    elif sys.argv[1] != "version":
        raise ValueError("Expected version or tag")
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        for key in ("version", "tag"):
            print(f"{key}={identity[key]}", file=output)


if __name__ == "__main__":
    main()
