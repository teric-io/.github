"""Publish the stable release authorized by the original protected dispatch."""
import base64
import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

# In isolated mode import only the sibling module from the reviewed tools checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import release


def unique_fields(pairs):
    obj = dict(pairs)
    release.require(len(obj) == len(pairs), "Duplicate release request field")
    return obj


def authorize(evidence):
    metadata = release.cut_metadata()
    env = os.environ
    actor = env["RELEASE_TRIGGER_ACTOR_ID"]
    login = env["RELEASE_TRIGGER_LOGIN"]
    release.require(actor.isdecimal() and login.endswith("[bot]") and env["GITHUB_ACTOR_ID"] == actor
                    and env["GITHUB_ACTOR"] == env["GITHUB_TRIGGERING_ACTOR"] == login
                    and env["GITHUB_RUN_ATTEMPT"] == "1", "Expected the original release trigger App")
    workflow = env["TRUSTED_CUT_WORKFLOW"]
    release.require(workflow == env["GITHUB_WORKFLOW_REF"] and
                    workflow.startswith(env["GITHUB_REPOSITORY"] + "/.github/workflows/") and
                    workflow.endswith("@refs/heads/main"), "Unexpected dispatch workflow")
    release.require(env.get("RELEASE_HANDOFF_ENABLED") == "true", "Deployment handoff is pending")
    event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_bytes())
    inputs = event["inputs"]
    release.require(event["repository"]["id"] == int(env["GITHUB_REPOSITORY_ID"])
                    and inputs.get("publish_to_ecr") in ("true", True)
                    and inputs.get("release_version") == metadata["version"], "Expected a release dispatch")
    raw = inputs.get("release_request")
    release.require(isinstance(raw, str) and 0 < len(raw.encode()) <= 2048, "Missing original release request")
    request = json.loads(raw, object_pairs_hook=unique_fields)
    receipt = json.loads((evidence / "registry-proof.json").read_bytes())
    release.require(isinstance(request, dict) and
                    set(request) == {"version", "source", "image", "package", "image_source", "instruction_ts"}
                    and all(isinstance(v, str) for v in request.values())
                    and re.fullmatch(r"[0-9]{10}\.[0-9]{6}", request["instruction_ts"])
                    and re.fullmatch(r"[0-9a-f]{40}", request["image_source"])
                    and request["image_source"] == receipt.get("image_source_commit")
                    and request["source"] == metadata["commit"]
                    and all(request[k] == metadata[k] for k in ("version", "image", "package"))
                    and receipt.get("release_request") == request and receipt.get("release_trigger_actor_id") == actor
                    and receipt.get("run_attempt") == "1", "Release request and verified evidence differ")


def github(method, endpoint, token, body=None):
    # Keep credentials out of process arguments, redirects, logs and exception bodies.
    connection = http.client.HTTPSConnection("api.github.com", timeout=30)
    try:
        connection.request(method, endpoint, None if body is None else json.dumps(body),
                           {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                            "User-Agent": "teric-service-release", "Content-Type": "application/json"})
        response = connection.getresponse()
        release.require(200 <= response.status < 300, f"Publisher GitHub API returned {response.status}")
        data = response.read()
        return json.loads(data) if data else None
    finally:
        connection.close()


def app_jwt(pem, app_id):
    def encode(data):
        return base64.urlsafe_b64encode(data).rstrip(b"=")

    now = int(time.time())
    claims = {"iat": now - 60, "exp": now + 300, "iss": app_id}
    message = encode(b'{"typ":"JWT","alg":"RS256"}') + b"." + encode(json.dumps(claims).encode())
    # Only the public JWT header and claims go to the temporary file.
    with tempfile.TemporaryFile() as claims_file:
        claims_file.write(message)
        claims_file.seek(0)
        fd = claims_file.fileno()
        signed = subprocess.run(["openssl", "dgst", "-sha256", "-sign", "/dev/stdin", f"/dev/fd/{fd}"],
                                input=pem, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                pass_fds=(fd,), check=True)
    return (message + b"." + encode(signed.stdout)).decode()


