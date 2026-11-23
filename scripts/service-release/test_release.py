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
                manifest = json.loads(read_blob(descriptor))
                self.assertEqual(COMMIT, manifest["annotations"]["org.opencontainers.image.revision"])
                layer, = manifest["layers"]
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
        self.data = json.dumps(self.metadata).encode()
        self.published = dict(self.event["release"], immutable=True,
                              assets=[{"name": "release.json", "id": 13,
                                       "digest": "sha256:" + hashlib.sha256(self.data).hexdigest()}])
        self.path = self.root / "event.json"

    def candidate(self, ancestor="ahead"):
        self.path.write_text(json.dumps(self.event))
        env = {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_EVENT_NAME": "release",
               "GITHUB_EVENT_PATH": str(self.path), "RELEASE_APP_BOT_ID": "42"}
        with patch.dict(os.environ, env), patch.object(release, "gh", return_value=self.data), \
                patch.object(release, "api", side_effect=[self.published, {"sha": COMMIT}, {"status": ancestor}]):
            return release.candidate()

    def test_candidate(self):
        self.assertEqual(self.candidate()["package-reference"], REFERENCE)

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
        source = self.root / "in"
        source.mkdir()
        (source / "release-manifest.json").write_text('{"provides":[],"requires":[],"schema":{}}')
        (source / "sbom.json").write_text('{"spdxVersion":"SPDX-2.3"}')
        files = release.release_assets(source, self.root / "out", self.metadata)
        for line in files["SHA256SUMS"].decode().splitlines():
            digest, name = line.split()
            self.assertEqual(hashlib.sha256(files[name]).hexdigest(), digest)
        with self.assertRaises(ValueError):
            release.validate_identity(dict(self.metadata, package="registry/unit:latest"), "owner/repo", "v1.0.1", COMMIT)


if __name__ == "__main__":
    unittest.main()
