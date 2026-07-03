Audit Report

## Title
Stale `rsETHPrice` in `getRsETHAmountToMint()` Allows New Depositors to Capture Unclaimed Yield — (File: contracts/LRTDepositPool.sol)

## Summary

`LRTOracle` stores `rsETHPrice` as a persistent state variable that is only updated when `updateRSETHPrice()` is explicitly called. `LRTDepositPool.getRsETHAmountToMint()` reads this cached value directly with no prior refresh. When staking rewards have accrued since the last price update, the stored price is lower than the true protocol price, causing any depositor to receive more rsETH than the current protocol value warrants, permanently diluting existing holders' unclaimed yield.

## Finding Description

`rsETHPrice` is a persistent state variable in `LRTOracle`: [1](#0-0) 

It is only updated when `_updateRsETHPrice()` is explicitly invoked, either via the public `updateRSETHPrice()` or the manager-only `updateRSETHPriceAsManager()`: [2](#0-1) 

`getRsETHAmountToMint()` reads `lrtOracle.rsETHPrice()` directly from storage with no prior refresh: [3](#0-2) 

The deposit path `depositAsset()` → `_beforeDeposit()` → `getRsETHAmountToMint()` contains no call to update the price: [4](#0-3) 

The formula `rsethAmountToMint = (amount × assetPrice) / rsETHPrice` uses the stale denominator. When rewards have accrued and the true price is `totalETHInProtocol / rsethSupply > rsETHPrice`, the denominator is smaller than it should be, so the depositor receives excess rsETH. The `minRSETHAmountExpected` slippage guard protects only the depositor, not existing holders.

The `_updateRsETHPrice()` function itself confirms the price is computed on-demand from live TVL data and is not kept in sync with deposits: [5](#0-4) 

## Impact Explanation

**High — Theft of unclaimed yield.** Every unit of accrued-but-unrecorded yield that a new depositor captures at the stale price is yield that existing rsETH holders earned and will never receive. After the deposit, when `updateRSETHPrice()` is eventually called, the new price is computed over a larger rsETH supply (inflated by the excess minted tokens), permanently diluting all prior holders. The magnitude scales with deposit size, price staleness, and total accrued rewards.

## Likelihood Explanation

**Medium.** `rsETHPrice` is not updated atomically with every deposit; it requires a separate transaction. On a live protocol with continuous EigenLayer staking rewards, the window between reward accrual and the next price update exists continuously. An attacker needs only to observe that `lrtOracle.rsETHPrice()` is below `_getTotalEthInProtocol() / rsETH.totalSupply()` (both computable off-chain or via view calls), then deposit before the next `updateRSETHPrice()` call. No privileged access is required; `depositAsset()` is fully public.

## Recommendation

At the start of `depositAsset()` (and `depositETH()`), call `ILRTOracle(lrtOracleAddress).updateRSETHPrice()` before invoking `getRsETHAmountToMint()`. This ensures the price used for minting always reflects the current protocol state. Alternatively, inline the price computation in `getRsETHAmountToMint()` using live TVL data rather than the cached `rsETHPrice` storage variable.

## Proof of Concept

1. Protocol TVL = 1 050 ETH; rsETH supply = 1 000; true price = 1.05 ETH/rsETH. `rsETHPrice` was last stored at 1.00 ETH/rsETH (before the latest reward epoch).
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 105e18, 0, "")`.
3. `getRsETHAmountToMint` computes: `105e18 × 1e18 / 1.00e18 = 105 rsETH`.
4. Correct amount at true price: `105e18 × 1e18 / 1.05e18 ≈ 100 rsETH`.
5. Attacker receives **5 excess rsETH** representing yield earned by the 1 000 existing holders.
6. When `updateRSETHPrice()` is subsequently called, the new price is computed over a supply of 1 105 rsETH instead of 1 100, permanently diluting all prior holders.

**Foundry fork test plan:**
- Fork mainnet (or a testnet deployment).
- Simulate reward accrual by directly increasing the ETH balance of a NodeDelegator (or mocking `getEffectivePodShares`).
- Assert `lrtOracle.rsETHPrice()` has not been updated.
- Call `depositAsset()` as an unprivileged attacker.
- Assert attacker received more rsETH than `(depositAmount × assetPrice) / truePrice` would yield.
- Call `updateRSETHPrice()` and assert the resulting price is lower than it would have been without the attacker's deposit, confirming dilution of existing holders.

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-96)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }

    /// @dev update rsETH price as an manager account
    /// @dev main benefit is to be able to update the price in case of the price going above the threshold
    /// @dev only LRT manager is allowed to call this function
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L231-250)
```text
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
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
