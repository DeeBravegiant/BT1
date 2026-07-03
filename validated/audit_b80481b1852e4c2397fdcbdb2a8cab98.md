Audit Report

## Title
Direct ETH Donation to `LRTDepositPool` Inflates `rsETHPrice`, Causing Depositors to Receive Zero rsETH - (File: contracts/LRTDepositPool.sol, contracts/LRTOracle.sol)

## Summary
`LRTDepositPool` exposes an unrestricted `receive()` function, and `getETHDistributionData()` reports `address(this).balance` directly as the ETH lying in the pool. Any actor can donate ETH to inflate the reported TVL, which inflates `rsETHPrice` when the public `updateRSETHPrice()` is called. Subsequent depositors who pass `minRSETHAmountExpected = 0` can have their ETH permanently absorbed by the protocol while receiving zero rsETH in return.

## Finding Description
**Root cause:** `LRTDepositPool.getETHDistributionData()` uses `address(this).balance` (line 480) as the ETH lying in the pool. Because `receive() external payable {}` (line 58) is unrestricted, any actor can inflate this value without minting rsETH.

**Exploit chain:**
1. Attacker calls `LRTDepositPool.receive()` sending `D` ETH directly. `address(this).balance` increases by `D`.
2. Attacker (or anyone) calls `LRTOracle.updateRSETHPrice()` (line 87, `public whenNotPaused`). Inside `_updateRsETHPrice()`, `_getTotalEthInProtocol()` (line 331) calls `ILRTDepositPool.getTotalAssetDeposits(ETH)` (line 341), which calls `getAssetDistributionData(ETH_TOKEN)` → `getETHDistributionData()`, returning the inflated `address(this).balance`. The new price is computed as `(totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply)` (line 250), inflated by `D`.
3. Victim calls `depositETH{value: 1 ETH}(0, "")`. `getRsETHAmountToMint` computes `(1e18 * assetPrice) / rsETHPrice` (line 520). With a sufficiently inflated `rsETHPrice`, integer division truncates to zero.
4. `_beforeDeposit` checks `rsethAmountToMint < minRSETHAmountExpected` (line 667). With `minRSETHAmountExpected = 0`, the condition `0 < 0` is false — no revert.
5. `_mintRsETH(0)` (line 686) calls `IRSETH.mint(msg.sender, 0)`. Victim's ETH is accepted; zero rsETH is minted. The ETH is permanently absorbed into protocol TVL.

**Why existing guards fail:**
- The `pricePercentageLimit` guard (lines 256–265) only blocks non-manager callers when `pricePercentageLimit > 0` AND the single-step price jump exceeds the threshold. If `pricePercentageLimit == 0` (unconfigured), there is no cap at all. Even when set, the attacker can execute the inflation gradually across multiple transactions, each staying within the per-step limit, to cumulatively distort the price.
- There is no check anywhere that `rsethAmountToMint > 0` before minting.

## Impact Explanation
**Critical — Direct theft of any user funds (in-motion).**

When `rsETHPrice` is inflated sufficiently, a depositor's ETH is accepted by the contract while zero rsETH is minted to them. The depositor has no claim to their ETH; it is permanently absorbed into the protocol TVL and proportionally benefits all existing rsETH holders. This constitutes direct, permanent theft of user funds in-motion. Even short of the zero-mint extreme, every depositor after the price inflation receives proportionally fewer rsETH tokens than the fair exchange rate warrants, constituting a quantifiable loss of value.

## Likelihood Explanation
**Low.** The attacker must sacrifice real ETH with no guaranteed direct return (profit only materializes if they hold a large rsETH position and can exit on secondary markets). The `pricePercentageLimit` guard, when configured, limits single-step price jumps for unprivileged callers, requiring the attacker to spread the inflation across multiple transactions over multiple periods. The victim must also pass `minRSETHAmountExpected = 0`, which is common in direct contract calls and many integrations but is not universal. Despite low likelihood, the impact is severe and the attack is fully permissionless once the preconditions are met.

## Recommendation
1. **Replace `address(this).balance` with a tracked variable.** Introduce a `totalDepositedETH` storage variable incremented only through legitimate deposit flows (`depositETH`, `receiveFromRewardReceiver`, `receiveFromNodeDelegator`, `receiveFromLRTConverter`). Use this variable in `getETHDistributionData()` instead of `address(this).balance`. ETH arriving via the bare `receive()` should not count toward TVL.
2. **Reject zero-rsETH mints unconditionally.** Add `if (rsethAmountToMint == 0) revert ZeroRsETHMinted();` in `_beforeDeposit` or `_mintRsETH`, regardless of `minRSETHAmountExpected`.
3. **Enforce a non-zero `pricePercentageLimit` at deployment and upgrade.** Document and enforce this as a required configuration step.

## Proof of Concept
```
// Preconditions:
// rsethSupply = 1000e18, totalETHInProtocol = 1000e18, rsETHPrice = 1e18
// pricePercentageLimit = 0 (unconfigured)

// Step 1: Attacker donates 999 ETH directly
(bool ok,) = address(lrtDepositPool).call{value: 999 ether}("");
// address(lrtDepositPool).balance is now 999 ETH higher

// Step 2: Attacker triggers price update
lrtOracle.updateRSETHPrice();
// _getTotalEthInProtocol() returns 1999e18
// newRsETHPrice = 1999e18 / 1000e18 ≈ 1.999e18

// Step 3 (extreme — attacker donates 999_000 ETH total):
// newRsETHPrice = 1000e18 (1000x inflation)
// Victim deposits 1 ETH with minRSETHAmountExpected = 0:
lrtDepositPool.depositETH{value: 1 ether}(0, "");
// rsethAmountToMint = (1e18 * 1e18) / 1000e18 = 0
// _beforeDeposit: 0 < 0 == false → no revert
// _mintRsETH(0) → victim receives 0 rsETH, 1 ETH permanently absorbed

// Foundry test sketch:
// 1. Fork mainnet / deploy local fixture
// 2. vm.deal(attacker, 999_000 ether); vm.prank(attacker); address(pool).call{value: ...}("")
// 3. oracle.updateRSETHPrice()
// 4. vm.deal(victim, 1 ether); vm.prank(victim); pool.depositETH{value: 1 ether}(0, "")
// 5. assertEq(rsETH.balanceOf(victim), 0)
// 6. assertGt(address(pool).balance, 0)  // victim's ETH absorbed
```