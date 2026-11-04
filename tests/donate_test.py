"""
Checks portable/scripts/donate.json: python donate_test.py <portable/scripts>

Every crypto address must pass its network's own check (checksum or length)
and get a QR code, and the SBP link must be https. A typo here would send
the donations nowhere, so the Windows build stops on it.
"""
import base64
import hashlib
import sys

sys.path.insert(0, sys.argv[1])
import app  # noqa: E402

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58decode(text):
    n = 0
    for ch in text:
        n = n * 58 + B58.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\0" * (len(text) - len(text.lstrip("1"))) + raw


def b58check(text, version):
    raw = b58decode(text)
    body, check = raw[:-4], raw[-4:]
    return hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4] == check and body[:1] == version


def ton_ok(text):
    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    crc = 0
    for byte in raw[:34]:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1) & 0xFFFF
    return len(raw) == 36 and crc == int.from_bytes(raw[34:], "big")


def valid(network, address):
    net = network.lower()
    if "tron" in net:
        return b58check(address, b"\x41")
    if "bitcoin" in net:
        return address.startswith("1") and b58check(address, b"\x00")
    if "litecoin" in net:
        versions = {"L": b"\x30", "M": b"\x32", "3": b"\x05"}
        return address[:1] in versions and b58check(address, versions[address[:1]])
    if "solana" in net:
        return len(b58decode(address)) == 32
    if "ethereum" in net or "erc20" in net:
        return address.startswith("0x") and len(address) == 42 and all(c in "0123456789abcdefABCDEF" for c in address[2:])
    if net == "ton":
        return ton_ok(address)
    raise AssertionError(f"no check for network {network}")


info = app.donate_info()
assert info, "donate.json is missing or empty"
assert info["sbp"]["url"].startswith("https://") and info["sbp"]["qr"].startswith("<svg"), info["sbp"]
for item in info["crypto"]:
    assert valid(item["network"], item["address"]), f"bad address: {item['coin']} {item['network']} {item['address']}"
    assert item["qr"].startswith("<svg"), item["coin"]
print(f"donate test: OK ({len(info['crypto'])} addresses, SBP link)")
