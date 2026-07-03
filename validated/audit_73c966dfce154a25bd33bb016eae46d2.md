Audit Report

## Title
Stale `rsETHPrice` Enables Excess rsETH Minting at Depositors' Expense — (`contracts/LRTOracle.sol`, `contracts/LRTDepositPool.sol`)

## Summary

`LRTOracle.rsETHPrice` is a stored state variable updated only when `updateRSETHPrice()` is explicitly called. Neither `depositETH()` nor `depositAsset()` in `LRTDepositPool` refresh this value before computing the rsETH mint amount. An attacker who deposits during a window where EigenLayer rewards have accrued but `rsETHPrice` has not been updated receives more rsETH than their deposit is worth, diluting the yield that belongs to existing holders.

## Finding Description

`rsETHPrice` is a persistent storage variable in `LRTOracle`: [1](#0-0) 

It is only updated when `updateRSETHPrice()` is explicitly called — by a keeper, a manager, or any unprivileged caller (subject to the `pricePercentageLimit` threshold): [2](#0-1) 

`depositETH()` and `depositAsset()` both call `_beforeDeposit()` without first refreshing the price: [3](#0-2) [4](#0-3) 

`_beforeDeposit()` calls `getRsETHAmountToMint()`, which divides by the stored (potentially stale) `rsETHPrice`: [5](#0-4) 

When EigenLayer rewards accrue and increase the protocol's TVL, the true rsETH/ETH rate rises above the stored `rsETHPrice`. Any deposit made in this window mints rsETH at the stale lower rate, giving the depositor more rsETH than their contribution warrants. When `rsETHPrice` is subsequently updated (by keeper or by the attacker themselves, provided the price increase is within `pricePercentageLimit`), the attacker's excess rsETH is backed by yield that was earned by existing holders before the deposit.

The `pricePercentageLimit` guard in `_updateRsETHPrice()` only blocks a non-manager from calling `updateRSETHPrice()` if the price jump exceeds the configured threshold: [6](#0-5) 

This does not prevent the deposit attack. The attacker only needs to deposit before the price is updated; the price update can be performed by the keeper afterward. For normal reward accrual (small incremental increases), the attacker can also call `updateRSETHPrice()` themselves to crystallize the gain in a single transaction.

The `minRSETHAmountExpected` slippage parameter in `depositETH()`/`depositAsset()` protects the depositor from receiving too little rsETH, but provides no protection against receiving too much — which is the attack vector here. [7](#0-6) 

## Impact Explanation

**High — Theft of unclaimed yield.**

When rewards accrue and `rsETHPrice` is stale-low, a depositor receives excess rsETH. After the price is updated, the attacker's rsETH is worth more than their deposit, at the direct expense of existing rsETH holders whose proportional share of the TVL is diluted. The profit scales linearly with deposit size and the magnitude of accrued-but-unrecorded rewards. This is a direct, concrete transfer of yield from existing holders to the attacker, matching the "High — Theft of unclaimed yield" impact class.

## Likelihood Explanation

**Medium.**

The attack requires no privileged access. The attacker observes on-chain state (current TVL via `getTotalAssetDeposits` and stored `rsETHPrice`) to identify a stale-price window, then calls `depositETH()` or `depositAsset()`. For incremental reward accrual (the common case), the attacker can also call `updateRSETHPrice()` in the same transaction or immediately after to crystallize the gain. The window exists between every two consecutive keeper price updates and is not enforced on-chain. The attack is repeatable and requires no victim interaction.

## Recommendation

Call `updateRSETHPrice()` (or an equivalent internal price refresh) at the start of `depositETH()` and `depositAsset()` before any price-dependent calculation is performed:

```solidity
function depositAsset(address asset, uint256 depositAmount, uint256 minRSETHAmountExpected, string calldata referralId)
    external nonReentrant whenNotPaused onlySupportedERC20Token(asset)
{
    ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice(); // add this
    uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);
    ...
}
```

Apply the same fix to `depositETH()`. For `initiateWithdrawal()` and `instantWithdrawal()`, a price refresh is also advisable to ensure users receive the correct `expectedAssetAmount` at initiation time.

## Proof of Concept

**Setup:**
- Protocol TVL: 1,000 ETH, rsETH supply: 1,000, stored `rsETHPrice` = 1.000 ETH (last updated).
- EigenLayer rewards accrue: TVL grows to 1,010 ETH. True price = 1.010 ETH. `rsETHPrice` still = 1.000 ETH (stale).

**Attack sequence:**
1. Attacker calls `depositETH{ value: 10 ether }(0, "")`.
2. `getRsETHAmountToMint` computes: `10e18 * 1e18 / 1.000e18 = 10 rsETH`. Fair amount at true price: `10 / 1.010 ≈ 9.901 rsETH`.
3. Attacker receives **10 rsETH** — an excess of ~0.099 rsETH.
4. Attacker (or keeper) calls `updateRSETHPrice()`. New TVL = 1,020 ETH (1,010 + 10 deposit), new supply = 1,010 rsETH. New price ≈ `1020 / 1010 ≈ 1.0099 ETH`.
5. Attacker's 10 rsETH is worth `10 × 1.0099 ≈ 10.099 ETH` — a profit of ~0.099 ETH extracted from existing holders' accrued yield.

**Foundry test plan:**
- Fork mainnet or deploy mock contracts with controlled TVL and `rsETHPrice`.
- Simulate reward accrual by increasing the mock EigenLayer strategy balance without calling `updateRSETHPrice()`.
- Call `depositETH()` as attacker; assert rsETH minted > fair amount.
- Call `updateRSETHPrice()`; assert attacker's rsETH value > deposit value.
- Assert existing holders' rsETH value decreased relative to pre-attack state.

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L252-266)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
```

**File:** contracts/LRTDepositPool.sol (L86-91)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

```

**File:** contracts/LRTDepositPool.sol (L110-116)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

```

**File:** contracts/LRTDepositPool.sol (L516-520)
```text
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L648-670)
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

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
    }
```
