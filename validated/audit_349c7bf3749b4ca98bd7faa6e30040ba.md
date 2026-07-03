Audit Report

## Title
Stale Cross-Chain rsETH/ETH Rate in `CrossChainRateReceiver.getRate()` Enables Over-Issuance of wrsETH and Pool Insolvency - (`contracts/cross-chain/CrossChainRateReceiver.sol`)

## Summary
`CrossChainRateReceiver.getRate()` returns the stored `rate` unconditionally with no staleness check, despite `lastUpdated` being recorded on every update. All L2 pool variants (`RSETHPoolV2`, `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, etc.) call `getRate()` to price every `deposit()`. Because `updateRate()` on the L1 provider is permissionless but requires the caller to pay LayerZero fees out-of-pocket with no protocol incentive, the L2 rate can freeze indefinitely below the true rsETH/ETH rate. Any depositor during a staleness window receives excess wrsETH, which can be redeemed for more ETH than was deposited, leaving the wrapper undercollateralized and the pool insolvent.

## Finding Description
**Rate storage without staleness enforcement:**

`CrossChainRateReceiver` stores `lastUpdated` on every `lzReceive` call but never consults it in `getRate()`:

```solidity
// lzReceive stores the timestamp
lastUpdated = block.timestamp;   // line 97

// getRate() ignores it entirely
function getRate() external view returns (uint256) {
    return rate;   // line 103-104 — no staleness guard
}
```

**Rate update is caller-funded with no incentive:**

`MultiChainRateProvider.updateRate()` is `external payable` and permissionless, but the caller must supply ETH to cover LayerZero messaging fees for every destination chain. There is no reimbursement or on-chain reward mechanism. During high-gas periods or low protocol activity, no one has an economic reason to call it.

**All pool deposit paths use the stale rate:**

`RSETHPoolV2.viewSwapRsETHAmountAndFee()` and the equivalent function in every pool variant compute:
```solidity
uint256 rsETHToETHrate = getRate();   // stale value
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```
A stale (lower) `rsETHToETHrate` produces a larger `rsETHAmount`. The pool then calls `wrsETH.mint(msg.sender, rsETHAmount)` — minting wrsETH directly without requiring rsETH to already be in the wrapper.

**Redemption path confirms the profit:**

`RsETHTokenWrapper._withdraw()` burns wrsETH and transfers alt rsETH 1:1. The alt rsETH bridges back to L1 as canonical rsETH at 1:1. On L1, `LRTWithdrawalManager` redeems rsETH for ETH at the current (true) rsETH price. The pool's ETH is bridged to L1 via `L1VaultV2`, which deposits it into the LRT deposit pool and mints rsETH at the true rate — fewer rsETH than the wrsETH already issued. The wrapper is permanently undercollateralized by the difference.

**Existing guards are insufficient:**

The `limitDailyMint` modifier caps the total wrsETH minted per day but does not prevent the attack — it only limits the daily magnitude. The `paused` modifier can halt deposits but requires a privileged `PAUSER_ROLE` holder to act, which is reactive and not automatic.

## Impact Explanation
**Critical — Direct theft of user funds and protocol insolvency.**

For every ETH deposited at a stale rate, the attacker receives wrsETH worth more than the deposited ETH at the true rate. When redeemed through the wrapper → L1 bridge → withdrawal manager path, the attacker extracts more ETH than contributed. The pool's ETH reserves, once bridged to L1 and converted to rsETH at the true rate, are insufficient to back all outstanding wrsETH. The last holders of wrsETH cannot redeem, constituting direct theft from honest depositors and permanent protocol insolvency. This matches the allowed impact: **Critical — Direct theft of any user funds** and **Critical — Protocol insolvency**.

## Likelihood Explanation
No special role or privileged access is required. Any external caller can:
1. Read `CrossChainRateReceiver.lastUpdated` and `LRTOracle.rsETHPrice()` on-chain to measure divergence.
2. Call `RSETHPoolV2.deposit{value: X}(...)` (or any pool variant) during the staleness window.
3. Redeem the excess wrsETH through the standard wrapper → bridge → withdrawal path.

The attack is passive (no front-running required), repeatable up to the daily mint limit per day, and requires no victim mistakes. The staleness window can realistically span hours to days during periods of high gas prices or low protocol activity, and rsETH/ETH appreciates continuously, so any non-zero staleness creates exploitable divergence.

## Recommendation
1. Add a `MAX_STALENESS` constant (e.g., 24 hours) and revert in `CrossChainRateReceiver.getRate()` if `block.timestamp - lastUpdated > MAX_STALENESS`.
2. Alternatively, add a staleness check in each pool's `deposit()` path by reading `CrossChainRateReceiver.lastUpdated` directly.
3. Introduce an on-chain incentive (e.g., a small ETH bounty from protocol fees) for calling `updateRate()` to ensure liveness and prevent the staleness window from opening.

## Proof of Concept
**Setup:**
- L2 pool: `RSETHPoolV2` with `rsETHOracle` pointing to a `CrossChainRateReceiver`.
- True rsETH/ETH rate on L1: `1.05e18`.
- Stale rate in `CrossChainRateReceiver.rate`: `1.00e18` (last updated 48 hours ago).

**Attack sequence:**
1. Attacker observes `CrossChainRateReceiver.lastUpdated` is 48 hours old and `rate == 1.00e18` while L1 `LRTOracle.rsETHPrice() == 1.05e18`.
2. Attacker calls `RSETHPoolV2.deposit{value: 100 ether}("ref")`.
3. Pool computes `rsETHAmount = 100e18 * 1e18 / 1.00e18 = 100e18` wrsETH and mints it to attacker.
4. Fair amount at true rate: `100e18 * 1e18 / 1.05e18 ≈ 95.24e18` wrsETH. Attacker has `~4.76e18` excess.
5. Pool eventually bridges 100 ETH to L1 → L1Vault deposits into LRT pool → mints `~95.24` rsETH → bridges rsETH back to L2 wrapper.
6. Wrapper now holds `~95.24` rsETH but has `100` wrsETH outstanding.
7. Attacker calls `RsETHTokenWrapper.withdraw(altRsETH, 100e18)` → burns 100 wrsETH, receives 100 alt rsETH (wrapper is now insolvent; remaining wrsETH holders cannot redeem).
8. Attacker bridges 100 alt rsETH to L1 → canonical rsETH → redeems via `LRTWithdrawalManager` at true rate `1.05e18` → receives `~105 ETH`.
9. Net profit: `~5 ETH` per `100 ETH` deposited. Honest users holding remaining wrsETH cannot redeem.

**Foundry fork test plan:** Fork an L2 where `CrossChainRateReceiver` is deployed. Warp `block.timestamp` forward by 48 hours without calling `updateRate()`. Confirm `CrossChainRateReceiver.lastUpdated` is stale. Call `RSETHPoolV2.deposit` with a large ETH value. Assert `wrsETH` minted exceeds `deposit * 1e18 / trueRate`. Assert `wrsETH.totalSupply() > rsETHInWrapper` after simulating the L1 bridging flow at the true rate.