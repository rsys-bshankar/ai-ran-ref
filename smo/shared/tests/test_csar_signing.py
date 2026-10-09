"""PR-RAPP-1: digest list, detached ed25519 signature, trust store and verification of a CSAR."""

import io
import json
import zipfile

import pytest

from smo_shared import csar_signing as cs

FILES = {
    "TOSCA-Metadata/TOSCA.meta": b"Entry-Definitions: Definitions/asd.yaml\n",
    "Definitions/asd.yaml": b"application_name: Demo\n",
    "manifest.yaml": b"rappManifest: {manifestVersion: '1.0'}\n",
}


def build(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return out.getvalue()


def keypair(tmp_path, name):
    private_pem, public_pem = cs.generate_keypair()
    (tmp_path / f"{name}.pub").write_bytes(public_pem)
    return cs.load_private_key(private_pem)


@pytest.fixture
def key(tmp_path):
    return keypair(tmp_path, "acme")


@pytest.fixture
def trust(tmp_path, key):
    return cs.load_trust_store(tmp_path)


def signed(key, files=FILES) -> bytes:
    return cs.sign_csar(build(files), key)


def rewrite(data: bytes, change) -> bytes:
    """The package with `change(files)` applied to its entries (a dict name -> bytes, edited in place)."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        files = {i.filename: z.read(i.filename) for i in z.infolist()}
    change(files)
    return build(files)


def code_of(data, trust) -> str:
    with pytest.raises(cs.SignatureError) as excinfo:
        cs.verify_csar(data, trust)
    return excinfo.value.code


def test_the_digest_list_names_every_file_sorted_and_never_itself():
    listing = cs.digest_list({**FILES, cs.DIGEST_FILE: b"x", cs.SIGNATURE_FILE: b"y"}).decode()
    assert [line.split("  ", 1)[1] for line in listing.splitlines()] == sorted(FILES)
    assert listing.splitlines()[0] == "%s  Definitions/asd.yaml" % __import__("hashlib").sha256(FILES["Definitions/asd.yaml"]).hexdigest()


def test_a_signed_package_verifies_and_names_the_publisher_the_operator_gave_the_key(key, trust):
    result = cs.verify_csar(signed(key), trust)
    assert result.publisher == "acme" and result.files == len(FILES) and result.key_id == cs.key_id(key.public_key())


def test_signing_is_repeatable_so_a_rebuilt_package_is_byte_identical(key):
    assert signed(key) == signed(key)


def test_re_signing_replaces_the_earlier_signature_instead_of_stacking_entries(key, tmp_path):
    other = keypair(tmp_path, "other")
    twice = cs.sign_csar(signed(key), other)
    with zipfile.ZipFile(io.BytesIO(twice)) as z:
        assert z.namelist().count(cs.SIGNATURE_FILE) == 1
    assert cs.verify_csar(twice, cs.load_trust_store(tmp_path)).publisher == "other"


def test_a_modified_file_is_rejected_and_named(key, trust):
    data = rewrite(signed(key), lambda f: f.__setitem__("manifest.yaml", b"tampered\n"))
    with pytest.raises(cs.SignatureError, match="manifest.yaml does not match its signed digest") as excinfo:
        cs.verify_csar(data, trust)
    assert excinfo.value.code == "modified"


def test_an_added_file_is_rejected_and_named(key, trust):
    data = rewrite(signed(key), lambda f: f.__setitem__("evil.py", b"import os\n"))
    with pytest.raises(cs.SignatureError, match="evil.py is not covered") as excinfo:
        cs.verify_csar(data, trust)
    assert excinfo.value.code == "added"


def test_a_removed_file_is_rejected_and_named(key, trust):
    data = rewrite(signed(key), lambda f: f.pop("manifest.yaml"))
    with pytest.raises(cs.SignatureError, match="manifest.yaml is in the signed digest list but not in the package") as excinfo:
        cs.verify_csar(data, trust)
    assert excinfo.value.code == "missing"


def test_a_rewritten_digest_list_is_caught_by_the_signature(key, trust):
    def forge(files):
        files[cs.DIGEST_FILE] = cs.digest_list({**files, "manifest.yaml": b"tampered\n"})
        files["manifest.yaml"] = b"tampered\n"
    assert code_of(rewrite(signed(key), forge), trust) == "bad_signature"


def test_a_signature_made_with_another_key_under_a_trusted_key_id_is_rejected(key, trust, tmp_path):
    impostor = cs.load_private_key(cs.generate_keypair()[0])

    def swap(files):
        document = json.loads(files[cs.SIGNATURE_FILE])
        document["signature"] = json.loads(cs.sign_digests(files[cs.DIGEST_FILE], impostor))["signature"]
        files[cs.SIGNATURE_FILE] = json.dumps(document).encode()
    with pytest.raises(cs.SignatureError, match="publisher acme") as excinfo:
        cs.verify_csar(rewrite(signed(key), swap), trust)
    assert excinfo.value.code == "bad_signature"


def test_a_package_signed_by_a_key_nobody_trusts_is_an_unknown_publisher(trust):
    stranger = cs.load_private_key(cs.generate_keypair()[0])
    with pytest.raises(cs.SignatureError, match="unknown publisher") as excinfo:
        cs.verify_csar(signed(stranger), trust)
    assert excinfo.value.code == "unknown_publisher"
    assert "BEGIN" not in str(excinfo.value)


def test_a_package_that_says_who_it_is_does_not_get_that_name(key, trust):
    data = rewrite(signed(key), lambda f: None)
    assert cs.verify_csar(data, trust).publisher == "acme"        # the name is the trust store's, never a field of the package


def test_an_unsigned_package_is_reported_as_unsigned(trust):
    assert code_of(build(FILES), trust) == "unsigned"


def test_a_digest_list_without_a_signature_is_unsigned_and_a_signature_without_a_list_is_malformed(key, trust):
    data = signed(key)
    assert code_of(rewrite(data, lambda f: f.pop(cs.SIGNATURE_FILE)), trust) == "unsigned"
    assert code_of(rewrite(data, lambda f: f.pop(cs.DIGEST_FILE)), trust) == "malformed"


@pytest.mark.parametrize("signature", [b"not json", b"[]", b'{"version": 2, "algorithm": "ed25519", "keyId": "a", "signature": "AA=="}',
                                       b'{"version": 1, "algorithm": "rsa", "keyId": "a", "signature": "AA=="}',
                                       b'{"version": 1, "algorithm": "ed25519", "keyId": 1, "signature": "AA=="}',
                                       b'{"version": 1, "algorithm": "ed25519", "keyId": "a", "signature": "***"}'])
def test_a_malformed_signature_entry_is_rejected_not_crashed_on(key, trust, signature):
    assert code_of(rewrite(signed(key), lambda f: f.__setitem__(cs.SIGNATURE_FILE, signature)), trust) == "malformed"


@pytest.mark.parametrize("digests", [b"nonsense\n", b"\xff\xfe", ("0" * 64 + "  a\n" + "0" * 64 + "  a\n").encode()])
def test_a_malformed_digest_list_is_rejected(key, tmp_path, digests):
    private = key
    trust = cs.load_trust_store(tmp_path)

    def forge(files):
        files[cs.DIGEST_FILE] = digests
        files[cs.SIGNATURE_FILE] = cs.sign_digests(digests, private)         # validly signed, so only the format can fail
    assert code_of(rewrite(signed(key), forge), trust) == "malformed"


def test_a_package_with_a_file_twice_is_rejected(key, trust):
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(signed(key))) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            target.writestr(info.filename, source.read(info.filename))
        with pytest.warns(UserWarning, match="Duplicate name"):
            target.writestr("manifest.yaml", b"second one\n")
    assert code_of(out.getvalue(), trust) == "malformed"


@pytest.mark.parametrize("name", ["../escape.py", "/abs.py", "a\\b.py"])
def test_a_file_with_an_unsafe_path_is_rejected(key, trust, name):
    assert code_of(rewrite(signed(key), lambda f: f.__setitem__(name, b"x")), trust) == "malformed"


def test_a_file_that_is_not_a_zip_is_malformed(trust):
    assert code_of(b"not a zip", trust) == "malformed"


def test_sign_csar_refuses_a_package_with_a_file_twice(key):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("a", b"1")
        with pytest.warns(UserWarning, match="Duplicate name"):
            z.writestr("a", b"2")
    with pytest.raises(cs.SignatureError):
        cs.sign_csar(out.getvalue(), key)


def test_directory_entries_are_ignored_by_the_digest_check(key, trust):
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(signed(key))) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            target.writestr(info.filename, source.read(info.filename))
        target.writestr("Artifacts/", b"")
    assert cs.verify_csar(out.getvalue(), trust).files == len(FILES)


# -------------------------------------------------------------------------------------------------------------------------- the trust store

def test_a_directory_is_read_for_pub_and_pem_files_and_skips_the_rest(tmp_path):
    _, a = cs.generate_keypair()
    _, b = cs.generate_keypair()
    (tmp_path / "acme.pub").write_bytes(a)
    (tmp_path / "vendor.pem").write_bytes(b)
    (tmp_path / "README.txt").write_text("not a key")
    (tmp_path / ".hidden.pub").write_text("kubernetes data dirs and dotfiles are skipped")
    (tmp_path / "sub.pub").mkdir()
    store = cs.load_trust_store(tmp_path)
    assert sorted(k.publisher for k in store.keys) == ["acme", "vendor"] and bool(store)


def test_a_file_may_hold_several_keys_named_after_the_file(tmp_path):
    (tmp_path / "acme.pub").write_bytes(cs.generate_keypair()[1] + cs.generate_keypair()[1])
    assert [k.publisher for k in cs.load_trust_store(tmp_path / "acme.pub").keys] == ["acme", "acme#2"]


def test_a_symlinked_key_is_followed_like_a_mounted_configmap(tmp_path):
    data = tmp_path / "..data"
    data.mkdir()
    (data / "acme.pub").write_bytes(cs.generate_keypair()[1])
    (tmp_path / "acme.pub").symlink_to(data / "acme.pub")
    assert [k.publisher for k in cs.load_trust_store(tmp_path).keys] == ["acme"]


@pytest.mark.parametrize("where", ["missing", "empty", "not-a-key", "wrong-type", "unreadable"])
def test_an_unusable_trust_store_is_an_error_not_a_smaller_store(tmp_path, where):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    if where == "missing":
        target = tmp_path / "nope"
    elif where == "empty":
        target = tmp_path
    elif where == "not-a-key":
        (tmp_path / "x.pub").write_text("hello")
        target = tmp_path
    elif where == "wrong-type":
        pem = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        (tmp_path / "x.pub").write_bytes(pem)
        target = tmp_path
    else:
        (tmp_path / "x.pub").symlink_to(tmp_path / "gone")
        target = tmp_path
    with pytest.raises(cs.TrustStoreError) as excinfo:
        cs.load_trust_store(target)
    assert "PRIVATE" not in str(excinfo.value)


def test_a_key_that_is_not_ed25519_or_not_pem_is_refused_when_loaded():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    ec_private = ec.generate_private_key(ec.SECP256R1())
    pem = ec_private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    with pytest.raises(ValueError, match="ed25519"):
        cs.load_private_key(pem)
    with pytest.raises(ValueError, match="PEM private key"):
        cs.load_private_key(b"nope")
    with pytest.raises(ValueError, match="PEM public key"):
        cs.load_public_key(b"nope")
    with pytest.raises(ValueError, match="ed25519 public key"):
        cs.load_public_key(ec_private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
