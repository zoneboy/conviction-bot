"""Input parsing, address validation, chain detection."""
import re

B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
B58_SET = set(B58_ALPHABET)

EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
SOL_CANDIDATE_RE = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")


def b58_decode_len(s: str) -> int:
    """Return the decoded byte length, or -1 if not valid base58."""
    if not s or any(c not in B58_SET for c in s):
        return -1
    num = 0
    for c in s:
        num = num * 58 + B58_ALPHABET.index(c)
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    leading = len(s) - len(s.lstrip("1"))
    return len(body) + leading


def is_solana_address(s: str) -> bool:
    return 32 <= len(s) <= 44 and b58_decode_len(s) == 32


def is_evm_address(s: str) -> bool:
    return bool(EVM_RE.match(s))


def detect_chain(address: str) -> str | None:
    if is_evm_address(address):
        return "base"
    if is_solana_address(address):
        return "solana"
    return None


def parse_input(text: str) -> tuple[str | None, str | None, str | None]:
    """Return (address, chain, error)."""
    if not text:
        return None, None, "Send a contract address."
    parts = text.strip().split()
    if parts and parts[0].lower().startswith("/scan"):
        parts = parts[1:]
    if not parts:
        return None, None, ("Usage: <code>/scan &lt;contract address&gt; "
                            "[solana|base]</code>")

    explicit = None
    tokens = []
    for p in parts:
        low = p.lower()
        if low in ("solana", "sol", "base", "eth", "ethereum"):
            explicit = "solana" if low in ("solana", "sol") else "base"
        else:
            tokens.append(p)

    address = None
    for t in tokens:
        cleaned = t.strip().strip(",.<>()[]\"'`")
        if is_evm_address(cleaned) or is_solana_address(cleaned):
            address = cleaned
            break
        found = SOL_CANDIDATE_RE.search(cleaned)
        if found and is_solana_address(found.group()):
            address = found.group()
            break

    if not address:
        return None, None, ("That does not look like a valid contract address. "
                            "Send a Solana mint (base58, 32-44 chars) or an EVM "
                            "address (0x + 40 hex).")

    chain = explicit or detect_chain(address)
    if chain is None:
        return None, None, "Could not determine the chain for that address."
    if explicit and detect_chain(address) != explicit:
        return None, None, (f"Address format does not match chain "
                            f"<b>{explicit}</b>.")
    return address, chain, None
