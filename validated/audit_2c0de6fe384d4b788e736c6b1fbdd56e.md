The code confirms the claim. Key observations:

- [1](#0-0)  — `_beforeDeposit` (mint calculation) executes on L111 before `safeTransferFrom` on L114.
- [2](#0-1)  — `getRsETHAmountToMint` uses the caller-supplied `amount` directly.
- [3](#0-2)  — `getTotalAssetDeposits` uses live `balanceOf`, so the oracle reflects actual (slightly lower) balance.
- [4](#0-3)  — `_getTotalEthInProtocol` calls `getTotalAssetDeposits`, confirming the oracle-side accounting is correct while rsETH supply is inflated.
- stETH is a confirmed supported asset in `LRTDepositPool.sol`. [5](#0-4) 

---

Audit Report

## Title
rsETH Over-Minted Due to Pre-Transfer Amount Used for Mint Calculation with Rebasing stETH Deposits — (File: contracts/LRTDepositPool.sol)

## Summary
`LRTDepositPool.depositAsset()` computes the rsETH mint amount from the caller-supplied `depositAmount` before executing `safeTransferFrom`. For stETH — a supported rebasing token with share-based accounting — the contract may receive up to 1 wei less than `depositAmount` per transfer. Because the oracle uses live `balanceOf` calls while rsETH supply is minted against the full requested amount, the rsETH/ETH exchange rate is permanently depressed by a small amount on every stETH deposit.

## Finding Description
In `depositAsset`, `_beforeDeposit` is called on L111 to compute `rsethAmountToMint` using `getRsETHAmountToMint(asset, depositAmount)`. The actual `safeTransferFrom` executes on L114, after the mint amount is already fixed. `getRsETHAmountToMint` computes `(amount * assetPrice) / rsETHPrice` using the requested `depositAmount`, not the actual received amount.

stETH is a supported asset. Its share-based accounting means `safeTransferFrom(sender, this, X)` may credit the contract with `X − 1` wei due to integer rounding in share conversion. The rsETH minted is based on the full `X`.

`_getTotalEthInProtocol` calls `getTotalAssetDeposits(asset)`, which uses `IERC20(asset).balanceOf(address(this))` — reflecting the actual (slightly lower) balance. The rsETH supply, however, is permanently inflated. The resulting rsETH price (`totalETHInProtocol / rsethSupply`) is permanently depressed per deposit, with no rebase or correction mechanism to reconcile the divergence.

Existing checks (`minRSETHAmountExpected`, deposit limits) do not address the pre-transfer vs. post-transfer accounting gap.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

Each stETH deposit over-mints rsETH by up to 1 wei worth of rsETH. Across many deposits, the cumulative inflation of rsETH supply causes the rsETH/ETH exchange rate to be permanently lower than it should be. Existing rsETH holders receive slightly less ETH per rsETH upon redemption than the protocol's accounting promises. No funds are directly stolen, but the protocol fails to deliver the exact exchange rate implied by the deposit math.

## Likelihood Explanation
**Low.** The rounding occurs on every stETH deposit due to stETH's share-based accounting. The per-deposit magnitude is at most 1 wei, making the cumulative effect negligible in practice. The entry path is fully unprivileged — any user calling `depositAsset` with stETH triggers it.

## Recommendation
Compute the rsETH mint amount based on the actual balance increase rather than the caller-supplied `depositAmount`:

```solidity
uint256 balanceBefore = IERC20(asset).balanceOf(address(this));
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
uint256 actualReceived = IERC20(asset).balanceOf(address(this)) - balanceBefore;
uint256 rsethAmountToMint = getRsETHAmountToMint(asset, actualReceived);
if (rsethAmountToMint < minRSETHAmountExpected) revert MinimumAmountToReceiveNotMet();
_mintRsETH(rsethAmountToMint);
```

This eliminates the divergence for any token with transfer-side rounding or fees, and the slippage check (`minRSETHAmountExpected`) still protects the depositor.

## Proof of Concept
1. Protocol has 1000 stETH in TVL, rsETH supply = 1000e18, rsETH price = 1e18.
2. User calls `depositAsset(stETH, 1000e18, minRSETH, "")`.
3. `_beforeDeposit` (L665) computes `rsethAmountToMint = 1000e18 * 1e18 / 1e18 = 1000e18`.
4. `safeTransferFrom` (L114) transfers stETH; due to share rounding, contract receives `1000e18 − 1` wei.
5. `_mintRsETH(1000e18)` mints rsETH based on the full `1000e18`.
6. New state: actual stETH balance = `2000e18 − 1`, rsETH supply = `2000e18`.
7. Oracle price = `(2000e18 − 1) * 1e18 / 2000e18` < `1e18` — permanently depressed.
8. All existing holders redeem for slightly less ETH than the pre-deposit price implied.

**Foundry fork test plan:** Fork mainnet, deploy/configure with stETH as supported asset, call `depositAsset(stETH, amount, 0, "")`, assert `IERC20(stETH).balanceOf(depositPool) < amount` while `rsETH.totalSupply()` increased by `amount`-worth of rsETH, and verify `lrtOracle.rsETHPrice()` is lower than pre-deposit.

### Citations

**File:** contracts/LRTDepositPool.sol (L110-115)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);
```

**File:** contracts/LRTDepositPool.sol (L444-444)
```text
        assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L648-665)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
```

**File:** contracts/LRTOracle.sol (L341-341)
```text
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
```
