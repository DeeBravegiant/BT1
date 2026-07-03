### Title
Stale `rsETHPrice` Used in `instantWithdrawal()` Allows Over-Redemption Before Circuit Breaker Triggers - (File: `contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTWithdrawalManager.instantWithdrawal()` computes the asset payout using `lrtOracle.rsETHPrice()`, a cached state variable that is only updated when `updateRSETHPrice()` is explicitly called. If the true rsETH value has dropped (e.g., due to an EigenLayer slashing event) but `rsETHPrice` has not yet been refreshed, any user can burn rsETH and receive more underlying assets than the current fair value. The circuit breaker in `LRTOracle` that would pause the protocol on significant price drops is never invoked within the `instantWithdrawal()` execution path, creating a direct time-of-check vs. time-of-use gap identical in structure to the reported analog.

---

### Finding Description

`LRTOracle.rsETHPrice` is a stored state variable, not a live computation: [1](#0-0) 

It is only updated by explicit calls to `updateRSETHPrice()` or `updateRSETHPriceAsManager()`: [2](#0-1) 

`LRTWithdrawalManager.instantWithdrawal()` calls `getExpectedAssetAmount()` to determine how many assets to disburse: [3](#0-2) 

`getExpectedAssetAmount()` reads `lrtOracle.rsETHPrice()` directly — the stale cached value — while `lrtOracle.getAssetPrice(asset)` reads live from Chainlink: [4](#0-3) 

The full `instantWithdrawal()` execution path is:
1. Read stale `rsETHPrice` → compute `assetAmountUnlocked`
2. Burn rsETH from the caller
3. Redeem `assetAmountUnlocked` from the unstaking vault
4. Transfer assets to the caller [5](#0-4) 

`updateRSETHPrice()` is **never called** anywhere in this path. The circuit breaker in `LRTOracle._updateRsETHPrice()` — which pauses `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` itself when the price drops beyond `pricePercentageLimit` — is therefore never evaluated: [6](#0-5) 

This is the exact TOCTOU ordering flaw described in the analog report: the price used for the financial operation is locked in from a stale read, and the circuit breaker check that would have changed the outcome is evaluated only when `updateRSETHPrice()` is separately called — after the damage is done.

---

### Impact Explanation

**Medium — Temporary freezing of funds / theft of unclaimed yield / contract fails to deliver promised returns.**

More precisely: after a slashing event reduces the true per-share value of rsETH, any user who calls `instantWithdrawal()` before `updateRSETHPrice()` is called receives more underlying assets than the current fair value of their rsETH. The excess is extracted from the unstaking vault at the expense of remaining rsETH holders. The magnitude scales with (a) the size of the slashing event and (b) the staleness window between the event and the next `updateRSETHPrice()` call. Because `instantWithdrawal()` is atomic and irreversible, the over-redeemed assets cannot be recovered once the circuit breaker eventually fires.

---

### Likelihood Explanation

- `instantWithdrawal()` is callable by any unprivileged user when `isInstantWithdrawalEnabled[asset]` is `true`.
- `rsETHPrice` is updated by off-chain keeper bots, not atomically with every user action, so a staleness window always exists.
- EigenLayer slashing events are realistic; several have occurred on mainnet.
- An attacker can monitor on-chain for slashing-related events (e.g., `NodeDelegator` balance drops) and call `instantWithdrawal()` before the keeper bot calls `updateRSETHPrice()`, front-running the circuit breaker.
- No special privileges, leaked keys, or governance capture are required.

---

### Recommendation

Invoke `updateRSETHPrice()` (or an equivalent fresh price computation) at the start of `instantWithdrawal()`, before `getExpectedAssetAmount()` is called. This ensures the circuit breaker is evaluated with the current price in the same transaction, and the payout is based on the refreshed rate — mirroring the fix described in the analog report.

```solidity
function instantWithdrawal(address asset, uint256 rsETHUnstaked, string calldata referralId)
    external nonReentrant whenNotPaused ...
{
    // Refresh price first so circuit breaker fires if needed and payout uses current rate
    ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice();

    uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
    ...
}
```

---

### Proof of Concept

1. Protocol state: `rsETHPrice = 1.05e18` (stored, last updated 2 hours ago). `pricePercentageLimit = 0.05e18` (5%).
2. An EigenLayer slashing event reduces the true rsETH backing to `0.95e18` per token. `updateRSETHPrice()` has not been called yet; the circuit breaker has not fired; the protocol is unpaused.
3. Attacker holds `1000e18` rsETH. Calls `instantWithdrawal(ETH, 1000e18, "")`:
   - `getExpectedAssetAmount` computes: `1000e18 * 1.05e18 / 1e18 = 1050 ETH` (stale price)
   - rsETH is burned from attacker
   - `1050 ETH` is redeemed from the unstaking vault
   - Attacker receives `1050 ETH` (minus fee)
4. Fair value at current price: `1000e18 * 0.95e18 / 1e18 = 950 ETH`.
5. Attacker extracted `~100 ETH` more than fair value, at the expense of remaining rsETH holders.
6. When `updateRSETHPrice()` is eventually called, the circuit breaker detects the `>5%` drop, pauses the protocol — but the over-redemption is already complete and irreversible.

The root cause is identical to the analog: `getExpectedAssetAmount()` performs a view-only read of the stale `rsETHPrice` storage slot, uses that value to lock in the payout, and the circuit breaker that would have changed the outcome is only evaluated in a separate, later transaction. [7](#0-6) [8](#0-7) [2](#0-1) [6](#0-5)

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

**File:** contracts/LRTOracle.sol (L270-281)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
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
