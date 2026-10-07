"""Which _CHECKPOINT_OFFSET_STEPS tier does each id land in?

Read-only probe: written before the implementation so the tiering can be
checked against the measurements other tests pin.
"""
import zlib

STEPS = (-0.12, -0.06, 0.0, 0.06, 0.12)


def tier(cid):
    return STEPS[zlib.crc32(cid.encode("utf-8")) % len(STEPS)]


ids = [
    "ckpt-41", "ckpt-40", "ckpt-43", "ckpt-v41", "ckpt-v42", "ckpt-v43",
    "ckpt-v44", "ckpt-v45", "ckpt-v46", "ckpt-v47", "ckpt-v48",
    "ckpt-aaa", "ckpt-bbb", "ckpt-ccc", "ckpt-ddd", "ckpt-zzz", "ckpt-abc",
    "ckpt-det", "ckpt-replay", "ckpt-provenance",
]
for cid in ids:
    print(f"{cid:>16} -> {tier(cid):+.2f}")

print()
pairs = [("ckpt-v41", "ckpt-v42"), ("ckpt-v43", "ckpt-v44"),
         ("ckpt-v45", "ckpt-v46"), ("ckpt-v47", "ckpt-v48")]
print("adjacent pairs differ?",
      [(a, b, tier(a) != tier(b)) for a, b in pairs])
print()
sp = sorted({tier(f"ckpt-{i}") for i in range(200)})
print("distinct tiers over ckpt-0..199:", sp)
sp500 = sorted({tier(f"ckpt-{i}") for i in range(500)})
print("distinct tiers over ckpt-0..499:", sp500)
print("distinct tiers over ckpt-aaa..ddd:",
      sorted({tier(c) for c in ("ckpt-aaa", "ckpt-bbb", "ckpt-ccc", "ckpt-ddd")}))