@contextmanager
def installation_token():
    env = os.environ
    app_id, installation = int(env["RELEASE_APP_ID"]), int(env["RELEASE_INSTALLATION_ID"])
    repo_id = int(env["GITHUB_REPOSITORY_ID"])
    output = subprocess.check_output(["aws", "secretsmanager", "get-secret-value", "--secret-id",
                                      env["PUBLISHER_SECRET_ARN"], "--query", "SecretString", "--output", "json",
                                      "--no-cli-pager"])
    jwt = app_jwt(json.loads(output).encode(), app_id)
    record = github("GET", "/repos/" + env["GITHUB_REPOSITORY"] + "/installation", jwt)
    release.require(record["id"] == installation and record["app_id"] == app_id
                    and record["repository_selection"] == "selected"
                    and record["account"]["id"] == int(env["GITHUB_REPOSITORY_OWNER_ID"]),
                    "Unexpected publisher installation")
    result = github("POST", f"/app/installations/{installation}/access_tokens", jwt,
                    {"repository_ids": [repo_id], "permissions": {"contents": "write"}})
    token = result["token"]
    try:
        release.require(result["permissions"] in ({"contents": "write"}, {"contents": "write", "metadata": "read"}),
                        "Unexpected publisher token permissions")
        scope = github("GET", "/installation/repositories", token)
        release.require(scope["total_count"] == 1 and [r["id"] for r in scope["repositories"]] == [repo_id],
                        "Unexpected publisher token repository scope")
        yield token
    finally:
        github("DELETE", "/installation/token", token)


def publish():
    metadata = release.cut_metadata()
    repo, tag = metadata["repository"], metadata["version"]
    author = int(os.environ["RELEASE_APP_BOT_ID"])
    assets = Path(os.environ["RUNNER_TEMP"]) / "service-release-assets"
    release.require({p.name for p in assets.iterdir()} == release.ASSET_NAMES, "Unexpected validated files")
    expected = {name: "sha256:" + hashlib.sha256((assets / name).read_bytes()).hexdigest()
                for name in release.ASSET_NAMES}
    release_id = release.draft(expected_author=author)["release-id"]
    endpoint = f"repos/{repo}/releases/{release_id}"

    def inspect():
        current = release.api(endpoint)
        release.require(current["id"] == release_id and current["author"]["id"] == author
                        and current["tag_name"] == tag and current["target_commitish"] == metadata["commit"]
                        and current["prerelease"] is False and len(current["assets"]) == len(expected)
                        and {a["name"]: a["digest"] for a in current["assets"]} == expected,
                        "Release identity or asset inventory changed")
        return current

    if inspect()["draft"]:
        refs = release.api(f"repos/{repo}/git/matching-refs/tags/{tag}")
        release.require(all(r["ref"] != "refs/tags/" + tag for r in refs), "Release tag already exists")
        try:
            release.gh("api", "--method", "PATCH", endpoint, "-F", "draft=false")
        except (OSError, subprocess.SubprocessError):
            # Publication could have succeeded. Read back before deciding the outcome.
            pass
    current = inspect()
    release.require(current["draft"] is False and current.get("immutable") is True
                    and release.api(f"repos/{repo}/commits/{tag}")["sha"] == metadata["commit"],
                    "Could not confirm immutable publication of the approved commit")
    return release_id


if __name__ == "__main__":
    authorize(Path("evidence"))
    if sys.argv[1:] == ["publish"]:
        with installation_token() as token:
            os.environ["GH_TOKEN"] = token
            try:
                release_id = publish()
            finally:
                del os.environ["GH_TOKEN"]
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            print(f"release-id={release_id}", file=output)
    else:
        release.require(sys.argv[1:] == ["authorize"], "Unknown publisher operation")
