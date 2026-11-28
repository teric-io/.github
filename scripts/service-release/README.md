# Service release workflows

These reusable workflows run on GitHub.com. Pin the workflow reference to a reviewed full commit SHA. Each workflow retrieves its implementation from the commit that defines the called job. Calls use the service repository as their release boundary.

## Package rendered manifests

```yaml
jobs:
  package:
    permissions:
      contents: read
    uses: teric-io/.github/.github/workflows/service-release-package.yml@<reviewed-commit-sha>
    with:
      manifest-directory: deploy/rendered
```

Commit the final YAML in a flat directory in the service repository. Validate its objects and references in service CI. The workflow reads those files at the caller revision and uploads one Actions artifact containing `oci-layout.tar`. It exposes `artifact-id`, `artifact-name`, and `package-digest`. The digest identifies the exact OCI image manifest. Its `artifactType` is `application/vnd.teric.kubernetes.manifests.v1`, the Teric package format for rendered Kubernetes YAML. The config is the OCI empty JSON object and the single `application/vnd.oci.image.layer.v1.tar+gzip` layer contains the YAML files. Rebuilding a package made before the artifact type was added produces a new manifest digest. Verify and promote the digest returned by the current build. [OCI image manifest specification](https://github.com/opencontainers/image-spec/blob/v1.1.1/manifest.md#image-manifest-property-descriptions) · [Argo CD OCI artifact format](https://argo-cd.readthedocs.io/en/stable/user-guide/oci/#usage-guidelines)

A registry publisher can download that artifact by ID in the same run. With an already authorized destination and identity, copy the layout and compare the remote manifest digest to the package output.

```bash
oras cp --from-oci-layout oci-layout.tar:release "$REGISTRY_REPOSITORY:$VERSION"
test "$(oras resolve "$REGISTRY_REPOSITORY:$VERSION")" = "$PACKAGE_DIGEST"
```

## Cut a draft

Call `service-release-cut.yml` from a manual dispatch on protected `main`. Place it after the service has verified the image and package in its registry and run its release preview. The called workflow uses the caller environment `service-release-cut`. Restrict that environment to protected `main` and approved release operators. Configure the repository-scoped GitHub App installation there with variable `RELEASE_APP_ID` and environment secret `RELEASE_APP_PRIVATE_KEY`. Grant that installation Contents write on the service repository. Restrict who can modify release workflows and environment settings. In that environment set `RELEASE_CUT_WORKFLOW_REF` to the approved caller (for example `owner/repo/.github/workflows/cut.yml@refs/heads/main`). Set `RELEASE_IMAGE_REPOSITORY` and `RELEASE_PACKAGE_REPOSITORY` to the full approved registry repository names, without tags or digests. The caller grants its ambient token Actions read and Contents read. Pass only the named environment secret from the caller's reusable-workflow job:

```yaml
jobs:
  cut:
    needs: [package, verify-publication]
    uses: teric-io/.github/.github/workflows/service-release-cut.yml@<reviewed-commit-sha>
    permissions:
      contents: read
      actions: read
    secrets:
      RELEASE_APP_PRIVATE_KEY: ${{ secrets.RELEASE_APP_PRIVATE_KEY }}
    with:
      version: ${{ inputs.version }}
      package-artifact-id: ${{ needs.package.outputs.artifact-id }}
      evidence-artifact-id: ${{ needs.verify-publication.outputs.artifact-id }}
      package-reference: ${{ needs.verify-publication.outputs.package-reference }}
      image-reference: ${{ needs.verify-publication.outputs.image-reference }}
```

Keep that key solely in the protected environment. The called job selects the environment. GitHub requires the caller to pass the name even when only the environment contains its value. [GitHub reusable workflow secret rules](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows#using-inputs-and-secrets-in-a-reusable-workflow)

| Input | Contents |
| --- | --- |
| `version` | Stable `vMAJOR.MINOR.PATCH` version |
| `image-reference`, `package-reference` | Registry paths with full `@sha256:` digests |
| `package-artifact-id` | The package workflow's `artifact-id` from the same run |
| `evidence-artifact-id` | ID of the protected verification job's artifact named `service-release-proof-<run-id>` |

The service-owned verification job reads each manifest back from the authorized registry by digest. Save the exact returned bytes (including the absence of a trailing newline) as `image-oci.json` and `package-oci.json`. Verify the build provenance for those references using the service's approved verifier. After that succeeds, generate `registry-proof.json` from the observed results. Include these fields:

- `repository`, `version`, `commit`, `image` and `package` identify the caller, release version, source commit and verified registry references.
- `workflow`, `run_id`, `run_attempt`, and `package_artifact_id` are the GitHub workflow ref, run ID, attempt, and immutable package artifact ID, all strings.
- `provenance_verified` is Boolean true and `provenance_ref` identifies the immutable evidence the verifier accepted.

In the same artifact, include `release-manifest.json` and an SPDX or CycloneDX `sbom.json`. In the manifest, list each contract the service provides in `provides` with its `contract` name and `serves` version or range. List dependencies in `requires` with their `contract` name and `range`. Use an empty array for either list when appropriate. A stateless service uses `"schema": {}`. A service with a schema supplies its revision in `schema.head` and the earliest compatible stable release in `schema.oldest_binary` (for example, `v1.4.0`). Both draft preparation and published-candidate verification check these field types.

Create the proof artifact only in the reviewed publisher/verifier job. Use its upload action's returned ID directly as the cut input. Do not accept operator-supplied receipt bytes. The reusable workflow checks the run, source, evidence and package blobs before obtaining the App token. Each adopter must demonstrate the protected caller, least-privilege App installation, registry readback and provenance verifier with its owners.

The cut creates a draft pinned to the dispatch commit. It attaches the service contract, SBOM, stable registry proof, `release.json` with the source and digest references, and `SHA256SUMS`. Successful completion returns `release-id`. A retry can finish a partial draft at the same version and commit with the same bytes. Investigate any asset conflict. Publish only a complete, reviewed draft using the separately authorized release App.

## Publish a delegated release

Use `service-release-publish.yml` for a service that has admitted an independent release trigger. Call it from the protected package workflow after verification, with the five inputs listed under “Cut a draft”. Grant the job `contents: read`, `actions: read`, and `id-token: write`. Pin the shared workflow to a reviewed commit. Each delegated release adds one Ubuntu job capped at ten minutes.

The original dispatch must set `publish_to_ecr`, `release_version` and a `release_request` JSON string. Its fields are `instruction_ts`, `version`, `source`, `image_source`, `image` and `package`. The trusted trigger independently verifies the human instruction and the reviewed candidate before dispatch. The service verifier includes the same request, its verified `image_source_commit` and the numeric trigger actor ID as a string in `release_trigger_actor_id` in the same-run registry proof. The publisher validates the original event, actor, workflow, version, source and digests before requesting signer access.

The credential owner sets the following values in the service's protected `service-release-cut` environment, in addition to the cut workflow and registry repository variables.

| Variables | Owner action |
| --- | --- |
| `RELEASE_TRIGGER_ACTOR_ID`, `RELEASE_TRIGGER_LOGIN` | Identify the dedicated workflow trigger App's bot account. |
| `RELEASE_APP_ID`, `RELEASE_INSTALLATION_ID`, `RELEASE_APP_BOT_ID` | Identify the separate publisher App, its service-only installation and bot account. |
| `RELEASE_PUBLISHER_ROLE_ARN`, `RELEASE_PUBLISHER_REGION`, `RELEASE_PUBLISHER_SECRET_ARN` | Select the private AWS signing path. Store the App's PEM as the secret string. |
| `RELEASE_HANDOFF_ENABLED` | Set to `true` only after verifying the previous writer has stopped and its pending updates are reconciled. Clear it to stop further publications. |

The platform owner must bind the signing role's GitHub OIDC trust to the exact shared `job_workflow_ref` including its commit SHA, service repository and owner IDs, trigger actor ID, protected main ref, cut environment, subject and STS audience. The role needs only `secretsmanager:GetSecretValue` on that single secret when using the AWS-managed encryption key. Test denial for the coding identity and an unrelated workflow before provisioning the private key. Requalify the trust binding when changing the shared workflow pin. [AWS GitHub OIDC condition keys](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_iam-condition-keys.html)

The publisher requests a short-lived installation token for the one service, checks its permissions, uploads the validated assets, and publishes the matching draft. It then verifies the immutable release, asset digests and tag commit, and revokes the token. An uncertain publication response triggers a readback. Reconcile an uncertain dispatch or publication by release ID, tag, source and asset digests before authorizing another request. The credential owner can independently suspend the publisher installation or revoke access to the signing role.

## Verify a published candidate

Call `service-release-candidate.yml` from `on: { release: { types: [published] } }`. Set the service repository variable `RELEASE_APP_BOT_ID` to the numeric user ID of the release App's bot account. This differs from the App ID. The workflow reads the event and immutable release from GitHub. It checks the publisher, original stable publication, version tag, ancestry on `main`, and the exact release asset set, and each asset digest against the release checksum inventory.

On success it returns `release-id`, `version`, `source-sha`, `image-reference`, and `package-reference`. A failed check stops the job. The caller can record a held-release reason on failure. Before writing desired state, its authorized promotion operation must still evaluate live dependency compatibility, schema rollback compatibility, the allowed resources, and build and registry provenance; verify the current channel generation; and bind its decision to these exact digests. Only the service's admitted writer can update that record. Verification and packaging run with read-only repository access.

## Assign an immutable build candidate

An adopting service calls the read-only `.github/actions/service-release-version` action before it compiles release evidence. The first line of the PR description, or the PR title, supplies the Angular header. Preserve its change type and breaking status in the protected squash commit. A breaking footer starts on its own line with `BREAKING CHANGE: ` followed by a description. A scope is optional.

| Reviewed PR description | Next stable version after v1.9.10 |
| --- | --- |
| `feat(api)!: remove the legacy request` or a breaking footer | v2.0.0 |
| `feat(api): add batch requests` | v1.10.0 |
| `fix(api): reject an empty input` | v1.9.11 |

The largest change across all unreleased squash merges determines the version. Maintenance types such as `perf`, `docs`, `ci`, and `test` count as patches. A malformed description or a classification that differs from the protected commit stops the build. Before the first stable tag the service supplies its initial release version. Each new Actions run gets its own `vMAJOR.MINOR.PATCH-rc.PR.RUN_ID` tag.

Call `service-release-tag.yml` with the same initial version, the computed stable version, the protected CI workflow filename and both verified artifact IDs and digest references. Pin both shared entry points to the same reviewed revision. Set the approved package caller in `RELEASE_CUT_WORKFLOW_REF`. The tag step requires the `service-release-candidate-tag` environment, its exact OIDC workflow pin, the repository-scoped publisher and `RELEASE_CANDIDATE_TAG_ENABLED=true`. The tag annotation records the merged commit, PR, image source, artifact digests and IDs, CI attempt and provenance. An identical retry reads back the tag. A conflicting record stops. The separate delegated publisher owns stable publication.
