"""Upload local files to the CyberBrick C3 over raw REPL (binary-safe, chunked).

Usage: python c3_put.py COM18 local1:remote1 local2:remote2 ...
Example: python c3_put.py COM18 config.py:/config.py main.py:/larm_recv.py
"""
import sys
import binascii
import c3_raw


def put_file(s, local, remote):
    with open(local, "rb") as f:
        data = f.read()
    out, err = c3_raw.exec_raw(s, "f=open(%r,'wb')\n" % remote)
    if err.strip():
        raise RuntimeError("open %s: %s" % (remote, err.strip()))
    for i in range(0, len(data), 256):
        h = binascii.hexlify(data[i:i + 256]).decode()
        _, err = c3_raw.exec_raw(
            s, "import binascii\nf.write(binascii.unhexlify('%s'))\n" % h)
        if err.strip():
            raise RuntimeError("write %s @%d: %s" % (remote, i, err.strip()))
    c3_raw.exec_raw(s, "f.close()\n")
    # verify size
    out, err = c3_raw.exec_raw(
        s, "import os\nprint(os.stat(%r)[6])\n" % remote)
    return len(data), out.strip()


def main():
    port = sys.argv[1]
    pairs = [a.split(":", 1) for a in sys.argv[2:]]
    s = c3_raw.connect(port)
    try:
        for local, remote in pairs:
            sent, devsize = put_file(s, local, remote)
            print("put %s -> %s : sent %d, device reports %s" %
                  (local, remote, sent, devsize))
    finally:
        s.write(b"\x02")
        s.close()


if __name__ == "__main__":
    main()
