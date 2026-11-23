"""Assemble a draft on protected main and verify a published release event."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

VERSION = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
REFERENCE = re.compile(r"[a-z0-9][a-z0-9.:/-]*/[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def gh(*args):
    return subprocess.check_output(["gh", *args])


def api(endpoint):
    return json.loads(gh("api", endpoint))


def validate_identity(metadata, repository, tag, commit):
    require(metadata["repository"] == repository and metadata["version"] == tag
            and metadata["commit"] == commit, "Release metadata does not match the tagged source")
    require(VERSION.fullmatch(tag) and re.fullmatch(r"[0-9a-f]{40}", commit), "Invalid release identity")
    for key in ("image", "package"):
        require(REFERENCE.fullmatch(metadata[key]), f"{key} must be a fully qualified digest reference")


def release_assets(source, output, metadata):
    files = {}
    for name in ("release-manifest.json", "sbom.json"):
        path = source / name
        require(path.is_file() and not path.is_symlink(), f"Missing regular file {name}")
        files[name] = path.read_bytes()
        require(isinstance(json.loads(files[name]), dict), f"{name} must be a JSON object")
    contract = json.loads(files["release-manifest.json"])
    require(all(key in contract for key in ("provides", "requires", "schema")), "Missing release contract fields")
    sbom = json.loads(files["sbom.json"])
    require(sbom.get("spdxVersion") or sbom.get("bomFormat") == "CycloneDX", "Expected SPDX or CycloneDX JSON")
    files["release.json"] = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode()
    files["SHA256SUMS"] = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n"
                                  for name, data in sorted(files.items())).encode()
    output.mkdir()
    for name, data in files.items():
        (output / name).write_bytes(data)
    return files


def draft():
    require(os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch"
            and os.environ["GITHUB_REF"] == "refs/heads/main"
            and os.environ.get("GITHUB_REF_PROTECTED") == "true", "Cut requires a dispatch on protected main")
    repository, tag, commit = (os.environ[k] for k in ("GITHUB_REPOSITORY", "RELEASE_VERSION", "GITHUB_SHA"))
    metadata = {"repository": repository, "version": tag, "commit": commit,
                "image": os.environ["IMAGE_REFERENCE"], "package": os.environ["PACKAGE_REFERENCE"]}
    validate_identity(metadata, repository, tag, commit)
    assets = Path(os.environ["RUNNER_TEMP"]) / "service-release-assets"
    files = release_assets(Path("evidence"), assets, metadata)
    releases = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repository}/releases?per_page=100"))
    matches = [r for page in releases for r in page if r["tag_name"] == tag]
    require(len(matches) <= 1, "Multiple releases for this version")
    if matches:
        release = matches[0]
        require(release["draft"] and release["target_commitish"] == commit, "Version already in use")
    else:
        # Reserve a new version only. The App creates its tag at publication.
        refs = api(f"repos/{repository}/git/matching-refs/tags/{tag}")
        require(all(ref["ref"] != "refs/tags/" + tag for ref in refs), "Tag already exists")
        release = json.loads(gh("api", "--method", "POST", f"repos/{repository}/releases",
                                "-f", f"tag_name={tag}", "-f", f"target_commitish={commit}",
                                "-f", f"name={tag}", "-F", "draft=true", "-F", "prerelease=false"))
    release_id = release["id"]
    existing = {a["name"]: a for a in api(f"repos/{repository}/releases/{release_id}/assets?per_page=100")}
    require(existing.keys() <= files.keys(), "Draft contains unexpected assets")
    for name, data in files.items():
        if name in existing:
            require(existing[name].get("digest") == "sha256:" + hashlib.sha256(data).hexdigest(),
                    f"Draft already has different content for {name}")
        else:
            gh("release", "upload", tag, str(assets / name), "--repo", repository)
    current = api(f"repos/{repository}/releases/{release_id}")
    expected = {name: "sha256:" + hashlib.sha256(data).hexdigest() for name, data in files.items()}
    require(current["draft"] and {a["name"]: a["digest"] for a in current["assets"]} == expected,
            "Incomplete draft; re-run with the same inputs")
    return {"release-id": release_id}


def candidate():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    require(os.environ["GITHUB_EVENT_NAME"] == "release" and event["action"] == "published",
            "Expected an original release publication")
    expected_bot = os.environ["RELEASE_APP_BOT_ID"]
    require(expected_bot.isdecimal() and event["sender"]["type"] == "Bot"
            and str(event["sender"]["id"]) == expected_bot, "Publisher is not the configured release App")
    repository = os.environ["GITHUB_REPOSITORY"]
    require(event["repository"]["full_name"] == repository, "Unexpected release repository")
    published = event["release"]
    require(not published["draft"] and not published["prerelease"], "Only stable publications are eligible")
    release = api(f"repos/{repository}/releases/{published['id']}")
    require(release.get("immutable") is True and not release["draft"] and not release["prerelease"]
            and release["tag_name"] == published["tag_name"], "Expected the same immutable published release")
    tag = release["tag_name"]
    require(VERSION.fullmatch(tag), "Expected a stable semantic version tag")
    commit = api(f"repos/{repository}/commits/{tag}")["sha"]
    require(api(f"repos/{repository}/compare/{commit}...main")["status"] in ("identical", "ahead"),
            "Tagged source is not on main")
    item = next((a for a in release["assets"] if a["name"] == "release.json"), None)
    require(item is not None, "Missing release.json")
    data = gh("api", "-H", "Accept: application/octet-stream", f"repos/{repository}/releases/assets/{item['id']}")
    require(item.get("digest") == "sha256:" + hashlib.sha256(data).hexdigest(), "Release asset digest mismatch")
    metadata = json.loads(data)
    validate_identity(metadata, repository, tag, commit)
    return {"release-id": release["id"], "source-sha": commit, "version": tag,
            "package-reference": metadata["package"], "image-reference": metadata["image"]}


if __name__ == "__main__":
    import sys

    outputs = {"draft": draft, "candidate": candidate}[sys.argv[1]]()
    with open(os.environ["GITHUB_OUTPUT"], "a") as f:
        for key, value in outputs.items():
            print(f"{key}={value}", file=f)
