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

Commit the final YAML in a flat directory in the service repository. Validate its objects and references in service CI. The workflow reads those files at the caller revision and uploads one Actions artifact containing `oci-layout.tar`. It exposes `artifact-id`, `artifact-name`, and `package-digest`. The digest identifies the OCI manifest with one gzip tar layer containing the YAML.

A registry publisher can download that artifact by ID in the same run. With an already authorized destination and identity, copy the layout and compare the remote manifest digest to the package output.

```bash
oras cp --from-oci-layout oci-layout.tar:release "$REGISTRY_REPOSITORY:$VERSION"
test "$(oras resolve "$REGISTRY_REPOSITORY:$VERSION")" = "$PACKAGE_DIGEST"
```

## Cut a draft

Call `service-release-cut.yml` from a manual dispatch on protected `main`. Place it after the service has verified the image and package in its registry and run its release preview. The called workflow uses the caller environment `service-release-cut`. Restrict that environment to protected `main` and approved release operators. Configure the repository-scoped GitHub App installation there with variable `RELEASE_APP_ID` and environment secret `RELEASE_APP_PRIVATE_KEY`. Grant that installation Contents write on the service repository. Restrict who can modify release workflows and environment settings.

| Input | Contents |
| --- | --- |
| `version` | Stable `vMAJOR.MINOR.PATCH` version |
| `image-reference`, `package-reference` | Registry paths with full `@sha256:` digests |
| `evidence-artifact-id` | One artifact from the same run. At its root, place `release-manifest.json` with `provides`, `requires`, and `schema`, plus an SPDX or CycloneDX `sbom.json`. |

The cut creates a draft pinned to the dispatch commit. It attaches both input files, `release.json` with the source and digest references, and `SHA256SUMS`. Successful completion returns `release-id`. A retry can finish a partial draft at the same version and commit with the same bytes. Investigate any asset conflict. Publish only a complete, reviewed draft using the separately authorized release App.

## Verify a published candidate

Call `service-release-candidate.yml` from `on: { release: { types: [published] } }`. Set the service repository variable `RELEASE_APP_BOT_ID` to the numeric user ID of the release App's bot account. This differs from the App ID. The workflow reads the event and immutable release from GitHub. It checks the publisher, original stable publication, version tag, ancestry on `main`, and digest of the release metadata.

On success it returns `release-id`, `version`, `source-sha`, `image-reference`, and `package-reference`. A failed check stops the job. The caller can record a held-release reason on failure. Before writing desired state, its authorized promotion operation must still evaluate live dependency compatibility, schema rollback compatibility, the allowed resources, and build and registry provenance; verify the current channel generation; and bind its decision to these exact digests. Only the service's admitted writer can update that record. Verification and packaging run with read-only repository access.
