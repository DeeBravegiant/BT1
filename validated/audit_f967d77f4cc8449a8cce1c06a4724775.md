Looking at the external report's vulnerability class — **oracle/rate abuse via instant redemption before a price decrease** — I need to find an analog in LRT-rsETH where an unprivileged user can exploit a stale rate to avoid losses or extract value.

Let me examine the key components: the oracle update mechanism, the deposit/redemption paths, and whether any instant exit exists.

Now I have the complete picture. The critical analog is `LRTWithdrawalManager.instantWithdrawal()` — a user-accessible instant exit path that uses the stored (potentially stale) `rsETHPrice` from `LRTOracle`, which is only updated when the permissionless `updateRSETHPrice()` is called.

---

### Title
Users Can Front-Run rsETH Price Decreases via `instantWithdrawal` to Avoid Losses, Shifting Burden to Honest Stakers - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager.instantWithdrawal()` allows rsETH holders to instantly redeem rsETH for underlying assets using the stored `rsETHPrice` from `LRTOracle`. Because `LRTOracle.updateRSETHPrice()` is a permissionless public function and the stored price is not updated atomically with EigenLayer state changes, an attacker can redeem rsETH at the old (higher) price before a slashing-induced price decrease is reflected, extracting more assets than they are entitled to and shifting the loss onto honest stakers.

### Finding Description

`LRTWithdrawalManager.instantWithdrawal()` computes the asset payout via `getExpectedAssetAmount()`:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [1](#0-0) 

This reads `lrtOracle.rsETHPrice()`, which is a **stored state variable** in `LRTOracle` that is only updated when `updateRSETHPrice()` is explicitly called:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [2](#0-1) 

The `_updateRsETHPrice()` function computes the new price by reading EigenLayer pod shares via `getEffectivePodShares()`. EigenLayer pod balances are only updated on-chain when a validator submits a proof (e.g., via `EigenPod::verifyBalanceUpdates()`). This creates a window where:

1. A slashing event has occurred on the beacon chain, reducing the actual ETH backing rsETH.
2. The EigenLayer contracts have not yet been notified (no proof submitted).
3. `rsETHPrice` in `LRTOracle` is therefore stale — still reflecting the pre-slash value.

During this window, `instantWithdrawal` pays out at the inflated stale price.

The `instantWithdrawal` function is accessible to any rsETH holder when `isInstantWithdrawalEnabled[asset]` is `true` (set by the manager):

```solidity
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
``` [3](#0-2) 

The attacker can perform the following sequence atomically:

1. Monitor the beacon chain for validator penalties/slashings affecting Kelp's EigenLayer pods.
2. Call `instantWithdrawal` to redeem rsETH at the stale (pre-slash) `rsETHPrice`, receiving more assets than the current backing warrants.
3. Call `EigenPod::verifyBalanceUpdates()` (a permissionless EigenLayer function) to notify EigenLayer of the balance drop.
4. Call `LRTOracle.updateRSETHPrice()` (also permissionless) to reflect the decreased price.
5. The rsETH price drops; the attacker has already exited at the old higher price.

### Impact Explanation

The attacker redeems rsETH at a price higher than the post-slash true value, extracting more underlying assets (ETH or LSTs) than they are entitled to. The shortfall is absorbed by the remaining rsETH holders, who now hold rsETH backed by fewer assets than the protocol's accounting reflects. This constitutes **theft of yield / protocol insolvency** at the expense of honest stakers — exactly the impact described in the reference report.

Impact: **High — Theft of unclaimed yield / direct loss to honest stakers.**

### Likelihood Explanation

- Beacon chain slashing events are observable on-chain and via public APIs.
- `EigenPod::verifyBalanceUpdates()` is permissionless, so the attacker can trigger the price drop themselves after exiting.
- `LRTOracle.updateRSETHPrice()` is permissionless, so the attacker controls the timing of the price update.
- `instantWithdrawal` is available to any rsETH holder when enabled for an asset.
- The attack requires no special privileges beyond holding rsETH.

Likelihood: **Medium** — requires monitoring the beacon chain and acting within the window before the price update, but the attacker controls both the exit and the price update timing.

### Recommendation

1. **Call `updateRSETHPrice()` at the start of `instantWithdrawal`** to ensure the payout is computed against the current price, not a stale one.
2. Alternatively, introduce a **withdrawal queue** for instant withdrawals (similar to the standard `initiateWithdrawal` flow) so that the price used for payout is determined at unlock time, not at request time.
3. Consider restricting `updateRSETHPrice()` to authorized callers (e.g., a keeper/manager) to prevent attackers from controlling the timing of price updates.

### Proof of Concept

```
Block N:   Slashing event occurs on beacon chain for a Kelp validator.
           EigenLayer pod balance has NOT yet been updated on-chain.
           LRTOracle.rsETHPrice = 1.05 ETH (stale, pre-slash value).

Block N:   Attacker calls LRTWithdrawalManager.instantWithdrawal(ETH, 100e18, "")
           getExpectedAssetAmount: 100e18 * 1.05e18 / 1e18 = 105 ETH paid out.
           Attacker receives 105 ETH (minus fee).

Block N:   Attacker calls EigenPod.verifyBalanceUpdates() → EigenLayer reflects slash.
           Attacker calls LRTOracle.updateRSETHPrice() → rsETHPrice drops to 1.03 ETH.

Result:    Attacker received 105 ETH for 100 rsETH.
           True value at new price: 100 * 1.03 = 103 ETH.
           Attacker extracted 2 ETH excess from the protocol.
           Remaining rsETH holders absorb the 2 ETH shortfall.
``` [4](#0-3) [5](#0-4) [2](#0-1) [6](#0-5)

### Citations

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

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L214-232)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }

        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }

        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

```
