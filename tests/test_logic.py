import sys; sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
from core import validate
from core.profiler import _replay, TxDelta, _extract
from core.scoring import _supply_component

# base58 validation
assert validate.is_solana_address("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
assert validate.is_solana_address("So11111111111111111111111111111111111111112")
assert not validate.is_solana_address("0x1234")
assert validate.is_evm_address("0x"+"a"*40)
print("addr ok")
print(validate.parse_input("/scan EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v solana"))
print(validate.parse_input("check this EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"))
print(validate.parse_input("hello how are you"))

# ledger replay: buy 1000 tok for 2 SOL, sell all for 5 SOL -> win, pnl +3
M="AAA"
d=[TxDelta(ts=1000,sig="a",quote_sol=-2.0,tokens={M:1000.0}),
   TxDelta(ts=1000+3600*8,sig="b",quote_sol=5.0,tokens={M:-1000.0}),
   # losing trade
   TxDelta(ts=2000,sig="c",quote_sol=-1.0,tokens={"BBB":500.0}),
   TxDelta(ts=2000+600,sig="d",quote_sol=0.4,tokens={"BBB":-500.0}),
   # airdrop, no quote leg -> ignored
   TxDelta(ts=3000,sig="e",quote_sol=-0.000005,tokens={"CCC":999.0}),
   # partial sell leaving dust -> should close
   TxDelta(ts=4000,sig="f",quote_sol=-3.0,tokens={"DDD":100.0}),
   TxDelta(ts=4000+7200,sig="g",quote_sol=4.0,tokens={"DDD":-99.0}),
  ]
closed,wins,pnl,hold=_replay(sorted(d,key=lambda x:x.ts),0)
print("closed",closed,"wins",wins,"pnl",round(pnl,3),"avg_hold_h",round(hold,2))
assert closed==3 and wins==2, (closed,wins)
assert abs(pnl-(3.0-0.6+1.0))<1e-6, pnl

# extraction from a realistic helius payload
tx={"timestamp":1700000000,"signature":"sig","slot":1,"feePayer":"W",
 "fee":5000,
 "nativeTransfers":[{"fromUserAccount":"W","toUserAccount":"POOL","amount":2_000_000_000}],
 "tokenTransfers":[{"mint":"AAA","tokenAmount":1234.5,"fromUserAccount":"POOL","toUserAccount":"W"}]}
e=_extract(tx,"W",150.0)
print("extract quote_sol",round(e.quote_sol,6),"tokens",e.tokens)
assert abs(e.quote_sol+2.000005)<1e-6

# wsol handled
tx2=dict(tx); tx2["tokenTransfers"]=[{"mint":"So11111111111111111111111111111111111111112","tokenAmount":3.0,"fromUserAccount":"POOL","toUserAccount":"W"},
 {"mint":"AAA","tokenAmount":10.0,"fromUserAccount":"W","toUserAccount":"POOL"}]
tx2["nativeTransfers"]=[]
e2=_extract(tx2,"W",150.0)
print("wsol sell quote",round(e2.quote_sol,6),e2.tokens)
assert abs(e2.quote_sol-2.999995)<1e-6

# supply curve
for v in (5,17.9,18,21.5,25,30,40,50,60):
    print(f"top10 {v}% -> factor {_supply_component(v):.3f}")
print("ALL TESTS PASS")
