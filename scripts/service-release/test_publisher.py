import base64
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import publisher
import release


class PublisherTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.request = {"version": "v1.0.0", "source": "a" * 40, "image_source": "c" * 40,
                        "image": "registry.example/fc@sha256:" + "b" * 64,
                        "package": "registry.example/manifests@sha256:" + "d" * 64,
                        "instruction_ts": "1795553174.191719"}
        workflow = "org/fc/.github/workflows/package.yml@refs/heads/main"
        self.env = {"RELEASE_VERSION": self.request["version"], "GITHUB_SHA": self.request["source"],
                    "IMAGE_REFERENCE": self.request["image"], "PACKAGE_REFERENCE": self.request["package"],
                    "GITHUB_REPOSITORY": "org/fc", "GITHUB_REPOSITORY_ID": "19", "GITHUB_REPOSITORY_OWNER_ID": "7",
                    "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF_PROTECTED": "true",
                    "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_PATH": str(self.root / "event.json"),
                    "TRUSTED_CUT_WORKFLOW": workflow, "GITHUB_WORKFLOW_REF": workflow, "GITHUB_RUN_ATTEMPT": "1",
                    "RELEASE_TRIGGER_ACTOR_ID": "42", "GITHUB_ACTOR_ID": "42", "GITHUB_ACTOR": "trigger[bot]",
                    "GITHUB_TRIGGERING_ACTOR": "trigger[bot]", "RELEASE_TRIGGER_LOGIN": "trigger[bot]",
                    "RELEASE_HANDOFF_ENABLED": "true", "RELEASE_APP_BOT_ID": "101", "RUNNER_TEMP": str(self.root)}
        env = patch.dict(os.environ, self.env)
        env.start()
        self.addCleanup(env.stop)
        self.inputs = {"publish_to_ecr": "true", "release_version": self.request["version"],
                       "release_request": json.dumps(self.request)}
        self.receipt = {"run_attempt": "1", "release_trigger_actor_id": "42", "release_request": self.request,
                        "image_source_commit": self.request["image_source"]}

    def authorize(self):
        (self.root / "event.json").write_text(json.dumps({"repository": {"id": 19}, "inputs": self.inputs}))
        (self.root / "registry-proof.json").write_text(json.dumps(self.receipt))
        publisher.authorize(self.root)

    def test_delegated_authority(self):
        self.authorize()
        for key, value in {"GITHUB_ACTOR_ID": "35", "GITHUB_ACTOR": "coder[bot]",
                           "GITHUB_TRIGGERING_ACTOR": "human", "GITHUB_REF": "refs/heads/other",
                           "GITHUB_WORKFLOW_REF": "org/fc/.github/workflows/ci.yml@refs/heads/main",
                           "GITHUB_EVENT_NAME": "pull_request", "GITHUB_RUN_ATTEMPT": "2",
                           "RELEASE_HANDOFF_ENABLED": "false"}.items():
            with self.subTest(key=key), patch.dict(os.environ, {key: value}), self.assertRaises(ValueError):
                self.authorize()
        for change in ({"publish_to_ecr": "false"}, {"release_request": ""}, {"release_version": "v2.0.0"},
                       {"release_request": json.dumps(self.request | {"source": "f" * 40})},
                       {"release_request": json.dumps(self.request | {"image": self.request["package"]})},
                       {"release_request": json.dumps(self.request | {"package": self.request["image"]})},
                       {"release_request": json.dumps(self.request | {"instruction_ts": "1111111111.000001"})},
                       {"release_request": '{"version":"v1.0.0",' + json.dumps(self.request)[1:]}):
            with self.subTest(change=change), patch.dict(self.inputs, change), self.assertRaises(ValueError):
                self.authorize()
        for change in ({"image_source_commit": "e" * 40}, {"release_request": None},
                       {"release_trigger_actor_id": "14"}, {"run_attempt": "2"}):
            with self.subTest(change=change), patch.dict(self.receipt, change), self.assertRaises(ValueError):
                self.authorize()

    def test_publish_readback_and_lost_response(self):
        files = self.root / "service-release-assets"
        files.mkdir()
        for name in release.ASSET_NAMES:
            (files / name).write_bytes(name.encode())
        assets = [{"name": n, "digest": "sha256:" + hashlib.sha256(n.encode()).hexdigest()} for n in release.ASSET_NAMES]
        record = {"id": 9, "author": {"id": 101}, "target_commitish": self.request["source"],
                  "tag_name": self.request["version"], "draft": True, "prerelease": False, "assets": assets}
        refs = []

        def api(endpoint):
            if "/matching-refs/" in endpoint:
                return refs
            if "/commits/" in endpoint:
                return {"sha": self.request["source"]}
            return record

        def send(*args):
            self.assertEqual(args, ("api", "--method", "PATCH", "repos/org/fc/releases/9", "-F", "draft=false"))
            record.update(draft=False, immutable=True)
            raise subprocess.CalledProcessError(1, "gh")

        with patch.object(release, "draft", return_value={"release-id": 9}), patch.object(release, "api", side_effect=api), \
                patch.object(release, "gh", side_effect=send) as publish:
            for change in ({"assets": assets[:-1]}, {"author": {"id": 17}}, {"target_commitish": "e" * 40},
                           {"prerelease": True}, {"tag_name": "v1.0.1"}):
                with self.subTest(change=change), patch.dict(record, change), self.assertRaises(ValueError):
                    publisher.publish()
            refs.append({"ref": "refs/tags/v1.0.0"})
            with self.assertRaisesRegex(ValueError, "tag already exists"):
                publisher.publish()
            refs.clear()
            publish.assert_not_called()
            self.assertEqual(publisher.publish(), 9)
            self.assertEqual(publisher.publish(), 9)
            publish.assert_called_once()
            with patch.dict(record, {"immutable": False}), self.assertRaises(ValueError):
                publisher.publish()

    def test_scoped_token_and_revocation(self):
        account = {"id": 7}
        record = {"app_id": 13, "id": 44, "repository_selection": "selected", "account": account}
        scope = {"total_count": 1, "repositories": [{"id": 19}]}
        minted = {"permissions": {"contents": "write", "metadata": "read"}, "token": "fixture"}
        env = {"RELEASE_APP_ID": "13", "RELEASE_INSTALLATION_ID": "44", "PUBLISHER_SECRET_ARN": "fixture"}
        with patch.dict(os.environ, env), patch.object(subprocess, "check_output", return_value=b'"fixture"'), \
                patch.object(publisher, "app_jwt", return_value="jwt"):
            with patch.object(publisher, "github", side_effect=[record, minted, scope, None]) as api:
                with publisher.installation_token() as value:
                    self.assertEqual(value, "fixture")
                self.assertEqual(api.call_args_list[1].args, ("POST", "/app/installations/44/access_tokens", "jwt",
                                 {"repository_ids": [19], "permissions": {"contents": "write"}}))
                api.assert_called_with("DELETE", "/installation/token", "fixture")
            for responses in ([record | {"id": 20}], [record, minted | {"permissions": {"actions": "write"}}, None],
                              [record, minted, scope | {"total_count": 2}, None]):
                with self.subTest(), patch.object(publisher, "github", side_effect=responses), self.assertRaises(ValueError):
                    with publisher.installation_token():
                        self.fail("Unexpected token issued")

    def test_sign_jwt_with_ephemeral_test_key(self):
        pem = subprocess.check_output(["openssl", "genrsa", "2048"], stderr=subprocess.DEVNULL)
        pub = self.root / "test.pub"
        subprocess.run(["openssl", "rsa", "-pubout", "-out", str(pub)], input=pem,
                       stderr=subprocess.DEVNULL, check=True)
        head, claims, signed = publisher.app_jwt(pem, 13).split(".")
        signature = self.root / "signature"
        signature.write_bytes(base64.urlsafe_b64decode(signed + "=="))
        data = json.loads(base64.urlsafe_b64decode(claims + "=="))
        self.assertEqual(data["iss"], 13)
        self.assertEqual(data["exp"] - data["iat"], 360)
        subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(pub), "-signature", str(signature)],
                       input=f"{head}.{claims}".encode(), stdout=subprocess.PIPE, check=True)
