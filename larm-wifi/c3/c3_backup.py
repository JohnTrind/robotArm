"""Full filesystem backup of the CyberBrick C3 over raw REPL (one connection).

Reads every file as hex (binary-safe) and mirrors it under a local dir.
Usage: python c3_backup.py COM18 ./backup_cyberbrick
"""
import sys
import os
import binascii
import c3_raw


def list_files(s):
    code = (
        "import os\n"
        "def walk(d):\n"
        "    for n in os.listdir(d):\n"
        "        p = d + '/' + n if d != '/' else '/' + n\n"
        "        try:\n"
        "            st = os.stat(p)\n"
        "        except Exception:\n"
        "            continue\n"
        "        if st[0] & 0x4000:\n"
        "            walk(p)\n"
        "        else:\n"
        "            print(p)\n"
        "walk('/')\n"
    )
    out, _ = c3_raw.exec_raw(s, code)
    return [ln.strip() for ln in out.splitlines() if ln.strip().startswith("/")]


def read_file_hex(s, path):
    # Stream in small chunks so the C3 never allocates a big buffer.
    code = (
        "import binascii\n"
        "f=open(%r,'rb')\n"
        "while True:\n"
        "    b=f.read(512)\n"
        "    if not b: break\n"
        "    print(binascii.hexlify(b).decode())\n"
        "f.close()\n" % path
    )
    out, err = c3_raw.exec_raw(s, code)
    if err.strip():
        raise RuntimeError("read %s: %s" % (path, err.strip()))
    return binascii.unhexlify("".join(out.split()))


def main():
    port = sys.argv[1]
    dest = sys.argv[2]
    s = c3_raw.connect(port)
    try:
        files = list_files(s)
        print("found", len(files), "files")
        for path in files:
            data = read_file_hex(s, path)
            local = dest + path
            os.makedirs(os.path.dirname(local), exist_ok=True)
            with open(local, "wb") as f:
                f.write(data)
            print("saved %5d  %s" % (len(data), path))
    finally:
        s.write(b"\x02")
        s.close()
    print("backup done ->", dest)


if __name__ == "__main__":
    main()
