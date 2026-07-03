### Title
`instantWithdrawal()` uses stale oracle price with no delay, enabling front-running of EigenLayer slashing events — (File: contracts/LRTWithdrawalManager.sol)

---

### Summary
The `instantWithdrawal()` function computes the asset payout using the stored `rsETHPrice` oracle value with no delay and no forced price refresh. When an EigenLayer slashing event reduces the protocol's underlying assets, a window exists between when the slashing is applied on-chain and when `updateRSETHPrice()` is called to reflect it. During this window any rsETH holder can call `instantWithdrawal()` at the pre-slash rate, receiving more assets than their rsETH is actually worth and transferring the slashing loss entirely to remaining holders.

---

### Finding Description

`instantWithdrawal()` at line 228 calls `getExpectedAssetAmount()`, which reads `lrtOracle.rsETHPrice()` — a stored state variable that is only updated when `updateRSETHPrice()` is explicitly invoked: [1](#0-0) 

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
```

`getExpectedAssetAmount()` reads the stored price directly: [2](#0-1) 

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

`rsETHPrice` is a state variable updated only on explicit calls to `updateRSETHPrice()`: [3](#0-2) 

There is no mechanism inside `instantWithdrawal()` to force a price refresh before computing the payout, and there is no withdrawal delay of any kind.

By contrast, the regular queued withdrawal path uses `_calculatePayoutAmount()`, which takes the **minimum** of the amount locked at request time and the amount recomputed at unlock time — ensuring a post-slash oracle update reduces the payout proportionally: [4](#0-3) 

```solidity
uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```

`instantWithdrawal()` has no equivalent protection. It uses whatever `rsETHPrice` is stored at call time.

The oracle's downside-protection auto-pause only fires when `updateRSETHPrice()` is actually called: [5](#0-4) 

Until that call happens, the protocol is not paused and `instantWithdrawal()` remains open at the stale price.

---

### Impact Explanation

An rsETH holder who observes an EigenLayer slashing event on-chain before the oracle is updated can call `instantWithdrawal()` and receive assets valued at the pre-slash rsETH price. The slashing loss is then borne entirely by remaining rsETH holders when the oracle is eventually updated and the price drops. This is a direct transfer of value from remaining rsETH holders to the front-runner.

**Impact: High — Theft of unclaimed yield / value from remaining rsETH holders.**

---

### Likelihood Explanation

- EigenLayer slashing is an explicitly anticipated event; the codebase imports `SlashingLib`. [6](#0-5) 
- `updateRSETHPrice()` is not called atomically with slashing; there is always an observable delay.
- The oracle price is a public state variable; any on-chain observer can detect staleness.
- `isInstantWithdrawalEnabled` must be `true` for the target asset, but this is an operational configuration, not a permanent barrier. [7](#0-6) 
- No special privileges are required beyond holding rsETH.

---

### Recommendation

1. In `instantWithdrawal()`, call `updateRSETHPrice()` (or an equivalent fresh price computation) before computing `assetAmountUnlocked`, so the payout always reflects the current state of the protocol.
2. Alternatively, apply the same `_calculatePayoutAmount()` minimum-of-locked-vs-current logic used in the queued withdrawal path to `instantWithdrawal()`.
3. Consider adding at minimum a single-block delay to `instantWithdrawal()` to prevent same-block front-running of oracle updates.

---

### Proof of Concept

1. An EigenLayer slashing event is included in a finalized block; the NodeDelegator's share balance decreases, reducing the true value of rsETH.
2. `rsETHPrice` in `LRTOracle` is still the pre-slash value (stale) because `updateRSETHPrice()` has not yet been called.
3. Attacker (rsETH holder) calls `instantWithdrawal(asset, rsETHAmount, "")` before any call to `updateRSETHPrice()`. [8](#0-7) 
4. `getExpectedAssetAmount()` computes `rsETHAmount * rsETHPrice_stale / assetPrice`, returning a larger asset amount than the post-slash fair value.
5. Attacker receives the inflated asset amount from `LRTUnstakingVault` via `unstakingVault.redeem()`. [9](#0-8) 
6. `updateRSETHPrice()` is eventually called; `rsETHPrice` drops to reflect the slash. [10](#0-9) 
7. Remaining rsETH holders now hold rsETH worth less than before the slash, having absorbed the full slashing loss that the attacker escaped.

### Citations

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

**File:** contracts/LRTWithdrawalManager.sol (L590-593)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**File:** contracts/LRTWithdrawalManager.sol (L833-834)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
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

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTUnstakingVault.sol (L21-21)
```text
import { SlashingLib } from "./external/eigenlayer/libraries/SlashingLib.sol";
```
