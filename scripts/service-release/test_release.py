import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import package
import release

COMMIT = "a" * 40
REFERENCE = "registry.example/team/unit@sha256:" + "d" * 64


class PackageTest(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as root:
            work = Path(root)
            source = work / "manifests"
            source.mkdir()
            data = b"apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: sample\n"
            (source / "sample.yaml").write_bytes(data)
            output = work / "layout.tar"
            digest = package.build(work, "manifests", COMMIT, output)
            first = output.read_bytes()
            self.assertEqual(digest, package.build(work, "manifests", COMMIT, output))
            self.assertEqual(first, output.read_bytes())
            with tarfile.open(output) as layout:
                def read_blob(descriptor):
                    path = descriptor["digest"].replace(":", "/")
                    content = layout.extractfile("blobs/" + path).read()
                    self.assertEqual(len(content), descriptor["size"])
                    self.assertEqual("sha256:" + hashlib.sha256(content).hexdigest(), descriptor["digest"])
                    return content
                descriptor, = json.load(layout.extractfile("index.json"))["manifests"]
                self.assertEqual(digest, descriptor["digest"])
                manifest_bytes = read_blob(descriptor)
                manifest = json.loads(manifest_bytes)
                self.assertEqual(manifest["mediaType"], "application/vnd.oci.image.manifest.v1+json")
                self.assertEqual(manifest["config"]["mediaType"], "application/vnd.oci.empty.v1+json")
                self.assertEqual(read_blob(manifest["config"]), b"{}")
                self.assertEqual(manifest["artifactType"], "application/vnd.teric.kubernetes.manifests.v1")
                self.assertEqual(descriptor["artifactType"], manifest["artifactType"])
                self.assertTrue(manifest_bytes.startswith(b'{"schemaVersion":2,'))
                self.assertEqual(COMMIT, manifest["annotations"]["org.opencontainers.image.revision"])
                layer, = manifest["layers"]
                self.assertEqual(layer["mediaType"], "application/vnd.oci.image.layer.v1.tar+gzip")
                with tarfile.open(fileobj=io.BytesIO(read_blob(layer)), mode="r:gz") as archive:
                    self.assertEqual(archive.getnames(), ["sample.yaml"])
                    self.assertEqual(archive.extractfile("sample.yaml").read(), data)
            (source / "extra.yml").symlink_to(output)
            with self.assertRaises(ValueError):
                package.build(work, "manifests", COMMIT, output)


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.event = {"action": "published", "repository": {"full_name": "owner/repo"},
                      "sender": {"type": "Bot", "id": 42},
                      "release": {"id": 21, "tag_name": "v1.0.1", "draft": False, "prerelease": False}}
        self.metadata = {"repository": "owner/repo", "version": "v1.0.1", "commit": COMMIT,
                         "image": REFERENCE, "package": REFERENCE}
        self.source = self.root / "evidence"
        self.source.mkdir()
        (self.source / "release-manifest.json").write_text('{"provides":[],"requires":[],"schema":{}}')
        (self.source / "sbom.json").write_text('{"spdxVersion":"SPDX-2.3"}')
        (self.source / "registry-proof.json").write_text(json.dumps(dict(self.metadata, provenance_verified=True, provenance_ref="signed-build-1")))
        self.files = release.release_assets(self.source, self.root / "assets", self.metadata)
        self.published = dict(self.event["release"], immutable=True, assets=self.assets(self.files))
        self.path = self.root / "event.json"

    @staticmethod
    def assets(files):
        return [{"id": i, "name": name, "digest": "sha256:" + hashlib.sha256(data).hexdigest()}
                for i, (name, data) in enumerate(files.items())]

    def candidate(self, ancestor="ahead"):
        self.path.write_text(json.dumps(self.event))
        env = {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_EVENT_NAME": "release",
               "GITHUB_EVENT_PATH": str(self.path), "RELEASE_APP_BOT_ID": "42"}
        def fetch(*args):
            asset_id = int(args[-1].rsplit("/", 1)[1])
            return list(self.files.values())[asset_id]
        with patch.dict(os.environ, env), patch.object(release, "gh", side_effect=fetch), \
                patch.object(release, "api", side_effect=[self.published, {"sha": COMMIT}, {"status": ancestor}]):
            return release.candidate()

    def test_candidate(self):
        self.assertEqual(self.candidate()["package-reference"], REFERENCE)

    def test_contract_evidence(self):
        fc = {"unit": "mcp-fast-classifier",
              "provides": [{"contract": "mcp-fast-classifier", "serves": "1.0.0"}],
              "requires": [], "schema": {}}
        stateful = {"provides": [{"contract": "dispatch-runtime", "serves": ">=1.3 <2.0"}],
                    "requires": [{"contract": "nats-topology/agent-dispatch", "range": ">=7"}],
                    "schema": {"head": "0023_runtime_binding", "oldest_binary": "v1.4.0"}}
        for contract in (fc, stateful):
            files = self.files | {"release-manifest.json": json.dumps(contract).encode()}
            release.validate_evidence(files, self.metadata)

        for change in ({"provides": None}, {"requires": {}}, {"provides": [False]},
                       {"provides": [{"contract": "fc", "serves": "  "}]},
                       {"requires": [{"contract": "", "range": ">=1"}]},
                       {"requires": [{"contract": "fc", "serves": "1.0.0"}]},
                       {"schema": []}, {"schema": {"head": "0023"}},
                       {"schema": {"head": None, "oldest_binary": "v1.4.0"}},
                       {"schema": {"head": "0023", "oldest_binary": "main"}}):
            files = self.files | {"release-manifest.json": json.dumps(fc | change).encode()}
            with self.subTest(change=change), self.assertRaises(ValueError):
                release.validate_evidence(files, self.metadata)

    def test_malformed_contract_at_cut_and_candidate(self):
        raw = b'{"provides":[],"requires":false,"schema":{}}'
        (self.source / "release-manifest.json").write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "requires"):
            release.release_assets(self.source, self.root / "invalid-assets", self.metadata)
        self.files["release-manifest.json"] = raw
        self.files["SHA256SUMS"] = release.checksum_file(self.files)
        self.published["assets"] = self.assets(self.files)
        with self.assertRaisesRegex(ValueError, "requires"):
            self.candidate()

    def test_rejected_release(self):
        for field, value in (("immutable", False), ("draft", True), ("prerelease", True)):
            with self.subTest(field=field), patch.dict(self.published, {field: value}), self.assertRaises(ValueError):
                self.candidate()
        for change in ({"action": "released"}, {"sender": {"type": "User", "id": 42}},
                       {"sender": {"type": "Bot", "id": 43}}):
            with self.subTest(change=change), patch.dict(self.event, change), self.assertRaises(ValueError):
                self.candidate()
        with self.assertRaises(ValueError):
            self.candidate("diverged")
        with patch.dict(self.event["release"], {"prerelease": True}), self.assertRaises(ValueError):
            self.candidate()

    def test_cut_assets(self):
        files = self.files
        for line in files["SHA256SUMS"].decode().splitlines():
            digest, name = line.split()
            self.assertEqual(hashlib.sha256(files[name]).hexdigest(), digest)
        with self.assertRaises(ValueError):
            release.validate_identity(dict(self.metadata, package="registry/unit:latest"), "owner/repo", "v1.0.1", COMMIT)

    def test_asset_inventory_and_checksums(self):
        assets = self.published["assets"]
        for invalid in (assets[:-1], assets + [dict(assets[0], name="extra")],
                        [dict(assets[0], digest="sha256:" + "0"*64)] + assets[1:]):
            with self.subTest(), patch.dict(self.published, {"assets": invalid}), self.assertRaises(ValueError):
                self.candidate()
        with patch.dict(self.files, {"SHA256SUMS": b"incomplete inventory"}):
            with patch.dict(self.published, {"assets": self.assets(self.files)}), self.assertRaises(ValueError):
                self.candidate()

    def test_verify_both_run_artifacts(self):
        env = {"GITHUB_RUN_ID": "10", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": COMMIT, "GITHUB_REPOSITORY_ID": "30",
               "GITHUB_REPOSITORY": "owner/repo", "EVIDENCE_ARTIFACT_ID": "6", "PACKAGE_ARTIFACT_ID": "7"}
        origin = {"id": 10, "head_sha": COMMIT, "repository_id": 30, "head_repository_id": 30}
        proof = {"id": 6, "expired": False, "workflow_run": origin, "name": "service-release-proof-10"}
        package_info = dict(proof, id=7, name=f"service-package-10-1-{COMMIT}-12345678-1234-1234-1234-1234567890ab")
        with patch.dict(os.environ, env):
            with patch.object(release, "api", side_effect=[proof, package_info]):
                release.verify_run_artifacts()
            for change in ({"expired": True}, {"id": 8}, {"name": "other-producer"},
                           {"workflow_run": dict(origin, id=11)}, {"workflow_run": dict(origin, head_sha="0"*40)},
                           {"workflow_run": dict(origin, head_repository_id=31)}):
                with self.subTest(change=change), patch.object(release, "api", side_effect=[proof, package_info | change]):
                    with self.assertRaises(ValueError):
                        release.verify_run_artifacts()

    def test_prepare_binds_registry_and_package(self):
        repo = "registry.example/team/unit"
        manifests = self.root / "yaml"
        manifests.mkdir()
        (manifests / "object.yaml").write_text("kind: ConfigMap\n")
        layout = self.root / "layout.tar"
        digest = package.build(self.root, "yaml", COMMIT, layout)
        with tarfile.open(layout) as archive:
            raw = archive.extractfile("blobs/" + digest.replace(":", "/")).read()
        (self.source / "package-oci.json").write_bytes(raw)
        image = b'{"schemaVersion":2,"manifests":[]}'
        (self.source / "image-oci.json").write_bytes(image)
        workflow = "owner/repo/.github/workflows/cut.yml@refs/heads/main"
        metadata = dict(self.metadata, package=repo + "@" + digest,
                        image=repo + "@sha256:" + hashlib.sha256(image).hexdigest())
        receipt = dict(metadata, workflow=workflow, run_id="123", run_attempt="1", package_artifact_id="22", provenance_verified=True, provenance_ref="signed-build-1")
        (self.source / "registry-proof.json").write_text(json.dumps(receipt))
        env = self.cut_env(metadata) | {"GITHUB_WORKFLOW_REF": workflow, "TRUSTED_CUT_WORKFLOW": workflow,
                                        "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "PACKAGE_ARTIFACT_ID": "22",
                                        "APPROVED_IMAGE_REPOSITORY": repo, "APPROVED_PACKAGE_REPOSITORY": repo}
        with patch.dict(os.environ, env):
            with patch.dict(os.environ, {"GITHUB_REF_PROTECTED": "false"}), self.assertRaises(ValueError):
                release.verify_cut_inputs(self.source, layout)
            with patch.dict(os.environ, {"GITHUB_RUN_ID": "124"}), self.assertRaises(ValueError):
                release.verify_cut_inputs(self.source, layout)
            with patch.dict(os.environ, {"TRUSTED_CUT_WORKFLOW": ""}), self.assertRaises(ValueError):
                release.verify_cut_inputs(self.source, layout)
            release.verify_cut_inputs(self.source, layout)
            (self.source / "image-oci.json").write_bytes(image + b" ")
            with self.assertRaisesRegex(ValueError, "registry digest"):
                release.verify_cut_inputs(self.source, layout)

    def cut_env(self, metadata):
        return {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main",
                "GITHUB_REF_PROTECTED": "true", "RELEASE_VERSION": metadata["version"],
                "GITHUB_REPOSITORY": metadata["repository"], "GITHUB_SHA": metadata["commit"],
                "IMAGE_REFERENCE": metadata["image"], "PACKAGE_REFERENCE": metadata["package"],
                "RUNNER_TEMP": str(self.root)}

    def test_partial_draft_retry(self):
        (self.root / "assets").rename(self.root / "service-release-assets")
        record, uploaded = {}, {}
        def api(url):
            if "matching-refs" in url:
                return []
            if "/assets?" in url:
                return self.assets(uploaded)
            return dict(record, assets=self.assets(uploaded))

        def gh(*args):
            if "--paginate" in args:
                return json.dumps([[record] if record else []]).encode()
            if "POST" in args:
                self.assertFalse(record)
                self.assertIn("draft=true", args)
                self.assertIn("target_commitish=" + COMMIT, args)
                record.update(id=21, tag_name="v1.0.1", draft=True, target_commitish=COMMIT)
                return json.dumps(record).encode()
            self.assertEqual(args[:2], ("release", "upload"))
            path = Path(args[3])
            if len(uploaded) == 1 and interrupt:
                raise RuntimeError("simulated transfer failure")
            self.assertNotIn(path.name, uploaded)
            uploaded[path.name] = path.read_bytes()
            return b""

        with patch.dict(os.environ, self.cut_env(self.metadata)), patch.object(release, "api", side_effect=api), \
                patch.object(release, "gh", side_effect=gh):
            interrupt = True
            with self.assertRaises(RuntimeError):
                release.draft()
            self.assertEqual(len(uploaded), 1)
            interrupt = False
            with patch.dict(uploaded, {next(iter(uploaded)): b"conflicting asset"}), self.assertRaises(ValueError):
                release.draft()
            self.assertEqual(release.draft(), {"release-id": 21})
            self.assertEqual(uploaded, self.files)
            self.assertEqual(release.draft(), {"release-id": 21})


if __name__ == "__main__":
    unittest.main()
