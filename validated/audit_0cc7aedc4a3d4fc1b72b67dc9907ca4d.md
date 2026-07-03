Audit Report

## Title
Missing `updateRSETHPrice()` Before Deposit Allows Minting Excess rsETH at Stale Price — (`contracts/LRTDepositPool.sol`)

## Summary

`LRTOracle` stores `rsETHPrice` as a state variable that is only updated when `updateRSETHPrice()` is explicitly called. `LRTDepositPool.depositETH()` and `depositAsset()` compute rsETH to mint using this stored value without first refreshing it. When ETH rewards have accrued into the deposit pool (e.g., via `FeeReceiver.sendFunds()`) but `rsETHPrice` has not yet been updated, any depositor receives more rsETH than their deposit entitles them to, directly diluting existing holders' unclaimed yield.

## Finding Description

`LRTOracle` declares `rsETHPrice` as a plain storage variable updated only inside `_updateRsETHPrice()`, which is invoked by the public `updateRSETHPrice()` or the manager-only `updateRSETHPriceAsManager()`.

`LRTDepositPool.depositETH()` calls `_beforeDeposit()` → `getRsETHAmountToMint()`, which computes:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`lrtOracle.rsETHPrice()` returns the **stored** state variable, not a freshly computed value. Neither `depositETH()` nor `depositAsset()` calls `updateRSETHPrice()` at any point before this division.

`FeeReceiver.sendFunds()` transfers accumulated MEV/execution-layer rewards directly to `LRTDepositPool` via `receiveFromRewardReceiver()`. After this call, `address(this).balance` of the deposit pool increases immediately. `getETHDistributionData()` reads `address(this).balance` directly, so `_getTotalEthInProtocol()` (used inside `_updateRsETHPrice()`) would return a higher value — but `rsETHPrice` is not updated until `updateRSETHPrice()` is explicitly called.

The exploit window is: `FeeReceiver.sendFunds()` executes → `rsETHPrice` is stale (lower than actual) → any deposit in this window mints excess rsETH at the stale price → `updateRSETHPrice()` is eventually called → the fee and price are computed against a supply that already includes the excess rsETH, permanently diluting original holders.

No existing check in `depositETH()` or `depositAsset()` enforces a fresh price before minting.

## Impact Explanation

**High — Theft of unclaimed yield.**

When `rsETHPrice` is stale (lower than actual), a depositor of `D` ETH receives `D / P_stale` rsETH instead of the correct `D / P_actual`. Since `P_stale < P_actual`, the depositor receives excess rsETH representing a claim on TVL earned by existing holders as staking/MEV yield. This is a direct, quantifiable transfer of unclaimed yield from existing rsETH holders to the depositor.

Numerical example (10% protocol fee, 100 ETH reward accrual on 1000 ETH TVL):

| Step | TVL | rsETH Supply | rsETHPrice |
|---|---|---|---|
| Initial | 1000 ETH | 1000 | 1.000 |
| After `sendFunds()` (100 ETH rewards) | 1100 ETH | 1000 | 1.000 (stale) |
| Alice deposits 100 ETH at stale price | 1200 ETH | 1100 | 1.000 (stale) |
| After `updateRSETHPrice()` | 1200 ETH | ~1109.24 | ~1.0818 |

- Original 1000 rsETH holders' value: `1000/1109.24 × 1200 ≈ 1081.8 ETH`
- Correct value (if price updated first): `1000/1100.91 × 1200 ≈ 1090 ETH`
- **Loss to existing holders: ~8.2 ETH**, captured by Alice as excess rsETH

## Likelihood Explanation

`updateRSETHPrice()` is a permissionless public function with no on-chain enforcement that it be called before every deposit. Rewards accumulate continuously via staking and MEV. `FeeReceiver.sendFunds()` is callable by anyone. Any deposit made between a `sendFunds()` call and the next `updateRSETHPrice()` call passively exploits the stale price. A sophisticated actor can deliberately call `sendFunds()` to flush rewards into the deposit pool and immediately deposit before anyone calls `updateRSETHPrice()`, maximizing the dilution. No special privileges, victim mistakes, or external protocol compromise are required.

## Recommendation

Call `updateRSETHPrice()` at the start of both `depositETH()` and `depositAsset()` in `LRTDepositPool`, before computing `rsethAmountToMint`:

```solidity
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId)
    external payable nonReentrant whenNotPaused onlySupportedAsset(LRTConstants.ETH_TOKEN)
{
    ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice(); // add
    uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
    _mintRsETH(rsethAmountToMint);
    emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
}
```

Apply the same fix to `depositAsset()`. This ensures the price reflects all accrued rewards before any new rsETH is minted.

## Proof of Concept

1. Protocol state: 1000 ETH TVL, 1000 rsETH supply, `rsETHPrice = 1.0`.
2. Call `FeeReceiver.sendFunds()` — 100 ETH of MEV rewards transferred to `LRTDepositPool`. `address(this).balance` increases to 1100 ETH. `rsETHPrice` remains `1.0` (stale).
3. Alice calls `depositETH{value: 100 ether}(0, "")`. `getRsETHAmountToMint` computes `100e18 * 1e18 / 1e18 = 100e18` rsETH. Alice receives 100 rsETH. TVL = 1200 ETH, supply = 1100 rsETH.
4. Anyone calls `updateRSETHPrice()`. `previousTVL = 1100 * 1.0 = 1100`. `rewardAmount = 1200 - 1100 = 100`. `protocolFeeInETH = 10`. `newRsETHPrice = 1190/1100 ≈ 1.0818`. Fee rsETH ≈ 9.24 minted to treasury. Supply = 1109.24.
5. Alice's 100 rsETH is worth `100 × 1.0818 = 108.18 ETH` — she deposited 100 ETH and captured ~8.18 ETH of yield belonging to original holders.
6. Original 1000 rsETH holders receive `1000 × 1.0818 = 1081.8 ETH` instead of the correct `~1090 ETH` — a loss of ~8.2 ETH of unclaimed yield.

**Foundry fork test plan:** Fork mainnet, set up state with known TVL and rsETH supply, call `sendFunds()` to inject rewards, deposit as Alice without calling `updateRSETHPrice()`, then call `updateRSETHPrice()` and assert that Alice's rsETH value exceeds her deposit amount and that original holders' per-rsETH ETH value is lower than it would be if `updateRSETHPrice()` had been called first.