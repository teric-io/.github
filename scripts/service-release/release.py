"""Assemble a draft on protected main and verify a published release event."""
import hashlib
import json
import os
import re
import subprocess
import tarfile
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


ASSET_NAMES = {"release.json", "release-manifest.json", "sbom.json", "registry-proof.json", "SHA256SUMS"}


def checksum_file(files):
    return "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(files.items())
                   if name != "SHA256SUMS").encode()


def validate_contract(contract):
    require(isinstance(contract, dict), "Expected a release contract object")
    for section, version_field in (("provides", "serves"), ("requires", "range")):
        entries = contract.get(section)
        require(isinstance(entries, list), f"{section} must be an array")
        for index, entry in enumerate(entries):
            require(isinstance(entry, dict), f"{section}[{index}] must be an object")
            for field in ("contract", version_field):
                value = entry.get(field)
                require(isinstance(value, str) and value.strip(), f"{section}[{index}].{field} must be a nonempty string")

    schema = contract.get("schema")
    require(isinstance(schema, dict), "schema must be an object")
    if schema:
        head = schema.get("head")
        oldest = schema.get("oldest_binary")
        require(isinstance(head, str) and head.strip(), "schema.head must name the schema revision")
        require(isinstance(oldest, str) and VERSION.fullmatch(oldest),
                "schema.oldest_binary must name a stable release version")


def validate_evidence(files, metadata):
    validate_contract(json.loads(files["release-manifest.json"]))
    sbom = json.loads(files["sbom.json"])
    require(sbom.get("spdxVersion") or sbom.get("bomFormat") == "CycloneDX", "Expected SPDX or CycloneDX JSON")
    receipt = json.loads(files["registry-proof.json"])
    require(isinstance(receipt.get("provenance_ref"), str) and receipt["provenance_ref"].strip(),
            "Missing verified build provenance reference")
    require(all(receipt.get(key) == value for key, value in metadata.items())
            and receipt.get("provenance_verified") is True, "Invalid registry proof")


def release_assets(source, output, metadata):
    files = {}
    for name in ("release-manifest.json", "sbom.json", "registry-proof.json"):
        path = source / name
        require(path.is_file() and not path.is_symlink(), f"Missing regular file {name}")
        files[name] = path.read_bytes()
        require(isinstance(json.loads(files[name]), dict), f"{name} must be a JSON object")
    validate_evidence(files, metadata)
    receipt = json.loads(files["registry-proof.json"])
    # A retry of the same run can repeat the readback. Retain the stable run ID in the draft.
    receipt.pop("run_attempt", None)
    files["registry-proof.json"] = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()
    files["release.json"] = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode()
    files["SHA256SUMS"] = checksum_file(files)
    output.mkdir()
    for name, data in files.items():
        (output / name).write_bytes(data)
    return files


def cut_metadata():
    require(os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch"
            and os.environ["GITHUB_REF"] == "refs/heads/main"
            and os.environ.get("GITHUB_REF_PROTECTED") == "true", "Cut requires a dispatch on protected main")
    repository, tag, commit = (os.environ[k] for k in ("GITHUB_REPOSITORY", "RELEASE_VERSION", "GITHUB_SHA"))
    metadata = {"repository": repository, "version": tag, "commit": commit,
                "image": os.environ["IMAGE_REFERENCE"], "package": os.environ["PACKAGE_REFERENCE"]}
    validate_identity(metadata, repository, tag, commit)
    return metadata


def verify_run_artifacts():
    repo, run, commit = (os.environ[k] for k in ("GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_SHA"))
    for kind, variable in (("proof", "EVIDENCE_ARTIFACT_ID"), ("package", "PACKAGE_ARTIFACT_ID")):
        artifact_id = os.environ[variable]
        require(re.fullmatch(r"[1-9][0-9]*", artifact_id), "Expected an immutable artifact ID")
        artifact = api(f"repos/{repo}/actions/artifacts/{artifact_id}")
        origin = artifact["workflow_run"]
        require(str(artifact["id"]) == artifact_id and artifact["expired"] is False
                and str(origin["id"]) == run and origin["head_sha"] == commit
                and origin["head_repository_id"] == origin["repository_id"] == int(os.environ["GITHUB_REPOSITORY_ID"]),
                "Artifact belongs to a different repository, run or revision, or has expired")
        if kind == "proof":
            require(artifact["name"] == "service-release-proof-" + run, "Unexpected proof producer artifact")
        else:
            expected = rf"service-package-{run}-([1-9][0-9]*)-{commit}-[a-f0-9-]{{36}}"
            name = re.fullmatch(expected, artifact["name"])
            require(name and int(name[1]) <= int(os.environ["GITHUB_RUN_ATTEMPT"]),
                    "Unexpected package producer artifact")


