Audit Report

## Title
Permissionless `updateRSETHPrice()` Enables Sandwich Attack to Extract Value from rsETH Holders - (`contracts/LRTOracle.sol`, `contracts/LRTDepositPool.sol`, `contracts/LRTWithdrawalManager.sol`)

## Summary

`LRTOracle.updateRSETHPrice()` is callable by any unprivileged address with no access control beyond `whenNotPaused`. Because both deposit minting and withdrawal redemption read the same stored `rsETHPrice` state variable, an attacker can atomically deposit at a stale (lower) price, force the price upward via `updateRSETHPrice()`, and immediately redeem via `instantWithdrawal()` at the updated higher price — extracting more underlying assets than were deposited. The surplus is extracted from existing rsETH holders whose share of backing assets is diluted.

## Finding Description

**Root cause:** `rsETHPrice` is a mutable storage variable written by `_updateRsETHPrice()`, and both the deposit-side minting ratio and the withdrawal-side redemption ratio read it directly from storage at call time. There is no snapshot, no per-transaction price lock, and no restriction on who may trigger a price update.

**Code path — deposit minting:**
`LRTDepositPool.depositAsset()` calls `_beforeDeposit()` → `getRsETHAmountToMint()`:
```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
A lower stored `rsETHPrice` produces a larger `rsethAmountToMint` for the same deposit.

**Code path — price update:**
`LRTOracle.updateRSETHPrice()` is `public whenNotPaused` with no role check. It calls `_updateRsETHPrice()`, which computes `newRsETHPrice = totalETHInProtocol / rsethSupply` and writes it to `rsETHPrice`. The `pricePercentageLimit` guard only reverts for non-managers when the new price exceeds `highestRsethPrice` by more than the configured threshold; for normal staking-reward accrual (e.g. ~0.01 %/day), the increase is well within any reasonable limit and the call succeeds.

**Code path — instant withdrawal redemption:**
`LRTWithdrawalManager.instantWithdrawal()` calls `getExpectedAssetAmount()`:
```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```
After `updateRSETHPrice()` has been called, `lrtOracle.rsETHPrice()` returns the updated higher value, so `underlyingToReceive` is larger.

**Exploit sequence (single atomic transaction, `isInstantWithdrawalEnabled[asset] == true`):**

Let `P_s` = stale stored price, `P_t` = true price (`P_t > P_s`), `D` = deposit value in ETH, `S` = existing rsETH supply, `T` = existing TVL.

1. Attacker calls `depositAsset(asset, X)` → receives `rsETH_atk = D / P_s` (inflated because denominator is `P_s < P_t`).
2. Attacker calls `updateRSETHPrice()` → new price `P_new = (T + D) / (S + D/P_s)`. Since `P_t > P_s`, it follows that `P_new > P_s` (proven: `T + D > P_s·(S + D/P_s)` iff `T > P_s·S` iff `P_t > P_s`).
3. Attacker calls `instantWithdrawal(asset, rsETH_atk)` → receives `rsETH_atk · P_new / assetPrice`.

Attacker profit in ETH = `D · (P_t − P_s) / (P_s · (1 + D/(P_s·S)))`. For small deposits relative to TVL this approaches `D · (P_t − P_s) / P_s`, which is positive whenever the price is stale.

**Existing guards and why they are insufficient:**

- `nonReentrant` on `depositAsset` and `instantWithdrawal`: prevents reentrancy within a single call, not sequential calls from an attacker contract.
- `pricePercentageLimit`: only blocks increases above `highestRsethPrice` exceeding the configured threshold. Normal daily staking-reward accrual is far below any reasonable threshold (e.g. 1 %), so the guard does not fire.
- `getAvailableAssetAmount` check in `initiateWithdrawal`: requires sufficient protocol-wide liquidity, not just the attacker's own deposit. A live protocol with meaningful TVL satisfies this.
- `CantInstantWithdrawMoreThanAvailable` in `instantWithdrawal`: requires the unstaking vault to hold sufficient assets. Again, a live protocol satisfies this.

## Impact Explanation

The attacker recovers more underlying assets than deposited. The surplus is not created from thin air; it is extracted from existing rsETH holders: the deposit at the stale price inflates rsETH supply relative to backing assets, and the withdrawal at the updated price redeems that inflated rsETH at a rate that exceeds the attacker's contribution. This constitutes **direct theft of user funds** — Critical impact under the allowed scope.

## Likelihood Explanation

`rsETHPrice` is not updated automatically; it requires an explicit call. Staking rewards accrue continuously, so the stored price is routinely stale between keeper updates. Any EOA or contract can call `updateRSETHPrice()`. The attacker only needs to monitor the gap between the stored price and the on-chain computable true price (both inputs are public), and execute when the gap exceeds gas cost. No special privileges, no governance capture, no external oracle compromise, and no victim mistake is required. Likelihood is **Medium** (requires a stale price window, which is a normal operating condition, and `isInstantWithdrawalEnabled` to be true for the fully atomic path).

## Recommendation

1. **Restrict `updateRSETHPrice()`** to a trusted keeper/manager role (e.g. `onlyLRTManager` or a dedicated `KEEPER_ROLE`), eliminating the ability for an attacker to trigger a price update on demand.
2. **Alternatively**, record the `rsETHPrice` at the start of each user transaction and use that snapshot for both the deposit minting ratio and any same-block withdrawal redemption, preventing intra-transaction price manipulation.
3. **For `instantWithdrawal()`**, consider using the price at the time of a prior withdrawal request rather than the live price at execution time, consistent with how `expectedAssetAmount` is already locked for the delayed path.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

interface IDepositPool  { function depositAsset(address,uint256,uint256,string calldata) external; }
interface IOracle       { function updateRSETHPrice() external; }
interface IWithdrawal   { function instantWithdrawal(address,uint256,string calldata) external; }
interface IERC20        { function approve(address,uint256) external; function balanceOf(address) external view returns(uint256); }

contract Exploit {
    IDepositPool  pool;
    IOracle       oracle;
    IWithdrawal   wm;
    IERC20        asset;
    IERC20        rsETH;

    constructor(address _pool, address _oracle, address _wm, address _asset, address _rsETH) {
        pool = IDepositPool(_pool); oracle = IOracle(_oracle);
        wm = IWithdrawal(_wm); asset = IERC20(_asset); rsETH = IERC20(_rsETH);
    }

    function attack(uint256 depositAmount) external {
        // 1. Deposit at stale (lower) rsETHPrice → receive inflated rsETH
        asset.approve(address(pool), depositAmount);
        pool.depositAsset(address(asset), depositAmount, 0, "");

        // 2. Force price update → rsETHPrice now reflects accrued rewards
        oracle.updateRSETHPrice();

        // 3. Instant withdrawal at updated (higher) rsETHPrice → receive more assets than deposited
        uint256 rsETHBal = rsETH.balanceOf(address(this));
        rsETH.approve(address(wm), rsETHBal);
        wm.instantWithdrawal(address(asset), rsETHBal, "");
        // asset.balanceOf(address(this)) > depositAmount  ← profit extracted from other holders
    }
}
```

**Foundry fork test plan:**
1. Fork mainnet at a block where `rsETHPrice` is known to be stale (compare stored value vs. `_getTotalEthInProtocol() / totalSupply()`).
2. Deploy `Exploit`, fund with asset tokens.
3. Call `attack(depositAmount)`.
4. Assert `asset.balanceOf(exploit) > depositAmount` after the call.
5. Assert `rsETHPrice` increased between steps 1 and 3.
6. Fuzz over `depositAmount` to characterize profit as a function of deposit size and price gap.