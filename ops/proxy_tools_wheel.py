"""Deterministic proxy-tools wheel recipe shared with the Intel release lane."""
import base64
import csv
import hashlib
import io
import os
import stat
import sys
import zipfile

NAME = "proxy_tools"            # distribution name as spelled in the sdist's PKG-INFO
VERSION = "0.1.0"
PACKAGES = ["proxy_tools"]      # from setup.py: packages=['proxy_tools']
TAG = "py3-none-any"
DIST_INFO = "%s-%s.dist-info" % (NAME, VERSION)
WHEEL_NAME = "%s-%s-%s.whl" % (NAME, VERSION, TAG)

FIXED_DATE = (1980, 1, 1, 0, 0, 0)
FILE_MODE = 0o644
COMPRESS_LEVEL = 9
LICENSE_CANDIDATES = ("LICENSE", "LICENSE.txt", "LICENSE.rst", "LICENSE.md",
                      "LICENCE", "COPYING", "COPYING.txt")
# Placeholder values old setuptools wrote for unset fields; dropped from METADATA.
PLACEHOLDER = "UNKNOWN"


def die(msg):
    sys.exit("build_wheel: error: " + msg)


def read_regular(path):
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode):
        die("not a regular file: %s" % path)
    with open(path, "rb") as fh:
        return fh.read()


def parse_pkg_info(data):
    """Parse RFC 822 style PKG-INFO headers into an ordered list of (field, value)."""
    text = data.decode("utf-8")
    fields = []
    for line in text.split("\n"):
        if line == "":
            continue
        if line[0] in " \t":
            if not fields:
                die("PKG-INFO starts with a continuation line")
            k, v = fields[-1]
            fields[-1] = (k, v + "\n" + line)
            continue
        if ": " not in line and not line.endswith(":"):
            die("unparseable PKG-INFO line: %r" % line)
        k, _, v = line.partition(":")
        fields.append((k.strip(), v.strip()))
    return fields


def build_metadata(pkg_info_fields):
    get = dict(pkg_info_fields)
    if get.get("Name") != NAME or get.get("Version") != VERSION:
        die("PKG-INFO name/version mismatch: %r %r" % (get.get("Name"), get.get("Version")))
    out = ["Metadata-Version: 2.1"]
    for k, v in pkg_info_fields:
        if k == "Metadata-Version":
            continue
        if v == PLACEHOLDER and k in ("Description", "Platform", "Home-page",
                                      "Author", "Author-email", "License",
                                      "Summary", "Keywords"):
            continue
        if k == "Description":
            die("non-placeholder Description in PKG-INFO; handle it as a body")
        out.append("%s: %s" % (k, v))
    return ("\n".join(out) + "\n").encode("utf-8")


def collect_package_files(root):
    files = {}
    for pkg in PACKAGES:
        pkg_dir = os.path.join(root, *pkg.split("."))
        if not os.path.isdir(pkg_dir) or os.path.islink(pkg_dir):
            die("package directory missing: %s" % pkg_dir)
        if not os.path.isfile(os.path.join(pkg_dir, "__init__.py")):
            die("package has no __init__.py: %s" % pkg_dir)
        for name in sorted(os.listdir(pkg_dir)):
            path = os.path.join(pkg_dir, name)
            if os.path.isdir(path):
                # sub-packages must be listed explicitly in PACKAGES
                if "%s.%s" % (pkg, name) not in PACKAGES:
                    die("unexpected subdirectory in package: %s" % path)
                continue
            if not name.endswith(".py"):
                die("unexpected non-.py file in package: %s" % path)
            arc = "/".join([*pkg.split("."), name])
            files[arc] = read_regular(path)
    return files


def record_hash(data):
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def zip_info(arcname):
    zi = zipfile.ZipInfo(arcname, date_time=FIXED_DATE)
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.create_system = 3  # Unix, so external_attr carries the POSIX mode
    zi.external_attr = (stat.S_IFREG | FILE_MODE) << 16
    return zi


def main(argv):
    if len(argv) != 3:
        die("usage: build_wheel.py <extracted-sdist-root> <output-dir>")
    root, out_dir = argv[1], argv[2]

    pkg_info = read_regular(os.path.join(root, "PKG-INFO"))
    metadata = build_metadata(parse_pkg_info(pkg_info))

    payload = collect_package_files(root)

    top_level = ("\n".join(sorted({p.split(".")[0] for p in PACKAGES})) + "\n").encode("ascii")
    egg_top = os.path.join(root, "%s.egg-info" % NAME, "top_level.txt")
    if os.path.exists(egg_top) and read_regular(egg_top) != top_level:
        die("top_level.txt disagrees with sdist egg-info/top_level.txt")

    wheel_file = (
        "Wheel-Version: 1.0\n"
        "Generator: hand-assembled from sdist\n"
        "Root-Is-Purelib: true\n"
        "Tag: %s\n" % TAG
    ).encode("ascii")

    dist_info = {
        DIST_INFO + "/METADATA": metadata,
        DIST_INFO + "/WHEEL": wheel_file,
        DIST_INFO + "/top_level.txt": top_level,
    }
    for cand in LICENSE_CANDIDATES:
        p = os.path.join(root, cand)
        if os.path.lexists(p):
            dist_info[DIST_INFO + "/" + cand] = read_regular(p)

    ordered = [(k, payload[k]) for k in sorted(payload)]
    ordered += [(k, dist_info[k]) for k in sorted(dist_info)]

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for arc, data in ordered:
        w.writerow([arc, record_hash(data), str(len(data))])
    record_name = DIST_INFO + "/RECORD"
    w.writerow([record_name, "", ""])
    record = buf.getvalue().encode("utf-8")
    ordered.append((record_name, record))

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, WHEEL_NAME)
    if os.path.lexists(out_path):
        die("refusing to overwrite existing %s" % out_path)
    tmp_path = out_path + ".part"
    with open(tmp_path, "xb") as fh:
        with zipfile.ZipFile(fh, "w") as zf:
            for arc, data in ordered:
                zf.writestr(zip_info(arc), data, compress_type=zipfile.ZIP_DEFLATED,
                            compresslevel=COMPRESS_LEVEL)
    os.replace(tmp_path, out_path)

    with open(out_path, "rb") as fh:
        whl = fh.read()
    sys.stdout.write(record.decode("utf-8"))
    print("wrote %s  size=%d  sha256=%s" % (WHEEL_NAME, len(whl), hashlib.sha256(whl).hexdigest()))


if __name__ == "__main__":
    main(sys.argv)
