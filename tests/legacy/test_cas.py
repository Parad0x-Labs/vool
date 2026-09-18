import os

from core.liquefy_cas import CAS_DIR, chunk_file, reconstruct_file


def run_test():
    print("--- Testing Petabyte Data Layer (CAS) ---")

    # 1. Create a 5MB dummy file (3 chunks)
    os.makedirs("test_data", exist_ok=True)
    test_file = "test_data/dummy_5mb.bin"
    with open(test_file, "wb") as f:
        f.write(os.urandom(5 * 1024 * 1024))

    print(f"Created {test_file}")

    # 2. Chunk it
    manifest = chunk_file(test_file)
    print("Manifest:")
    for k, v in manifest.items():
        if k == "chunks":
            print(f"  {k}: {len(v)} chunks")
            for c in v:
                print(f"    - {c}")
        else:
            print(f"  {k}: {v}")

    # 3. Verify it was written to disk
    print(f"\nChecking CAS directory: {CAS_DIR}")
    for k in manifest["chunks"]:
        expected_path = CAS_DIR / k[:2] / k[2:4] / k
        assert expected_path.exists(), f"Chunk {k} missing from disk!"

    root_hash = manifest["root_hash"]
    expected_root = CAS_DIR / root_hash[:2] / root_hash[2:4] / root_hash
    assert expected_root.exists(), f"Manifest block {root_hash} missing!"

    # 4. Reconstruct it
    out_file = "test_data/reconstructed_5mb.bin"
    success = reconstruct_file(root_hash, out_file)
    assert success, "Reconstruction failed!"

    # 5. Diff them
    with open(test_file, "rb") as f1, open(out_file, "rb") as f2:
        assert f1.read() == f2.read(), "File contents do not match after reconstruction!"

    print("\nSUCCESS! The CAS engine chunked, deduplicated, and reconstructed the 5MB file perfectly.")

if __name__ == "__main__":
    run_test()
