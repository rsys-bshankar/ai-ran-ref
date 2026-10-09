"""PR-RAPP-1: the sample packages are signed with the demo key, the CLI works, and the demo key is the only private key in the repository."""

import importlib.util
import io
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SMO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SMO_ROOT / "shared"))

from smo_shared import csar_signing as cs  # noqa: E402

SAMPLES = SMO_ROOT / "samples"
DEMO_DIR = SAMPLES / "demo-signing"
CLI = SMO_ROOT / "scripts" / "csar_sign.py"
COMMITTED = sorted(SAMPLES.glob("*.csar"))


def _builder():
    spec = importlib.util.spec_from_file_location("build_csar", SAMPLES / "build_csar.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True, cwd=cwd)


def test_there_are_four_committed_sample_packages():
    assert [c.name for c in COMMITTED] == ["coverage-optimization-rapp.csar", "energy-saving-rapp.csar", "mobility-optimization-rapp.csar",
                                           "traffic-steering-rapp.csar"]


@pytest.mark.parametrize("csar", COMMITTED, ids=lambda p: p.stem)
def test_each_committed_package_is_signed_and_equals_a_rebuild_from_its_sources(csar):
    assert _builder().build_bytes(csar.stem) == csar.read_bytes(), f"rebuild: python3 samples/build_csar.py {csar.stem}"
    verified = cs.verify_csar(csar.read_bytes(), cs.load_trust_store(DEMO_DIR))
    assert verified.publisher == "demo-publisher"
    with zipfile.ZipFile(csar) as z:
        assert cs.DIGEST_FILE in z.namelist() and cs.SIGNATURE_FILE in z.namelist()


def test_a_sample_is_rejected_by_a_trust_store_that_does_not_hold_the_demo_key(tmp_path):
    (tmp_path / "someone-else.pub").write_bytes(cs.generate_keypair()[1])
    with pytest.raises(cs.SignatureError, match="unknown publisher"):
        cs.verify_csar(COMMITTED[0].read_bytes(), cs.load_trust_store(tmp_path))


def test_an_unsigned_build_has_neither_entry_and_a_build_with_another_key_names_that_key(tmp_path):
    builder = _builder()
    with zipfile.ZipFile(io.BytesIO(builder.build_bytes("energy-saving-rapp", sign=False))) as z:
        assert not cs.is_signed(z.namelist())
    private_pem, public_pem = cs.generate_keypair()
    key = tmp_path / "mine.key.pem"
    key.write_bytes(private_pem)
    (tmp_path / "trust").mkdir()
    (tmp_path / "trust" / "mine.pub").write_bytes(public_pem)
    mine = builder.build_bytes("energy-saving-rapp", key)
    assert cs.verify_csar(mine, cs.load_trust_store(tmp_path / "trust")).publisher == "mine"
    assert mine != COMMITTED[1].read_bytes()


# ------------------------------------------------------------------------------------------------------------------------------ the CLI

def test_the_cli_verifies_the_samples_against_the_demo_trust_store():
    done = cli("verify", *map(str, COMMITTED), "--trust", str(DEMO_DIR))
    assert done.returncode == 0, done.stderr
    assert done.stdout.count("OK ") == 4 and "signed by demo-publisher" in done.stdout


def test_the_cli_round_trip_keygen_sign_verify_and_a_tampered_copy_is_rejected(tmp_path):
    prefix = str(tmp_path / "me")
    assert cli("keygen", "--out", prefix).returncode == 0
    assert (tmp_path / "me.key.pem").stat().st_mode & 0o077 == 0                  # the private key is for its owner only
    assert cli("keygen", "--out", prefix).returncode == 2                         # never overwrites a key
    unsigned = tmp_path / "pkg.csar"
    unsigned.write_bytes(_builder().build_bytes("energy-saving-rapp", sign=False))
    assert cli("verify", str(unsigned), "--trust", prefix + ".pub").returncode == 1
    assert "[unsigned]" in cli("verify", str(unsigned), "--trust", prefix + ".pub").stdout
    signed = tmp_path / "signed.csar"
    done = cli("sign", str(unsigned), "--key", prefix + ".key.pem", "--out", str(signed))
    assert done.returncode == 0, done.stderr
    ok = cli("verify", str(signed), "--trust", prefix + ".pub")
    assert ok.returncode == 0 and "signed by me" in ok.stdout
    with zipfile.ZipFile(signed) as z:
        files = {i.filename: z.read(i.filename) for i in z.infolist()}
    files["manifest.yaml"] += b"# changed\n"
    bad = tmp_path / "bad.csar"
    with zipfile.ZipFile(bad, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    rejected = cli("verify", str(bad), "--trust", prefix + ".pub")
    assert rejected.returncode == 1 and "REJECTED" in rejected.stdout and "manifest.yaml" in rejected.stdout and "[modified]" in rejected.stdout


def test_the_cli_signs_in_place_when_no_out_is_given_and_digests_lists_the_files(tmp_path):
    prefix = str(tmp_path / "me")
    cli("keygen", "--out", prefix)
    package = tmp_path / "pkg.csar"
    package.write_bytes(_builder().build_bytes("traffic-steering-rapp", sign=False))
    assert "signed: no" in cli("digests", str(package)).stdout
    assert cli("sign", str(package), "--key", prefix + ".key.pem").returncode == 0
    listing = cli("digests", str(package)).stdout
    assert "signed: yes" in listing and re.search(r"^[0-9a-f]{64}  manifest\.yaml$", listing, re.M)
    assert cs.DIGEST_FILE not in listing


def test_the_cli_reports_usage_and_file_problems_with_status_2_and_no_traceback(tmp_path):
    for args in (("verify", str(tmp_path / "missing.csar"), "--trust", str(DEMO_DIR)),
                 ("verify", str(COMMITTED[0]), "--trust", str(tmp_path / "no-such-store")),
                 ("sign", str(COMMITTED[0]), "--key", str(tmp_path / "nokey")),
                 ("digests", str(tmp_path / "missing.csar"))):
        done = cli(*args)
        assert done.returncode == 2 and done.stderr.startswith("error: ") and "Traceback" not in done.stderr, args
    not_a_key = tmp_path / "junk"
    not_a_key.write_text("junk")
    done = cli("sign", str(COMMITTED[0]), "--key", str(not_a_key))
    assert done.returncode == 2 and "junk" not in done.stderr.replace(str(not_a_key), "")
    not_a_zip = tmp_path / "x.csar"
    not_a_zip.write_bytes(b"nope")
    assert cli("digests", str(not_a_zip)).returncode == 2


# --------------------------------------------------------------------------------------------------------------------- the one private key

def test_the_demo_private_key_is_the_only_private_key_in_the_repository():
    seed_line = re.compile(r"ed25519-seed:[A-Za-z0-9+/]{43}=")
    pem = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
    holders = []
    for path in SMO_ROOT.rglob("*"):
        if not path.is_file() or {"node_modules", ".git", "__pycache__", ".pytest_cache"} & set(path.relative_to(SMO_ROOT).parts) or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if pem.search(text) or seed_line.search(text):
            holders.append(path.relative_to(SMO_ROOT).as_posix())
    assert holders == ["samples/demo-signing/demo-publisher.seed"]


def test_the_demo_key_files_say_what_they_are():
    seed = (DEMO_DIR / "demo-publisher.seed").read_text()
    assert seed.startswith("# DEMO MATERIAL") and "NEVER" in seed
    assert "NEVER" in (DEMO_DIR / "demo-publisher.pub").read_text()
    assert "never" in (DEMO_DIR / "README.md").read_text().lower()
    seed_key = cs.load_private_key(seed.encode())
    assert cs.key_id(seed_key.public_key()) == cs.load_trust_store(DEMO_DIR).keys[0].key_id
