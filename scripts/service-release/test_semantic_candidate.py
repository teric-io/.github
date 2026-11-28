import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import semantic_candidate as candidate


class SemanticCandidateTests(unittest.TestCase):
    def test_classification(self):
        for title, body, expected in [
            ("feat(api): add batching", "", 2), ("fix: retry", "", 1),
            ("perf: cache lookup", "", 1), ("docs: describe API", "", 1),
            ("feat!: remove v1 API", "", 3), ("fix(api): reject legacy input",
             "Changes\n\nBREAKING CHANGE: replace old input\n\nMigration steps", 3),
            ("fix: docs", "```text\nBREAKING CHANGE: sample\n```", 1),
            ("docs: example", "<!-- BREAKING CHANGE: example -->", 1),
        ]:
            with self.subTest(title=title, body=body):
                self.assertEqual(candidate.bump(title, body), expected)
        with self.assertRaises(ValueError):
            candidate.bump("feaat: typo", "")

    def test_numbering(self):
        for changes, expected in [([1], "v1.9.11"), ([1, 2, 1], "v1.10.0"), ([2, 3, 1], "v2.0.0")]:
            self.assertEqual(candidate.next_version("v1.9.10", changes), expected)

    def test_data_from_exact_merge_and_history(self):
        repo = "teric-io/example"
        sha, prev, base = "a" * 40, "b" * 40, "c" * 40
        headers = {sha: "fix: validate input", prev: "feat(api): add input"}
        def api(path):
            if path.endswith("/heads/main"):
                return {"object": {"sha": sha}}
            if "/compare/" in path:
                return {"status": "ahead", "merge_base_commit": {"sha": base}}
            if "/releases/tags/" in path:
                return {"immutable": True, "draft": False, "prerelease": False, "tag_name": "v1.9.10"}
            rev = sha if sha in path else prev
            if "/git/commits/" in path:
                return {"sha": rev, "message": headers[rev] + " (#18)", "parents": [{"sha": prev if rev == sha else base}]}
            self.assertIn("/pulls?", path)
            return [{"merged_at": "2026-11-27", "merge_commit_sha": rev, "base": {"repo": {"full_name": repo}, "ref": "main"},
                     "number": 18, "title": "Delivery PR", "body": headers[rev] + "\n\nReason for change."}]

        with tempfile.TemporaryDirectory() as temp:
            event = Path(temp) / "event"
            inputs = {"publish_to_ecr": True, "image_publication_attempt": "attempt"}
            event.write_text(json.dumps({"inputs": inputs}))
            workflow = repo + "/.github/workflows/package.yml@refs/heads/main"
            env = {"GITHUB_REPOSITORY": repo, "GITHUB_SHA": sha, "GITHUB_REF": "refs/heads/main",
                   "GITHUB_EVENT_NAME":"workflow_dispatch", "GITHUB_REF_PROTECTED":"true", "GITHUB_EVENT_PATH": str(event),
                   "GITHUB_WORKFLOW_REF":workflow, "TRUSTED_CUT_WORKFLOW":workflow, "INITIAL_RELEASE_VERSION":"v1.0.0",
                   "GITHUB_RUN_ID":"456"}
            with patch.dict(os.environ, env), patch.object(candidate.release, "api", side_effect=api), patch.object(candidate.release, "gh") as tags:
                tags.return_value = b"[[]]"
                self.assertEqual(candidate.versions()["tag"], "v1.0.0-rc.18.456")
                tags.return_value = json.dumps([[{"name":"v1.9.10","commit":{"sha":base}},
                                                {"name":"v9.0.0-rc.1.2","commit":{"sha":prev}}]])
                self.assertEqual(candidate.versions()["tag"], "v1.10.0-rc.18.456")
                event.write_text(json.dumps({"inputs":dict(inputs, release_version="v99.0.0")}))
                with self.assertRaisesRegex(ValueError,"automatically assigned"):
                    candidate.versions()

    def test_merge_classification_must_match(self):
        repo, sha = "teric-io/example", "a"*40
        pr = {"base":{"ref":"main","repo":{"full_name":repo}},"merged_at":"today","merge_commit_sha":sha,
              "number":1,"title":"fix: old","body":"BREAKING CHANGE: edited"}
        commit = {"sha":sha, "parents":[{"sha":"b"*40}], "message":"fix: old (#1)"}
        with patch.object(candidate.release,"api",side_effect=[commit,[pr]]), self.assertRaisesRegex(ValueError,"immutable"):
            candidate.merged_change(repo,sha)

    def test_tag_only_and_replay(self):
        writes, ref, objects = [], {}, {}
        @contextmanager
        def installation():
            yield "test"
        def github(method, path, token, body=None):
            if "/matching-refs/" in path:
                return [ref] if ref else []
            if method == "GET":
                return objects[path.split("/")[-1]]
            writes.append((method,path))
            if path.endswith("/tags"):
                objects["abc"] = dict(body, object={"type":"commit","sha":body["object"]})
                return {"sha":"abc"}
            self.assertTrue(path.endswith("/refs"))
            ref.update(ref=body["ref"],object={"type":"tag","sha":body["sha"]})
        identity = {"version":"v2.0.0","tag":"v2.0.0-rc.42.123","pr":42}
        meta = {"repository":"teric-io/example","commit":"a"*40,"version":"v2.0.0","image":"image","package":"package"}
        proof = {"image_source_commit":"b"*40,"provenance_ref":"test"}
        with patch.dict(os.environ, GITHUB_RUN_ID="123",PACKAGE_ARTIFACT_ID="1",EVIDENCE_ARTIFACT_ID="2"), patch.object(
                candidate.publisher,"installation_token",installation), patch.object(candidate.publisher,"github",side_effect=github):
            candidate.write_tag(identity,meta,proof,"build")
            candidate.write_tag(identity,meta,proof,"build")
            self.assertEqual(len(writes),2)
            with self.assertRaisesRegex(ValueError,"different source or evidence"):
                candidate.write_tag(identity,dict(meta,package="other"),proof,"build")
            self.assertEqual(len(writes),2)

    def test_protected_build_must_pass_on_exact_source(self):
        repo, commit = "teric-io/example", "a"*40
        run = {"repository":{"full_name":repo},"head_repository":{"full_name":repo},"path":".github/workflows/ci.yml",
               "event":"push","head_branch":"main","head_sha":commit,"status":"completed","conclusion":"success","id":99,"run_attempt":1}
        with patch.dict(os.environ, RELEASE_CHECK_WORKFLOW="ci.yml"), patch.object(candidate.release,"api",return_value={"workflow_runs":[run],"total_count":1}):
            self.assertTrue(candidate.checked_build(repo,commit).endswith("/99/attempts/1"))
            for field, value in (("head_sha", "0"*40), ("conclusion","failure"), ("event","pull_request")):
                with patch.dict(run, {field:value}), self.assertRaises(ValueError):
                    candidate.checked_build(repo, commit)