def verify_cut_inputs(evidence, layout):
    metadata = cut_metadata()
    workflow = os.environ["TRUSTED_CUT_WORKFLOW"]
    require(workflow == os.environ["GITHUB_WORKFLOW_REF"]
            and workflow.startswith(metadata["repository"] + "/.github/workflows/")
            and workflow.endswith("@refs/heads/main"), "Unapproved caller workflow")
    receipt = json.loads((evidence / "registry-proof.json").read_bytes())
    expected = {**metadata, "workflow": workflow, "run_id": os.environ["GITHUB_RUN_ID"],
                "package_artifact_id": os.environ["PACKAGE_ARTIFACT_ID"], "provenance_verified": True}
    require(all(receipt.get(key) == value for key, value in expected.items()), "Registry proof does not match cut")
    attempt = receipt.get("run_attempt")
    require(isinstance(attempt, str) and attempt.isdecimal()
            and 1 <= int(attempt) <= int(os.environ["GITHUB_RUN_ATTEMPT"]), "Invalid evidence run attempt")
    for kind in ("image", "package"):
        repo, digest = metadata[kind].rsplit("@", 1)
        require(repo == os.environ["APPROVED_" + kind.upper() + "_REPOSITORY"], "Unapproved registry repository")
        raw = (evidence / (kind + "-oci.json")).read_bytes()
        require("sha256:" + hashlib.sha256(raw).hexdigest() == digest, f"{kind} registry digest mismatch")
        require(json.loads(raw).get("schemaVersion") == 2, f"Invalid {kind} registry manifest")

    digest = metadata["package"].rsplit("@", 1)[1]
    with tarfile.open(layout, "r:") as archive:
        descriptors = json.load(archive.extractfile("index.json"))["manifests"]
        require(len(descriptors) == 1 and descriptors[0]["digest"] == digest, "OCI layout digest mismatch")

        def read_blob(descriptor):
            address = descriptor["digest"]
            require(re.fullmatch(r"sha256:[a-f0-9]{64}", address), "Invalid OCI blob digest")
            data = archive.extractfile("blobs/" + address.replace(":", "/")).read()
            require("sha256:" + hashlib.sha256(data).hexdigest() == address
                    and len(data) == descriptor["size"], "Corrupt OCI blob")
            return data

        manifest = json.loads(read_blob(descriptors[0]))
        require(manifest["annotations"]["org.opencontainers.image.revision"] == metadata["commit"],
                "Package source revision mismatch")
        read_blob(manifest["config"])
        layers = manifest["layers"]
        require(len(layers) == 1 and layers[0]["mediaType"] == "application/vnd.oci.image.layer.v1.tar+gzip",
                "Unexpected rendered manifest layer")
        read_blob(layers[0])

    assets = Path(os.environ["RUNNER_TEMP"]) / "service-release-assets"
    release_assets(evidence, assets, metadata)


def draft(expected_author=None):
    metadata = cut_metadata()
    repository, tag, commit = (metadata[key] for key in ("repository", "version", "commit"))
    assets = Path(os.environ["RUNNER_TEMP"]) / "service-release-assets"
    files = {f.name: f.read_bytes() for f in assets.iterdir()}
    require(json.loads(files["release.json"]) == metadata, "Validated release metadata mismatch")
    releases = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repository}/releases?per_page=100"))
    matches = [r for page in releases for r in page if r["tag_name"] == tag]
    require(len(matches) <= 1, "Multiple releases for this version")
    if matches:
        release = matches[0]
        if expected_author is not None:
            require(release["author"]["id"] == expected_author and release["prerelease"] is False,
                    "Version reserved by a different publisher or for a prerelease")
        require(release["target_commitish"] == commit and (release["draft"] or expected_author is not None),
                "Version already in use")
        if not release["draft"]:
            return {"release-id": release["id"]}
    else:
        # Reserve a new version only. The App creates its tag at publication.
        refs = api(f"repos/{repository}/git/matching-refs/tags/{tag}")
        require(all(ref["ref"] != "refs/tags/" + tag for ref in refs), "Tag already exists")
        release = json.loads(gh("api", "--method", "POST", f"repos/{repository}/releases",
                                "-f", f"tag_name={tag}", "-f", f"target_commitish={commit}",
                                "-f", f"name={tag}", "-F", "draft=true", "-F", "prerelease=false"))
    release_id = release["id"]
    if expected_author is not None:
        require(release["author"]["id"] == expected_author, "Unexpected draft author")
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
    assets = release["assets"]
    require(len(assets) == len(ASSET_NAMES) and {a["name"] for a in assets} == ASSET_NAMES,
            "Incomplete or unexpected release assets")
    files = {}
    for asset in assets:
        data = gh("api", "-H", "Accept: application/octet-stream", f"repos/{repository}/releases/assets/{asset['id']}")
        require(asset.get("digest") == "sha256:" + hashlib.sha256(data).hexdigest(), "Release asset digest mismatch")
        files[asset["name"]] = data
    require(files["SHA256SUMS"] == checksum_file(files), "Release checksum inventory mismatch")
    metadata = json.loads(files["release.json"])
    validate_identity(metadata, repository, tag, commit)
    validate_evidence(files, metadata)
    return {"release-id": release["id"], "source-sha": commit, "version": tag,
            "package-reference": metadata["package"], "image-reference": metadata["image"]}


if __name__ == "__main__":
    import sys

    if sys.argv[1] == "artifacts":
        verify_run_artifacts()
        sys.exit(0)
    if sys.argv[1] == "prepare":
        verify_cut_inputs(Path("evidence"), Path("package/oci-layout.tar"))
        sys.exit(0)
    outputs = {"draft": draft, "candidate": candidate}[sys.argv[1]]()
    with open(os.environ["GITHUB_OUTPUT"], "a") as f:
        for key, value in outputs.items():
            print(f"{key}={value}", file=f)
