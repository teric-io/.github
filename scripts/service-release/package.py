"""Build an OCI image layout containing rendered Kubernetes manifests."""
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path


def encode(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def add_file(archive, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))


def build(checkout, directory, revision, output):
    source = (checkout / directory).resolve()
    if not source.is_relative_to(checkout.resolve()) or not source.is_dir():
        raise ValueError("manifest-directory must be inside the caller checkout")
    files = sorted(source.iterdir())
    if not files:
        raise ValueError("manifest-directory is empty")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as layer:
        for path in files:
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in {".yaml", ".yml"}:
                raise ValueError("manifest-directory must contain only regular YAML files")
            add_file(layer, path.name, path.read_bytes())

    with tarfile.open(output, "w", format=tarfile.USTAR_FORMAT) as archive:
        def blob(media_type, data):
            digest = hashlib.sha256(data).hexdigest()
            add_file(archive, "blobs/sha256/" + digest, data)
            return {"mediaType": media_type, "size": len(data), "digest": "sha256:" + digest}

        config = blob("application/vnd.oci.empty.v1+json", b"{}")
        compressed = io.BytesIO()
        with gzip.GzipFile(fileobj=compressed, mode="wb", compresslevel=9, mtime=0) as zipped:
            zipped.write(buffer.getvalue())
        layer = blob("application/vnd.oci.image.layer.v1.tar+gzip", compressed.getvalue())
        media_type = "application/vnd.oci.image.manifest.v1+json"
        manifest = {"schemaVersion": 2, "mediaType": media_type, "config": config,
                    "layers": [layer], "annotations": {"org.opencontainers.image.revision": revision}}
        descriptor = blob(media_type, encode(manifest))
        descriptor["annotations"] = {"org.opencontainers.image.ref.name": "release"}
        add_file(archive, "oci-layout", encode({"imageLayoutVersion": "1.0.0"}))
        add_file(archive, "index.json", encode({"schemaVersion": 2, "manifests": [descriptor]}))
        return descriptor["digest"]


if __name__ == "__main__":
    import os

    digest = build(Path("caller"), os.environ["MANIFEST_DIRECTORY"], os.environ["GITHUB_SHA"],
                   Path(os.environ["RUNNER_TEMP"]) / "oci-layout.tar")
    with open(os.environ["GITHUB_OUTPUT"], "a") as outputs:
        print("digest=" + digest, file=outputs)
